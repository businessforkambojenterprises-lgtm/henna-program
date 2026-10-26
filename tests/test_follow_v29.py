"""Live height following on a real hand (user 2026-10-24: "Option B - the nozzle follows the skin live" -> "build this",
after "Remove the live gap pause"): each drawing line's Z moves by the skin's change since the scan, measured live at
that line's X/Y, inside 1 mm down / 2 mm up of the checked line and by at most 0.3 mm per line; X, Y and feed never
change; stale or unseen -> back to the plan; the link checks every changed line again; only watched real-hand runs
follow (never an air run, never a prosthetic job)."""
import os
import sys
import threading
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import follow, hand_watch as hw  # noqa: E402


def skin(z, x=(100, 140), y=(80, 120)):
    xs, ys = np.meshgrid(np.arange(x[0], x[1], 0.5), np.arange(y[0], y[1], 0.5))
    return np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, float(z))])


LINE = "G53 G1 X120.000 Y100.000 Z-24.000 F300"


class FollowerTests(unittest.TestCase):
    def setUp(self):
        self.ref = hw.Reference(skin(-30.0))
        self.now = 100.0
        self.live = (100.0, skin(-30.0))
        self.f = follow.Follower(self.ref, lambda: self.live, -49.0, -0.5, clock=lambda: self.now)

    def zs(self, n):
        return [follow.parse_g1(self.f.adjust(LINE))[2] for _ in range(n)]

    def test_follows_a_raised_hand_smoothly_inside_the_band(self):
        self.live = (100.0, skin(-29.0))                          # the skin rose 1 mm since the scan
        self.assertEqual(self.zs(5), [-23.7, -23.4, -23.1, -23.0, -23.0])
        self.live = (100.0, skin(-26.0))                          # 4 mm: only up to the band (+2)
        self.assertEqual(self.zs(5)[-1], -22.0)
        self.live = (100.0, skin(-34.0))                          # 4 mm lower: at most 1 mm down
        self.assertEqual(self.zs(12)[-1], -25.0)
        self.assertEqual((self.f.stats["min_dz"], self.f.stats["max_dz"]), (-1.0, 2.0))
        x, y, _, feed = follow.parse_g1(self.f.adjust(LINE))
        self.assertEqual((x, y, feed), (120.0, 100.0, "300"))     # X, Y and feed never change

    def test_stale_or_unseen_goes_back_to_the_plan(self):
        self.live = (100.0, skin(-29.0))
        self.zs(4)
        self.now = 101.0                                          # the picture is 1 s old
        self.assertEqual(self.zs(4)[-1], -24.0)
        self.live = (101.0, skin(-29.0, x=(10, 20)))              # nothing seen near the line
        self.now = 101.0
        self.assertEqual(self.zs(2)[-1], -24.0)
        self.assertGreater(self.f.stats["unseen"], 0)
        self.assertEqual(self.f.adjust("G53 G0 X10.000 Y10.000"), "G53 G0 X10.000 Y10.000")   # not a drawing line

    def test_never_below_the_floor_or_at_travel(self):
        f = follow.Follower(self.ref, lambda: (100.0, skin(-34.0)), -24.2, -0.5, clock=lambda: 100.0)
        self.assertEqual(f.adjust(LINE), LINE)                    # -24.3 would be under the floor: as checked

    def test_the_links_own_check(self):
        ok = follow.line_ok
        self.assertTrue(ok(LINE, "G53 G1 X120.000 Y100.000 Z-22.000 F300", -49, -0.5))
        self.assertTrue(ok(LINE, "G53 G1 X120.000 Y100.000 Z-25.000 F300", -49, -0.5))
        for bad in ("G53 G1 X120.000 Y100.000 Z-21.900 F300",        # more than 2 up
                    "G53 G1 X120.000 Y100.000 Z-25.100 F300",        # more than 1 down
                    "G53 G1 X121.000 Y100.000 Z-24.000 F300",        # X changed
                    "G53 G1 X120.000 Y100.000 Z-24.000 F600",        # feed changed
                    "G53 G0 Z-1.000"):
            self.assertFalse(ok(LINE, bad, -49, -0.5), bad)
        self.assertFalse(ok(LINE, "G53 G1 X120.000 Y100.000 Z-24.500 F300", -24.2, -0.5))   # under the floor


class LinkTests(unittest.TestCase):
    def test_send_line_uses_the_follow_function_and_checks_it(self):
        from henna_preview import grbl_program_link_v26 as l26
        from henna_preview.grbl_program_link_v29 import GrblProgramLinkV29
        sent = []
        saved = l26.GrblProgramLinkV26._send_line
        l26.GrblProgramLinkV26._send_line = lambda self, owner, line: sent.append(line)
        try:
            link = object.__new__(GrblProgramLinkV29)
            link._lock = threading.Lock()
            link._changed = threading.Condition(link._lock)
            link._check_token_locked = lambda owner: None
            link._bf_free = link._bf_most = None
            outs = iter(["G53 G1 X120.000 Y100.000 Z-23.000 F300", "G53 G1 X120.000 Y100.000 Z-20.000 F300"])
            link._run = {"follow": (lambda line: next(outs), -49.0, -0.5), "paused": False}
            link._send_line(("t", 1), LINE)                       # inside the band: sent changed
            link._send_line(("t", 1), LINE)                       # 4 mm up: the link sends the checked line
            link._send_line(("t", 1), "G53 G0 Z-0.500")           # not a drawing line: untouched
        finally:
            l26.GrblProgramLinkV26._send_line = saved
        self.assertEqual(sent, ["G53 G1 X120.000 Y100.000 Z-23.000 F300", LINE, "G53 G0 Z-0.500"])


class StudioTests(unittest.TestCase):
    def test_only_watched_real_hand_runs_follow(self):
        import webui_server_v29 as v29
        armed = []
        saved = (v29.v11.job_load, v29._z_home_check, v29.v28.run, v29._air_start, v29._prosthetic_start,
                 getattr(v29.machine, "arm_follow", None), v29.v28.reference, v29.v10.cal, dict(v29._FOLLOWER))
        job = {"id": "j", "subject": "realhand", "steps": {"program": {}, "design": {"standoff_mm": 6.0}}}
        v29.v11.job_load = lambda jid: job
        v29._z_home_check = lambda j: None
        v29.v28.run = lambda p: {"started": "real"}
        v29._air_start = lambda j, p: {"started": "air"}
        v29._prosthetic_start = lambda j, p: {"started": "prosthetic"}
        v29.machine.arm_follow = lambda fn, floor, travel: armed.append(fn is not None)
        v29.v28.reference = lambda j: hw.Reference(skin(-30.0))
        v29.v10.cal = lambda: {"firmware_z_floor": -49.0, "travel_z": -0.5}
        try:
            v29.run({"job": "j"})
            self.assertEqual(armed, [True, False])                # armed for the run, cleared after it
            self.assertIsNotNone(v29.follow_view()["dz_mm"])
            armed.clear()
            v29.run({"job": "j", "air_run": True})
            self.assertEqual(armed, [False])                      # an air run never follows
            armed.clear()
            job["subject"] = "prosthetic"
            v29.run({"job": "j"})
            self.assertEqual(armed, [False])
        finally:
            (v29.v11.job_load, v29._z_home_check, v29.v28.run, v29._air_start, v29._prosthetic_start, arm,
             v29.v28.reference, v29.v10.cal, fol) = saved
            if arm is None:
                del v29.machine.arm_follow
            else:
                v29.machine.arm_follow = arm
            v29._FOLLOWER.clear()
            v29._FOLLOWER.update(fol)


if __name__ == "__main__":
    unittest.main()
