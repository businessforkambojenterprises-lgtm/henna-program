"""Quick re-scan (Studio v29, user 2026-10-22: "it takes too much time"): the outline and the design follow the hand
(rigid fit of the hand points, in pixels and in machine mm), a changed pose / too big a move is refused, and one
request runs scan -> hand -> outline -> design check -> program through the page's own routes, stopping at the first
refusal with that step's name."""
import math
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import follow_hand as fh  # noqa: E402

NAMES = ["wrist", "thumb_cmc", "thumb_mcp", "index_mcp", "middle_mcp", "ring_mcp", "pinky_mcp", "index_tip"]
PTS = np.array([[120, 150], [130, 135], [138, 128], [100, 130], [100, 140], [100, 150], [102, 160], [70, 128]], float)


def moved(points, deg, dx, dy, about=(110, 140)):
    a = math.radians(deg)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    return (np.asarray(points) - about) @ R.T + about + [dx, dy]


def lm(points_mm, points_px=None):
    px = points_px if points_px is not None else points_mm * 2
    return {n: (tuple(p), tuple(m)) for n, p, m in zip(NAMES, px, points_mm)}


class FollowTests(unittest.TestCase):
    def test_rigid_fit_recovers_the_move(self):
        f = fh.fit(PTS, moved(PTS, 7.0, 4.0, -3.0))
        self.assertAlmostEqual(f["angle_deg"], 7.0, places=6)
        self.assertLess(f["rms"], 1e-9)
        np.testing.assert_allclose(fh.apply(PTS, f), moved(PTS, 7.0, 4.0, -3.0), atol=1e-9)
        mirror = PTS * [-1, 1]                                    # never a mirror
        self.assertGreater(fh.fit(PTS, mirror)["rms"], 1.0)

    def test_follow_gives_both_frames_and_refuses_bad_cases(self):
        new_mm = moved(PTS, -5.0, 6.0, 2.0)
        out = fh.follow(lm(PTS), lm(new_mm))
        self.assertAlmostEqual(out["turn_deg"], -5.0, places=1)
        self.assertAlmostEqual(out["machine"]["angle_deg"], -5.0, places=6)
        curled = new_mm.copy()
        curled[-1] += [25, 10]                                    # the index tip moved on its own
        with self.assertRaisesRegex(fh.FollowError, "changed its shape"):
            fh.follow(lm(PTS), lm(curled))
        with self.assertRaisesRegex(fh.FollowError, "too far"):
            fh.follow(lm(PTS), lm(moved(PTS, 0.0, 60.0, 0.0)))
        with self.assertRaisesRegex(fh.FollowError, "too far"):
            fh.follow(lm(PTS), lm(moved(PTS, 40.0, 0.0, 0.0)))
        few = {k: v for k, v in list(lm(PTS).items())[:4]}
        with self.assertRaisesRegex(fh.FollowError, "only 4 hand points"):
            fh.follow(few, lm(new_mm))


class QuickRescanTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.calls = []
        self.job = {"id": "20261022-150000-job", "subject": "realhand", "steps": {
            "scan": {"name": "a.db3", "mpos": [65.7, 80.0, -5.0]}, "hand": {"run": "web:h1"},
            "region": {"polygon_px": [[100, 100], [200, 100], [200, 200], [100, 200]]},
            "design": {"svg": "designs/arabic_finger_bel.svg", "fit_mm": 25.0, "rotation_deg": 10.0,
                       "centre_machine_xy": [110.0, 140.0], "standoff_mm": 6.0},
            "program": {"program": "p1", "draw_feed_mm_min": 300.0}}}
        reports = {"h1": PTS, "h2": moved(PTS, 5.0, 3.0, 1.0)}
        self.saved = (v29.v11.job_load, v29._route, v29._hand_report, v29.machine.status, v29._log, v29.v28._parts,
                      fh.landmarks, v29.v14.job_view)
        v29.v11.job_load = lambda jid: self.job
        v29._hand_report = lambda job: {"run": job["steps"]["hand"]["run"].split(":")[1],
                                        "colour_intrinsics": {"width": 848, "height": 480}}
        fh.landmarks = lambda rep, head, R, t: lm(reports[rep["run"]], reports[rep["run"]])
        v29.machine.status = lambda: {"capture_ready": True, "busy": "", "mpos": [65.7, 80.0, -5.0]}
        v29._log = lambda *a, **k: None
        v29.v28._parts = lambda: {"R": np.eye(2), "t": np.zeros(2)}
        v29.v14.job_view = lambda job: job
        self.verdict = "PASS"

        def route(path):
            def fn(p):
                self.calls.append((path, p))
                if path.endswith("/detect"):
                    self.job["steps"]["hand"] = {"run": "web:h2"}
                if path.endswith("/check_design"):
                    return {"result": {"verdict": self.verdict}}
                return {}
            return fn
        v29._route = route

    def tearDown(self):
        v29 = self.v29
        (v29.v11.job_load, v29._route, v29._hand_report, v29.machine.status, v29._log, v29.v28._parts,
         fh.landmarks, v29.v14.job_view) = self.saved

    def run_it(self):
        return self.v29.quick_rescan({"job": self.job["id"], "confirm_clear": True, "confirm_estop": True})

    def test_the_steps_in_order_and_the_design_follows_the_hand(self):
        out = self.run_it()
        paths = [c[0] for c in self.calls]
        self.assertEqual(paths, ["/api/studio/scan", "/api/studio/detect", "/api/studio/region_accept",
                                 "/api/studio/check_design", "/api/studio/program"])      # already at the scan position
        poly = np.array(self.calls[2][1]["polygon"])
        np.testing.assert_allclose(poly, moved([[100, 100], [200, 100], [200, 200], [100, 200]], 5.0, 3.0, 1.0), atol=0.1)
        d = self.calls[3][1]
        self.assertAlmostEqual(d["rotation_deg"], 15.0, places=1)
        np.testing.assert_allclose(d["centre_machine_xy"], moved([[110.0, 140.0]], 5.0, 3.0, 1.0)[0], atol=0.01)
        self.assertEqual((d["svg"], d["fit_mm"], d["standoff_mm"]), ("designs/arabic_finger_bel.svg", 25.0, 6.0))
        self.assertEqual(self.calls[4][1]["draw_feed_mm_min"], 300.0)
        self.assertAlmostEqual(out["followed"]["turn_deg"], 5.0, places=1)
        self.assertIn("program written", out["done"])

    def test_stops_at_a_refused_design_check(self):
        self.verdict = "REFUSED"
        with self.assertRaisesRegex(ValueError, "stopped at the design check"):
            self.run_it()
        self.assertNotIn("/api/studio/program", [c[0] for c in self.calls])

    def test_moves_to_the_scan_position_only_when_the_way_is_clear(self):
        v29 = self.v29
        states = iter([{"capture_ready": False, "busy": "", "mpos": [150.0, 150.0, -1.0]},
                       {"capture_ready": True, "busy": "", "mpos": [65.7, 80.0, -5.0]}])
        v29.machine.status = lambda: next(states, {"capture_ready": True, "busy": "", "mpos": [65.7, 80.0, -5.0]})
        saved = v29._way_to_scan_position_clear
        v29._way_to_scan_position_clear = lambda job, st: None
        try:
            self.run_it()
        finally:
            v29._way_to_scan_position_clear = saved
        self.assertEqual(self.calls[0][0], "/api/machine/scan_position")
        self.calls.clear()
        v29.machine.status = lambda: {"capture_ready": False, "busy": "", "mpos": [150.0, 150.0, -1.0]}

        def tall(job, st):
            raise ValueError("the straight way to the scan position crosses skin at Z -4.0")
        v29._way_to_scan_position_clear = tall
        try:
            with self.assertRaisesRegex(ValueError, "stopped at the move to the scan position: the straight way"):
                self.run_it()
        finally:
            v29._way_to_scan_position_clear = saved
        self.assertEqual(self.calls, [])                          # nothing moved

    def test_needs_the_steps_and_the_ticks(self):
        with self.assertRaisesRegex(ValueError, "emergency stop"):
            self.v29.quick_rescan({"job": self.job["id"]})
        del self.job["steps"]["design"]
        with self.assertRaisesRegex(ValueError, "Design step once first"):
            self.run_it()
        self.assertIs(self.v29.Handler.POST_API["/api/studio/quick_rescan"][0], self.v29.quick_rescan)


if __name__ == "__main__":
    unittest.main()
