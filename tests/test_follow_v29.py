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
        saved = (v29.v11.job_load, v29._z_home_check, v29.v28.run, v29._prosthetic_start,
                 getattr(v29.machine, "arm_follow", None), v29.v28.reference, v29.v10.cal, dict(v29._FOLLOWER))
        job = {"id": "j", "subject": "realhand", "steps": {"program": {}, "design": {"standoff_mm": 6.0}}}
        v29.v11.job_load = lambda jid: job
        v29._z_home_check = lambda j: None
        v29.v28.run = lambda p: {"started": "real"}
        v29._prosthetic_start = lambda j, p: {"started": "prosthetic"}
        v29.machine.arm_follow = lambda fn, floor, travel: armed.append(fn is not None)
        v29.v28.reference = lambda j: hw.Reference(skin(-30.0))
        v29.v10.cal = lambda: {"firmware_z_floor": -49.0, "travel_z": -0.5}
        try:
            v29.run({"job": "j"})
            self.assertEqual(armed, [True, False])                # armed for the run, cleared after it
            self.assertIsNotNone(v29.follow_view()["dz_mm"])
            armed.clear()
            job["subject"] = "prosthetic"
            v29.run({"job": "j"})
            self.assertEqual(armed, [False])
        finally:
            (v29.v11.job_load, v29._z_home_check, v29.v28.run, v29._prosthetic_start, arm,
             v29.v28.reference, v29.v10.cal, fol) = saved
            if arm is None:
                del v29.machine.arm_follow
            else:
                v29.machine.arm_follow = arm
            v29._FOLLOWER.clear()
            v29._FOLLOWER.update(fol)


class SteadierAndRecordedTests(unittest.TestCase):
    """2026-10-24 ("keep on building camera's live work"): the median of the last 3 pictures, a following record."""

    def test_one_noisy_picture_does_not_move_the_nozzle(self):
        ref = hw.Reference(skin(-30.0))
        frames = [(100.0, skin(-30.0)), (100.1, skin(-27.0)), (100.2, skin(-30.0))]   # one frame 3 mm off
        f = follow.Follower(ref, lambda: frames, -49.0, -0.5, clock=lambda: 100.3)
        self.assertEqual(f.adjust(LINE), LINE)
        frames[2] = (100.2, skin(-28.5))                         # two of three agree: 1.5 mm higher
        frames[0] = (100.0, skin(-28.5))
        self.assertNotEqual(f.adjust(LINE), LINE)

    def test_the_record_and_its_file(self):
        import csv
        import tempfile
        ref = hw.Reference(skin(-30.0))
        f = follow.Follower(ref, lambda: [(100.0, skin(-29.0))], -49.0, -0.5, clock=lambda: 100.0)
        for _ in range(3):
            f.adjust(LINE)
        f.adjust("G53 G0 Z-0.500")
        self.assertEqual(len(f.record), 3)                        # drawing lines only
        path = follow.save_record(f, os.path.join(tempfile.mkdtemp(), "f", "run_follow.csv"))
        with open(path, encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0], ["t_s", "x", "y", "checked_z", "measured_change_mm", "applied_dz_mm"])
        self.assertEqual([r[5] for r in rows[1:]], ["0.3", "0.6", "0.9"])
        self.assertIsNone(follow.save_record(follow.Follower(ref, lambda: None, -49, -0.5), path))


class AirRunEmptyBedTests(unittest.TestCase):
    """2026-10-24: an air run paused 5 times "something is in the design area" with nothing there - the hand lies on
    the jig's raised piece, which under thin fingers is less than 10 mm below the scanned skin. Against the empty bed
    (the daily reference) only something more than EMPTY_MM above it counts, per 5 mm square (2026-10-26)."""

    def test_the_raised_jig_is_empty_a_hand_is_not(self):
        ref = hw.Reference(skin(-30.0))                            # the scanned fingers
        empty = hw.Reference(skin(-36.0))                          # the jig piece under them: 6 mm lower
        live_empty = skin(-36.0)
        self.assertEqual(hw.evaluate(hw.air_reference(ref), live_empty, (120.0, 100.0, -10.0))["verdict"], "moved")
        r = hw.evaluate(hw.air_reference(ref, empty), live_empty, (120.0, 100.0, -10.0))
        self.assertEqual(r["verdict"], "ok")
        self.assertTrue(r["vs_empty_bed"])
        r = hw.evaluate(hw.air_reference(ref, empty), skin(-26.0), (120.0, 100.0, -10.0))   # a hand: 10 mm above
        self.assertEqual(r["verdict"], "moved")
        self.assertIn("more than 6 mm above the empty bed", hw.message(r))

    def test_scattered_depth_noise_is_empty(self):
        """2026-10-26: the saved picture of a false pause - noise blotches 3-4.6 mm high all over an empty bed."""
        rng = np.random.default_rng(4)
        ref, empty = hw.Reference(skin(-30.0)), hw.Reference(skin(-36.0))
        live = skin(-36.0)
        live[:, 2] += rng.normal(0.0, 1.0, len(live))
        spots = rng.random(len(live)) < 0.03
        live[spots, 2] += 4.0                                       # 3 % of the points 4 mm high
        r = hw.evaluate(hw.air_reference(ref, empty), live, (120.0, 100.0, -10.0))
        self.assertEqual(r["verdict"], "ok", r)
        finger = (np.abs(live[:, 0] - 115) < 6) & (np.abs(live[:, 1] - 100) < 6)
        live[finger, 2] = -36.0 + 13.0                              # a fingertip: 12 x 12 mm, 13 mm high
        self.assertEqual(hw.evaluate(hw.air_reference(ref, empty), live, (120.0, 100.0, -10.0))["verdict"], "moved")


class SeenInViewTests(unittest.TestCase):
    """2026-10-26: an air run at Z -37 over a palm-only area paused "the camera cannot see the design area (17 % seen)"
    with nothing wrong - with the head low the camera sees only a patch around the nozzle. Seen is now judged on the part
    of the area inside the camera's view; a camera that sees almost none of the area still counts as unseen."""

    def test_a_patch_in_view_is_seen(self):
        ref = hw.Reference(skin(-30.0))                                    # 40 x 40 mm area
        patch = skin(-30.0, x=(115, 127), y=(95, 107))                     # the camera sees 12 x 12 mm of it
        r = hw.evaluate(ref, patch, (120.0, 100.0, -24.0))
        self.assertEqual(r["verdict"], "ok")
        self.assertLess(r["in_view_share"], 0.2)
        self.assertGreater(r["seen_share"], 0.9)
        tiny = skin(-30.0, x=(100, 104), y=(80, 84))                       # only 1 % of the area in view
        self.assertEqual(hw.evaluate(ref, tiny, (120.0, 100.0, -24.0))["verdict"], "unseen")
        holey = np.vstack([skin(-30.0, x=(100, 140), y=(80, 82)), skin(-30.0, x=(139, 140), y=(80, 120))])
        self.assertEqual(hw.evaluate(ref, holey, (120.0, 100.0, -24.0))["verdict"], "unseen")  # in view, not seen
        sparse = skin(-30.0)[::8]                          # one point every 4 mm along X: few per 1 mm cell
        self.assertEqual(hw.evaluate(ref, sparse, (120.0, 100.0, -24.0))["verdict"], "ok")   # 2026-10-26: "20 % seen"


if __name__ == "__main__":
    unittest.main()
