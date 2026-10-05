"""Step 8 checks: the X/Y calibration recovers known transforms and rejects bad inputs."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.calibration import CalibrationError, dot_camera_mm, predict, solve

try:
    import pyrealsense2  # noqa: F401
    HAVE_RS = True
except ImportError:
    HAVE_RS = False

CAM = np.array([[0, 0], [70, 5], [-3, 72], [-71, -2], [4, -74], [50, 50], [-50, 52], [-49, -51], [52, -50]], float)


def make(theta_deg, t, head, mirror=False, noise=0.0, seed=0):
    th = np.radians(theta_deg)
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    if mirror:
        R = R @ np.diag([1, -1])
    rng = np.random.default_rng(seed)
    m = np.asarray(head) + CAM @ R.T + np.asarray(t) + rng.normal(0, noise, CAM.shape)
    return m, R


class Solve(unittest.TestCase):
    def test_recovers_rotation_and_offset(self):
        m, R = make(90, [-50.0, 55.0], [145.8, 42.6])
        r = solve(CAM, m, [145.8, 42.6])
        np.testing.assert_allclose(r["t"], [-50.0, 55.0], atol=1e-9)
        self.assertAlmostEqual(r["rotation_deg"], 90.0, places=6)
        self.assertFalse(r["mirrored"])
        self.assertLess(r["rms_mm"], 1e-9)
        self.assertAlmostEqual(r["scale_check"], 1.0, places=9)

    def test_detects_mirrored_axes(self):
        m, _ = make(30, [10.0, -20.0], [0, 0], mirror=True)
        r = solve(CAM, m, [0, 0])
        self.assertTrue(r["mirrored"])
        self.assertLess(r["rms_mm"], 1e-9)
        np.testing.assert_allclose(predict(CAM, [0, 0], r["R"], r["t"]), m, atol=1e-9)

    def test_noise_shows_in_residuals_not_hidden(self):
        m, _ = make(-15, [5.0, 5.0], [100, 100], noise=1.0, seed=3)
        r = solve(CAM, m, [100, 100])
        self.assertGreater(r["rms_mm"], 0.3)
        self.assertLess(r["rms_mm"], 2.0)

    def test_wrong_scale_is_visible(self):
        m, _ = make(0, [0, 0], [0, 0])
        r = solve(CAM * 0.9, m, [0, 0])  # camera units off by 10 %
        self.assertAlmostEqual(r["scale_check"], 1 / 0.9, places=6)
        self.assertGreater(r["max_residual_mm"], 3.0)

    def test_bad_inputs_rejected(self):
        with self.assertRaises(CalibrationError):
            solve(CAM[:2], CAM[:2], [0, 0])
        with self.assertRaises(CalibrationError):
            solve(CAM, CAM[:5], [0, 0])
        bad = CAM.copy()
        bad[0, 0] = np.nan
        with self.assertRaises(CalibrationError):
            solve(bad, CAM, [0, 0])


@unittest.skipUnless(HAVE_RS, "pyrealsense2 not installed in this Python")
class DotDepth(unittest.TestCase):
    INTR = {"width": 20, "height": 10, "fx": 100.0, "fy": 100.0, "ppx": 10.0, "ppy": 5.0,
            "model": "brown_conrady", "coeffs": [0, 0, 0, 0, 0]}

    def test_dot_without_measured_depth_rejected(self):
        raw = np.zeros((10, 20), np.uint16)
        p, n = dot_camera_mm(raw, 1e-4, self.INTR, (10, 5))
        self.assertIsNone(p)
        self.assertEqual(n, 0)

    def test_dot_uses_only_measured_neighbours(self):
        raw = np.zeros((10, 20), np.uint16)
        raw[5, 12] = 2000  # 200 mm, single measured pixel near the click
        p, n = dot_camera_mm(raw, 1e-4, self.INTR, (10, 5))
        self.assertEqual(n, 1)
        np.testing.assert_allclose(p, [0.0, 0.0, 200.0], atol=1e-6)

    def test_outside_image_rejected(self):
        with self.assertRaises(CalibrationError):
            dot_camera_mm(np.ones((10, 20), np.uint16), 1e-4, self.INTR, (50, 5))


if __name__ == "__main__":
    unittest.main()
