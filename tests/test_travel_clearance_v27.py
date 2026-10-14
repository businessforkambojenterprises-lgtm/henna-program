"""Studio v27: travel Z must clear the hand top + max(standoff, hop) + 3 mm before every check, program and run."""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import travel_clearance as tc  # noqa: E402


class RuleTests(unittest.TestCase):
    def test_numbers(self):
        ok = tc.check(-20.0, -5.0, 4.0, 5.0, bed_z=-46.0)                 # 26 mm hand: 15 mm under travel
        self.assertEqual((ok["needed_travel_z"], ok["gap_mm"], ok["hand_height_above_bed_mm"]), (-12.0, 15.0, 26.0))
        tc.check(-13.0, -5.0, 4.0, 5.0)                                   # exactly 8 mm: allowed
        with self.assertRaisesRegex(tc.ClearanceError, r"too tall.*Z -3\.4 \(about 42\.6 mm above the bed\)"):
            tc.check(-3.4, -5.0, 4.0, 5.0, bed_z=-46.0)                   # the palm-up prosthetic of 2026-10-14
        with self.assertRaises(tc.ClearanceError):
            tc.check(-12.9, -5.0, 4.0, 5.0)                               # 7.9 mm: refused
        with self.assertRaises(tc.ClearanceError):
            tc.check(-13.0, -5.0, 6.0, 5.0)                               # a larger standoff needs more room
        with self.assertRaisesRegex(tc.ClearanceError, "between the camera and the hand.*cable"):
            tc.check(6.5, -5.0, 4.0, 5.0)                                 # the hanging cable of 2026-10-14

    def test_hand_top_from_the_hand_bundle(self):
        tmp = tempfile.mkdtemp()
        try:
            raw = np.full((480, 848), 1830, np.uint16)                    # bed 183 mm from the camera
            raw[200:300, 400:500] = 1500                                  # hand top 150 mm
            lm = np.array([[400 + 10 * (i % 10), 200 + 9 * (i // 2)] for i in range(21)], float)
            np.savez(os.path.join(tmp, "frame_bundle.npz"), depth_raw_aligned=raw, depth_scale_m_per_unit=0.0001,
                     landmarks_px=lm)
            top = tc.hand_top(tmp, -5.0, 141.63, lambda d: d)
            self.assertAlmostEqual(top["hand_top_contact_z"], -5.0 - (150.0 - 141.63), places=2)
        finally:
            shutil.rmtree(tmp, True)


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import webui_server_v10 as v10
        import webui_server_v27 as v27
        cls.v10, cls.v27 = v10, v27

    def setUp(self):
        v27, v10 = self.v27, self.v10
        self.keep = (v27.clearance, v27.v11.job_load, v10.log_row)
        self.logged, self.called = [], []
        v10.log_row = lambda *a, **k: self.logged.append(a)

    def tearDown(self):
        self.v27.clearance, self.v27.v11.job_load, self.v10.log_row = self.keep

    def _job(self, subject):
        self.v27.v11.job_load = lambda jid: {"id": "j1", "subject": subject, "steps": {"scan": {"name": "r.db3"},
                                                                                     "design": {"standoff_mm": 4.0}}}

    def test_every_motion_route_is_guarded(self):
        for p in self.v27.GUARDED:
            self.assertIsNot(self.v27.Handler.POST_API[p][0], self.v27.v26.Handler.POST_API[p][0])

    def test_too_tall_prosthetic_is_refused_before_the_step(self):
        self._job("prosthetic")

        def too_tall(job, standoff):
            raise tc.ClearanceError("The hand is too tall for this machine")
        self.v27.clearance = too_tall
        for p in self.v27.GUARDED:
            fn = self.v27.guarded(p, lambda params: self.called.append(p))
            with self.assertRaisesRegex(tc.ClearanceError, "too tall"):
                fn({"job": "j1", "standoff_mm": 4.0})
        self.assertEqual(self.called, [])
        self.assertEqual(len(self.logged), len(self.v27.GUARDED))

    def test_fitting_hand_and_person_preview_go_through(self):
        self._job("prosthetic")
        self.v27.clearance = lambda job, standoff: {"gap_mm": 15.0}
        self.v27.guarded("/api/studio/program", lambda params: self.called.append("ok"))({"job": "j1"})
        self._job("person")                                              # camera-only preview: no motion
        self.v27.clearance = lambda job, standoff: self.fail("not for a person preview")
        self.v27.guarded("/api/studio/check_design", lambda params: self.called.append("person"))({"job": "j1"})
        self.assertEqual(self.called, ["ok", "person"])


if __name__ == "__main__":
    unittest.main()
