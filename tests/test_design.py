"""Step 6 checks: paths crossing excluded areas, missing depth and depth jumps break; units."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.design import (MISSING, OK, OUTSIDE, DesignError, centre_depth_mm, check_params,
                                  plan_samples, project)

try:
    import pyrealsense2  # noqa: F401
    HAVE_RS = True
except ImportError:
    HAVE_RS = False

W, H, FX = 200, 160, 100.0
INTR = {"width": W, "height": H, "fx": FX, "fy": FX, "ppx": 100.0, "ppy": 80.0,
        "model": "brown_conrady", "coeffs": [0, 0, 0, 0, 0]}
SCALE = 1e-4  # m per unit


def flat(z_mm=250.0):
    return np.full((H, W), int(z_mm * 10), np.uint16)


def everywhere():
    return np.ones((H, W), bool)


class Params(unittest.TestCase):
    def test_bad_params_rejected(self):
        for args in (("square", 10, 0, 3), ("circle", 0, 0, 3), ("circle", 500, 0, 3),
                     ("circle", 10, float("nan"), 3), ("circle", 10, 0, 0)):
            with self.assertRaises(DesignError):
                check_params(*args)

    def test_size_mm_to_pixels(self):
        # 20 mm wide at 250 mm with fx=100 -> radius 4 px
        _, _, r = plan_samples("circle", 20.0, (100, 80), 0, FX, 250.0)
        self.assertAlmostEqual(r, 4.0)

    def test_centre_depth_needs_measurement_in_region(self):
        raw = flat()
        self.assertAlmostEqual(centre_depth_mm(raw, SCALE, (100, 80), everywhere()), 250.0)
        self.assertIsNone(centre_depth_mm(raw, SCALE, (100, 80), np.zeros((H, W), bool)))


@unittest.skipUnless(HAVE_RS, "pyrealsense2 not installed in this Python")
class Projection(unittest.TestCase):
    def run_case(self, raw, mask, shape="circle", size=60.0, centre=(100, 80), jump=3.0):
        s, closed, _ = plan_samples(shape, size, centre, 0, FX, 250.0)
        return project(raw, SCALE, INTR, mask, s, closed, jump)

    def test_clean_flat_circle_is_one_unbroken_loop(self):
        r = self.run_case(flat(), everywhere())
        self.assertTrue(r["complete_unbroken"])
        self.assertEqual(r["stats"]["segments"], 1)
        self.assertEqual(r["stats"]["path_points_interpolated"], 0)
        # 60 mm circle facing the camera: whole-pixel path reads long, never short, by < 15 %
        self.assertGreaterEqual(r["stats"]["path_length_mm"], np.pi * 60 * 0.97)
        self.assertLessEqual(r["stats"]["path_length_mm"], np.pi * 60 * 1.15)

    def test_crossing_excluded_area_breaks(self):
        mask = everywhere()
        mask[:, 106:] = False  # right side is outside the reviewed region
        r = self.run_case(flat(), mask)
        self.assertFalse(r["complete_unbroken"])
        self.assertGreater(r["stats"]["rejected_outside_region"], 0)
        for seg in r["segments"]:
            self.assertTrue(all(u < 106 for u, _ in seg["pixels_uv"]))

    def test_missing_depth_breaks_and_is_not_bridged(self):
        raw = flat()
        raw[70:90, 84:94] = 0  # hole across the left edge of the circle (radius 12 px)
        r = self.run_case(raw, everywhere())
        self.assertGreater(r["stats"]["rejected_missing_depth"], 0)
        self.assertFalse(r["complete_unbroken"])
        for seg in r["segments"]:
            for u, v in seg["pixels_uv"]:
                self.assertNotEqual(raw[v, u], 0)

    def test_depth_jump_breaks(self):
        raw = flat()
        raw[:, :100] = int(240.0 * 10)  # 10 mm step down the middle (like a finger gap edge)
        r = self.run_case(raw, everywhere(), jump=3.0)
        self.assertGreaterEqual(r["stats"]["depth_jump_breaks"], 2)
        self.assertGreaterEqual(r["stats"]["segments"], 2)
        r2 = self.run_case(raw, everywhere(), jump=20.0)
        self.assertEqual(r2["stats"]["depth_jump_breaks"], 0)

    def test_open_arc_is_not_closed(self):
        r = self.run_case(flat(), everywhere(), shape="arc")
        self.assertEqual(r["stats"]["segments"], 1)
        seg = r["segments"][0]["pixels_uv"]
        self.assertNotEqual(seg[0], seg[-1])

    def test_statuses_are_labelled(self):
        raw = flat()
        raw[80, 112] = 0
        r = self.run_case(raw, everywhere())
        self.assertTrue(set(r["status"]) <= {OK, OUTSIDE, MISSING})


if __name__ == "__main__":
    unittest.main()
