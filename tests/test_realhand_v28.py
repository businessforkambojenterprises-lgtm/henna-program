"""Studio v28: real-hand hover runs - the hand watch (pure maths) and the safeguards around the routes."""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import hand_watch as hw  # noqa: E402

K = 141.5
FX = 435.0


def rays(shape, stride, cx=106, cy=60):                # optical centre in the middle of the strided picture
    h, w = shape
    vs, us = np.mgrid[0:h, 0:w]
    return ((us - cx) * stride / FX * 1.0, (vs - cy) * stride / FX * 1.0)


def skin(depth_mm, shape=(120, 212)):
    return np.full(shape, int(depth_mm * 10), np.uint16)     # 0.0001 m units


def pts(raw, head=(100.0, 100.0, -5.0), mask=None):
    return hw.machine_points(raw, 0.0001, rays(raw.shape, 4), head, np.eye(2), np.zeros(2), K, lambda d: d, mask=mask)


class WatchMathTests(unittest.TestCase):
    def setUp(self):
        self.mask = np.zeros((120, 212), bool)
        self.mask[40:80, 80:130] = True                       # the design region
        self.ref = hw.Reference(pts(skin(170.0), mask=self.mask))

    def test_machine_points_follow_the_calibration_model(self):
        p = pts(skin(170.0))
        self.assertTrue(np.allclose(p[:, 2], -5.0 - (170.0 - K)))                   # skin Z = head Z - (d - k)
        centre = p[np.argmin(np.hypot(p[:, 0] - 100, p[:, 1] - 100))]
        self.assertAlmostEqual(centre[0], 100.0, delta=2.0)                        # camera axis under the head
        raw = skin(170.0)
        raw[:, :30] = int(130.0 * 10)                                              # the nozzle, nearer than the tip
        self.assertEqual(len(pts(raw)), (212 - 30) * 120)                          # never counted as skin

    def test_still_hand_is_ok(self):
        r = hw.evaluate(self.ref, pts(skin(170.0)), (100, 100, -5))
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(r["median_dz_mm"], 0.0)

    def test_hand_lifted_3_mm_is_moved(self):
        r = hw.evaluate(self.ref, pts(skin(167.0)), (100, 100, -5))
        self.assertEqual(r["verdict"], "moved")
        self.assertAlmostEqual(r["median_dz_mm"], 3.0, places=1)
        self.assertIn("moved", hw.message(r))

    def test_part_of_the_area_rising_is_moved(self):
        raw = skin(170.0)
        raw[40:60, 80:130] = int(166.5 * 10)                                       # half the region 3.5 mm higher
        self.assertEqual(hw.evaluate(self.ref, pts(raw), (100, 100, -5))["verdict"], "moved")

    def test_small_noise_is_still_ok(self):
        rng = np.random.default_rng(1)
        raw = (skin(170.0).astype(float) + rng.normal(0, 4, (120, 212))).astype(np.uint16)   # +-0.4 mm
        self.assertEqual(hw.evaluate(self.ref, pts(raw), (100, 100, -5))["verdict"], "ok")

    def test_skin_close_under_the_tip_is_near(self):
        skin_z = -5.0 - (170.0 - K)
        r = hw.evaluate(self.ref, pts(skin(170.0)), (100, 100, skin_z + 1.0))     # tip 1 mm above the skin
        self.assertEqual(r["verdict"], "near")
        self.assertIn("within 1.0 mm", hw.message(r))
        self.assertEqual(hw.evaluate(self.ref, pts(skin(170.0)), (100, 100, skin_z + 6.0))["verdict"], "ok")

    def test_hand_out_of_sight_is_unseen(self):
        raw = skin(170.0)
        raw[30:90, 70:140] = 0                                                     # no depth over the region
        self.assertEqual(hw.evaluate(self.ref, pts(raw), (100, 100, -5))["verdict"], "unseen")
        self.assertEqual(hw.evaluate(self.ref, np.zeros((0, 3)), (100, 100, -5))["verdict"], "unseen")

    def test_reference_needs_skin(self):
        with self.assertRaises(ValueError):
            hw.Reference(np.zeros((3, 3)))


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import webui_server_v10 as v10
        cls.v10 = v10
        cls.cam0 = v10.camera
        import webui_server_v28 as v28
        cls.v28 = v28

    @classmethod
    def tearDownClass(cls):
        cls.v10.camera = cls.cam0

    def setUp(self):
        v28 = self.v28
        self.saved = (v28.v11.job_load, v28.hand_check, v28.v27.clearance, dict(v28._INNER), v28.WATCH.start,
                      self.v10.log_row, v28.reference)
        self.calls = []
        self.v10.log_row = lambda *a, **k: None
        v28.v27.clearance = lambda job, s: self.calls.append(("clearance", s))
        for p in v28._INNER:
            v28._INNER[p] = (lambda path: lambda params: self.calls.append((path, params)) or {"ok": True})(p)
        v28.WATCH.start = lambda job, ref: self.calls.append(("watch", job["id"]))
        v28.reference = lambda job: "ref"
        v28.hand_check = lambda job, samples=3: {"median_dz_mm": 0.1, "share_off": 0.0, "seen_share": 0.9, "verdict": "ok"}

    def tearDown(self):
        v28 = self.v28
        (v28.v11.job_load, v28.hand_check, v28.v27.clearance, inner, v28.WATCH.start, self.v10.log_row,
         v28.reference) = self.saved
        v28._INNER.clear()
        v28._INNER.update(inner)
        with v28.WATCH.lock:
            v28.WATCH.state = {"active": False}

    def job(self, subject, standoff=6.0, feed=300):
        self.v28.v11.job_load = lambda jid: {"id": "j1", "subject": subject,
                                             "steps": {"scan": {"name": "r.db3", "mpos": [65.7, 80, -5]},
                                                       "design": {"standoff_mm": standoff},
                                                       "program": {"draw_feed_mm_min": feed}}}

    def test_camera_only_real_hand_never_gets_a_program_or_run(self):
        self.job("person")
        for f in (self.v28.program, self.v28.run):
            with self.assertRaisesRegex(ValueError, "camera only"):
                f({"job": "j1", "confirm_clear": True, "confirm_estop": True})
        self.assertIn("realhand", self.v28.v11.SUBJECTS)

    def test_real_hand_needs_6_mm(self):
        self.job("realhand")
        with self.assertRaisesRegex(ValueError, "6 mm or more"):
            self.v28.check_design({"job": "j1", "standoff_mm": 4.0})
        self.v28.check_design({"job": "j1", "standoff_mm": 6.0})
        self.assertEqual(self.calls[0], ("clearance", 6.0))
        self.job("realhand", standoff=4.0)
        with self.assertRaisesRegex(ValueError, "6 mm or more"):
            self.v28.program({"job": "j1"})

    def test_real_hand_program_is_written_at_300_with_the_job_type_as_ticks(self):
        self.job("realhand")
        self.v28.program({"job": "j1", "draw_feed_mm_min": 1200})
        path, params = self.calls[-1]
        self.assertEqual((path, params["draw_feed_mm_min"]), ("/api/studio/program", 300))
        self.assertTrue(params["confirm_prosthetic"] and params["confirm_unmoved"])
        self.job("prosthetic")
        self.v28.program({"job": "j1", "draw_feed_mm_min": 600})
        self.assertEqual(self.calls[-1][1]["draw_feed_mm_min"], 600)              # the prosthetic keeps its choice

    def test_run_needs_the_one_tick_and_a_good_hand_check(self):
        self.job("realhand")
        with self.assertRaisesRegex(ValueError, "emergency stop"):
            self.v28.run({"job": "j1", "confirm_clear": True})

        def moved(job, samples=3):
            raise ValueError("Hand check: the hand moved since the scan")
        self.v28.hand_check = moved
        with self.assertRaisesRegex(ValueError, "moved"):
            self.v28.run({"job": "j1", "confirm_clear": True, "confirm_estop": True})
        self.assertFalse(any(c[0] == "/api/machine/run" for c in self.calls))     # nothing reached the machine

    def test_good_real_hand_run_starts_and_is_watched(self):
        self.job("realhand")
        self.v28.run({"job": "j1", "confirm_clear": True, "confirm_estop": True})
        run = [c for c in self.calls if c[0] == "/api/machine/run"][0][1]
        self.assertTrue(run["confirm_prosthetic"] and run["confirm_unmoved"])
        self.assertIn(("watch", "j1"), self.calls)
        self.job("realhand", feed=600)
        with self.assertRaisesRegex(ValueError, "300 mm/min"):
            self.v28.run({"job": "j1", "confirm_clear": True, "confirm_estop": True})

    def test_prosthetic_run_gets_the_hand_check_but_no_watch(self):
        self.job("prosthetic", standoff=4.0, feed=600)
        self.v28.run({"job": "j1", "confirm_clear": True, "confirm_estop": True})
        self.assertFalse(any(c[0] == "watch" for c in self.calls))

    def test_resume_after_a_watch_pause_checks_the_hand_first(self):
        self.job("realhand")
        with self.v28.WATCH.lock:
            self.v28.WATCH.state = {"active": True, "job": "j1", "paused_by_watch": "the hand moved"}

        def still_off(job, samples=3):
            raise ValueError("Hand check: the hand moved since the scan")
        self.v28.hand_check = still_off
        with self.assertRaisesRegex(ValueError, "moved"):
            self.v28.resume({})
        self.assertFalse(any(c[0] == "/api/machine/resume" for c in self.calls))
        self.v28.hand_check = lambda job, samples=3: {"verdict": "ok"}
        self.v28.resume({})
        self.assertTrue(any(c[0] == "/api/machine/resume" for c in self.calls))
        self.assertIsNone(self.v28.WATCH.view()["paused_by_watch"])

    def test_page(self):
        with open(self.v28.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        for s in ('data-subject="realhand"', "machJob", "/api/studio/watch", "RUN_TICKS=['rclear']", 'id="watchline"'):
            self.assertIn(s, page)
        for gone in ('id="rpros"', 'id="rstill"', 'id="pgpros"', 'id="pgstill"'):
            self.assertNotIn(gone, page)
        self.assertIs(self.v28.Handler.POST_API["/api/machine/run"][0], self.v28.run)


class WatchLoopTests(unittest.TestCase):
    """The watch thread with a stand-in machine and camera: it must hold the machine (Pause) for every danger."""

    @classmethod
    def setUpClass(cls):
        import webui_server_v10 as v10
        cls.v10 = v10
        import webui_server_v28 as v28
        cls.v28 = v28

    def setUp(self):
        v28 = self.v28
        self.saved = (v28.machine, self.v10.camera, v28.check_sample, self.v10.log_row, v28.NO_PICTURE_S, v28.UNSEEN_S)
        self.v10.log_row = lambda *a, **k: None
        self.paused = []
        self.run_busy = True
        outer = self

        class FakeMachine:
            def status(self):
                return {"busy": "program" if outer.run_busy else None, "mpos": [100.0, 100.0, -20.0], "age_s": 0.05}

            def pause(self):
                outer.paused.append(time.time())

        class FakeCam:
            def __init__(self):
                self.give = True

            def depth_sample(self):
                return {"t": time.time()} if self.give else None

        v28.machine = FakeMachine()
        self.cam = self.v10.camera = FakeCam()
        v28.NO_PICTURE_S, v28.UNSEEN_S = 0.3, 0.3

    def tearDown(self):
        v28 = self.v28
        v28.WATCH.stop()
        (v28.machine, self.v10.camera, v28.check_sample, self.v10.log_row, v28.NO_PICTURE_S, v28.UNSEEN_S) = self.saved

    def run_watch(self, verdict, seconds=1.0):
        self.v28.check_sample = lambda ref, s, head: {"verdict": verdict, "median_dz_mm": 3.0, "share_off": 0.9,
                                                      "seen_share": 0.1, "near_gap_mm": 0.8, "points": 100}
        job = {"id": "j1", "steps": {"scan": {"name": "r.db3"}}}
        self.v28.WATCH.start(job, "ref")
        time.sleep(seconds)
        return self.v28.WATCH.view()

    def test_moved_pauses_at_once(self):
        w = self.run_watch("moved", 0.5)
        self.assertEqual(len(self.paused), 1)
        self.assertIn("moved", w["paused_by_watch"])

    def test_near_pauses(self):
        self.assertIn("within", self.run_watch("near", 0.5)["paused_by_watch"])

    def test_unseen_pauses_after_a_while(self):
        self.assertIn("cannot see", self.run_watch("unseen", 1.0)["paused_by_watch"])

    def test_no_camera_picture_pauses(self):
        self.cam.give = False
        self.assertIn("no camera picture", self.run_watch("ok", 1.0)["paused_by_watch"])

    def test_still_hand_never_pauses_and_the_watch_ends_with_the_run(self):
        w = self.run_watch("ok", 0.6)
        self.assertEqual(self.paused, [])
        self.assertTrue(w["active"] and w["checks"] > 0)
        self.run_busy = False
        time.sleep(0.4)
        self.assertFalse(self.v28.WATCH.view()["active"])


if __name__ == "__main__":
    unittest.main()
