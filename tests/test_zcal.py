"""Step 11 checks: Z calibration recovers a known camera-to-tip height and Z scale."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.calibration import CalibrationError
from henna_preview.zcal import camera_xy_for_tool, solve_k, z_contact_for_depth

try:
    import pyrealsense2  # noqa: F401
    HAVE_RS = True
except ImportError:
    HAVE_RS = False

W, H = 400, 300
INTR = {"width": W, "height": H, "fx": 300.0, "fy": 300.0, "ppx": 200.0, "ppy": 150.0,
        "model": "brown_conrady", "coeffs": [0, 0, 0, 0, 0]}
R = np.array([[0.0, -1.0], [-1.0, 0.0]])
T = np.array([-52.0, 31.0])
K_TRUE = 140.0
TOP_Z = -60.0  # machine Z at which the tip touches the block top


def frame_at(head_z):
    """Flat block top seen from a head at head_z: depth = head_z - TOP_Z + K."""
    d = head_z - TOP_Z + K_TRUE
    raw = np.full((H, W), int(round(d * 10)), np.uint16)
    return {"depth_raw_aligned": raw, "depth_scale_m_per_unit": 1e-4, "colour_intrinsics": INTR}


HEAD_A = {"x": 145.8, "y": 42.6, "z": -40.0}
HEAD_B = {"x": 145.8, "y": 42.6, "z": -20.0}
CAL = {"R": R.tolist(), "t_mm": T.tolist()}


class Inverse(unittest.TestCase):
    def test_inverse_of_xy_calibration(self):
        cam = np.array([12.0, -7.0])
        tool = np.array([HEAD_A["x"], HEAD_A["y"]]) + R @ cam + T
        np.testing.assert_allclose(camera_xy_for_tool(tool, [HEAD_A["x"], HEAD_A["y"]], R, T), cam, atol=1e-9)

    def test_z_contact_formula(self):
        self.assertAlmostEqual(z_contact_for_depth(160.0, -40.0, 140.0), -60.0)


@unittest.skipUnless(HAVE_RS, "pyrealsense2 not installed in this Python")
class Solve(unittest.TestCase):
    def touches(self):
        out = []
        for i, cam in enumerate([[0, 0], [10, 5], [-8, 12]]):
            xy = np.array([HEAD_A["x"], HEAD_A["y"]]) + R @ np.array(cam, float) + T
            out.append({"label": f"T{i+1}", "x": float(xy[0]), "y": float(xy[1]), "z_contact": TOP_Z})
        return out

    def test_recovers_k_and_scale(self):
        r = solve_k(self.touches(), frame_at(HEAD_A["z"]), HEAD_A, CAL, frame_at(HEAD_B["z"]), HEAD_B)
        self.assertAlmostEqual(r["k_mm"], K_TRUE, delta=0.11)
        self.assertLess(r["k_spread_mm"], 0.2)
        self.assertAlmostEqual(r["z_scale_check"], 1.0, delta=0.01)

    def test_bad_touch_shows_in_residual(self):
        tc = self.touches()
        tc[2]["z_contact"] = TOP_Z + 2.0  # a touch recorded 2 mm too high
        r = solve_k(tc, frame_at(HEAD_A["z"]), HEAD_A, CAL)
        self.assertGreater(r["k_spread_mm"], 1.9)

    def test_missing_depth_rejected(self):
        f = frame_at(HEAD_A["z"])
        f["depth_raw_aligned"][:] = 0
        with self.assertRaises(CalibrationError):
            solve_k(self.touches(), f, HEAD_A, CAL)


if __name__ == "__main__":
    unittest.main()
