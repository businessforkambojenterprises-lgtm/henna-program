"""Run with:  python -m unittest discover -s tests -v   (standard library only)"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import MOTION_READY
from henna_preview.depth_surface import (
    MISSING, NOISY, UNSTABLE, VALID, MissingCalibrationError,
    classify_depth, deproject, raw_to_mm, require_intrinsics,
)


def make_intr(w=4, h=3, fx=100.0, fy=100.0, ppx=1.0, ppy=1.0):
    return require_intrinsics(w, h, fx, fy, ppx, ppy, "test", "unit test")


class Units(unittest.TestCase):
    def test_raw_to_mm_uses_depth_scale(self):
        # 2500 units * 0.0001 m/unit = 0.25 m = 250 mm
        self.assertAlmostEqual(float(raw_to_mm(2500, 1e-4)), 250.0)

    def test_principal_point_lies_on_optical_axis(self):
        d = np.full((3, 4), 250.0)
        xyz = deproject(d, make_intr())
        np.testing.assert_allclose(xyz[1, 1], [0.0, 0.0, 250.0])

    def test_offset_pixel_scales_with_depth(self):
        # one pixel right of ppx at fx=100, z=200 mm -> x = 2 mm
        d = np.full((3, 4), 200.0)
        xyz = deproject(d, make_intr())
        np.testing.assert_allclose(xyz[1, 2], [2.0, 0.0, 200.0])


class InvalidDepth(unittest.TestCase):
    def test_nan_and_zero_stay_missing_and_are_not_filled(self):
        d = np.full((3, 4), 250.0)
        d[0, 0] = np.nan
        d[2, 3] = 0.0
        xyz = deproject(d, make_intr())
        self.assertTrue(np.isnan(xyz[0, 0]).all())
        self.assertTrue(np.isnan(xyz[2, 3]).all())
        self.assertEqual(int(np.isfinite(xyz[..., 2]).sum()), 10)
        st = classify_depth(d)
        self.assertEqual(st[0, 0], MISSING)
        self.assertEqual(st[2, 3], MISSING)
        self.assertEqual(st[1, 1], VALID)

    def test_unstable_and_noisy_flags(self):
        d = np.full((1, 3), 250.0)
        vf = np.array([[1.0, 0.3, 1.0]])
        mad = np.array([[0.2, 0.2, 5.0]])
        st = classify_depth(d, vf, mad, min_valid_frac=0.6, max_mad_mm=1.0)
        self.assertEqual(st.tolist(), [[VALID, UNSTABLE, NOISY]])


class MissingCalibration(unittest.TestCase):
    def test_missing_value_rejected(self):
        with self.assertRaises(MissingCalibrationError):
            require_intrinsics(640, 480, None, 386.9, 318.8, 234.4, "", "")

    def test_nan_and_zero_rejected(self):
        with self.assertRaises(MissingCalibrationError):
            require_intrinsics(640, 480, float("nan"), 386.9, 318.8, 234.4, "", "")
        with self.assertRaises(MissingCalibrationError):
            require_intrinsics(640, 480, 0.0, 386.9, 318.8, 234.4, "", "")

    def test_missing_depth_scale_rejected(self):
        with self.assertRaises(MissingCalibrationError):
            raw_to_mm(2500, None)

    def test_shape_mismatch_rejected(self):
        with self.assertRaises(MissingCalibrationError):
            deproject(np.ones((5, 5)), make_intr())


class Safety(unittest.TestCase):
    def test_motion_ready_is_false(self):
        self.assertIs(MOTION_READY, False)


if __name__ == "__main__":
    unittest.main()
