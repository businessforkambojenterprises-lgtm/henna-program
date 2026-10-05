"""Step 14 checks: depth-scale fit recovers a known camera scale and the correction undoes it."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.calibration import CalibrationError
from henna_preview.depth_scale import correct_depth, fit_scale, measure, spot_grid

try:
    import pyrealsense2  # noqa: F401
    HAVE_RS = True
except ImportError:
    HAVE_RS = False

W, H = 320, 240
INTR = {"width": W, "height": H, "fx": 250.0, "fy": 250.0, "ppx": 160.0, "ppy": 120.0,
        "model": "brown_conrady", "coeffs": [0, 0, 0, 0, 0]}
ZS = [-20.0, -40.0, -50.0, -60.0]
G_TRUE = 0.978


def scene(head_z):
    """Bed at true depth 230 and a block 38 mm higher on the left half; camera reads depth*G around 200."""
    true = np.full((H, W), 230.0) + (head_z + 40.0)
    true[:, : W // 2] -= 38.0
    cam = 200.0 + (true - 200.0) * G_TRUE
    return {"depth_raw_aligned": np.round(cam * 10).astype(np.uint16), "depth_scale_m_per_unit": 1e-4,
            "colour_intrinsics": INTR}


class Fit(unittest.TestCase):
    def test_fit_recovers_slope_from_numbers(self):
        x = np.array(ZS)
        D = np.array([[150 + 0.978 * z for z in x], [190 + 0.978 * z for z in x], [170 + 0.978 * z for z in x]])
        r = fit_scale(D, ZS, -40.0)
        self.assertAlmostEqual(r["g"], 0.978, places=9)
        self.assertLess(r["rms_mm"], 1e-9)

    def test_correction_undoes_scale(self):
        d_ref = 164.0
        true = np.array([150.0, 164.0, 200.0])
        cam = d_ref + (true - d_ref) * 0.978
        np.testing.assert_allclose(correct_depth(cam, 0.978, d_ref), true)

    def test_too_little_data_refused(self):
        with self.assertRaises(CalibrationError):
            fit_scale([[1, 2, 3], [1, 2, 3]], [-20, -40, -60], -40)


@unittest.skipUnless(HAVE_RS, "pyrealsense2 not installed in this Python")
class Measured(unittest.TestCase):
    def test_end_to_end_on_synthetic_frames(self):
        frames = [scene(z) for z in ZS]
        spots = spot_grid(frames[1], step_px=60, margin_px=30)
        D, kept = measure(frames, ZS, spots)
        self.assertGreaterEqual(len(kept), 6)
        r = fit_scale(D, ZS, -40.0)
        self.assertAlmostEqual(r["g"], G_TRUE, delta=0.01)


if __name__ == "__main__":
    unittest.main()
