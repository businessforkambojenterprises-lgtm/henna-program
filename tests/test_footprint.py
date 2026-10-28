"""The nozzle stays above the high skin round each point, not only the skin under it (user 2026-10-27: curled
fingertips beside the tilted nozzle touched it at 3 mm - "build 3"): a ridge within FOOTPRINT_MM lifts the point, a
single noisy pixel or the head itself does not, the point's status is unchanged, and Studio's step runner uses it."""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import smooth_path as sp  # noqa: E402

INTR = {"width": 848, "height": 480, "fx": 435.4, "fy": 434.7, "ppx": 424.0, "ppy": 240.0, "model": "none",
        "coeffs": [0, 0, 0, 0, 0]}
HEAD = {"x": 0.0, "y": 0.0, "z": -0.5}
XY = {"R": [[1.0, 0.0], [0.0, 1.0]], "t_mm": [0.0, 0.0]}           # machine X/Y = camera X/Y here


def frame(depth_mm):
    return {"depth_raw_aligned": np.round(depth_mm * 10).astype(np.uint16), "depth_scale_m_per_unit": 0.0001,
            "colour_intrinsics": INTR}


def px(x_mm, z=200.0):
    return int(round(INTR["fx"] * x_mm / z + INTR["ppx"]))


class FootprintTests(unittest.TestCase):
    def setUp(self):
        self.depth = np.full((480, 848), 200.0)
        self.mask = np.ones((480, 848), bool)

    def measure(self, x=0.0):
        return sp.measure_with_footprint(np.array([[x, 0.0]]), frame(self.depth), self.mask, HEAD, XY)[0]

    def test_a_ridge_beside_the_point_lifts_it(self):
        self.depth[:, px(1.5):px(3.0)] = 197.0                      # skin 3 mm higher, 1.5-3 mm to the side
        status, p = self.measure()
        self.assertEqual(status, "ok")
        self.assertAlmostEqual(p[2], 197.0, delta=0.05)
        plain = sp.measure_under_plan(np.array([[0.0, 0.0]]), frame(self.depth), self.mask, HEAD, XY)[0]
        self.assertAlmostEqual(plain[1][2], 200.0, delta=0.05)      # the old reading: only the skin under it

    def test_one_noisy_pixel_and_the_head_do_not(self):
        self.depth[240, px(1.0)] = 195.0                            # a single pixel 5 mm high
        self.depth[200:280, px(-2.0):px(-1.0)] = 150.0              # the nozzle / head: 50 mm nearer
        self.assertAlmostEqual(self.measure()[1][2], 200.0, delta=0.05)

    def test_status_is_the_points_own(self):
        self.mask[:, :] = False
        self.assertEqual(self.measure()[0], "outside_region")

    def test_the_studio_runner_uses_it(self):
        import step21_design_preview as s21
        import step_path_clearance as spc
        saved = s21.measure_under_plan
        try:
            spc.use_footprint()
            self.assertIs(s21.measure_under_plan, sp.measure_with_footprint)
        finally:
            s21.measure_under_plan = saved


if __name__ == "__main__":
    unittest.main()
