"""Step 9 checks: verification uses the saved calibration without re-fitting, and catches a wrong one."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.calibration import CalibrationError
from henna_preview.verify import check_compatible, verify

try:
    import pyrealsense2  # noqa: F401
    HAVE_RS = True
except ImportError:
    HAVE_RS = False

W, H, FX = 400, 300, 200.0
INTR = {"width": W, "height": H, "fx": FX, "fy": FX, "ppx": 200.0, "ppy": 150.0,
        "model": "brown_conrady", "coeffs": [0, 0, 0, 0, 0]}
Z_MM = 200.0
R_TRUE = np.array([[0.0, -1.0], [-1.0, 0.0]])  # rotated and mirrored, like the real camera
T_TRUE = np.array([-48.0, 19.0])


def calib(R=R_TRUE, t=T_TRUE, tool="pencil", rec="pose1.db3"):
    return {"schema": "camera_tool_xy_calibration/1", "tool": tool,
            "inputs": {"head_pose_at_capture": {"recording": rec, "x": 0, "y": 0, "z": -40}},
            "result": {"R": np.asarray(R).tolist(), "t_mm": np.asarray(t).tolist(), "rms_mm": 1.0}}


def scene(head):
    """A flat target at 200 mm; dots placed at known pixels; machine X/Y from the true model."""
    raw = np.full((H, W), int(Z_MM * 10), np.uint16)
    pix = {"A": (200, 150), "B": (300, 150), "C": (200, 250), "D": (120, 80)}
    tg = {}
    for k, (u, v) in pix.items():
        cam = np.array([(u - 200) / FX * Z_MM, (v - 150) / FX * Z_MM])
        tg[k] = (np.asarray(head) + R_TRUE @ cam + T_TRUE).tolist()
    frame = {"depth_raw_aligned": raw, "depth_scale_m_per_unit": 1e-4, "colour_intrinsics": INTR}
    targets = {"tool": "pencil", "head_pose_at_capture": {"recording": "pose2.db3", "x": head[0], "y": head[1], "z": -40},
               "targets": tg}
    return frame, targets, {k: list(v) for k, v in pix.items()}


@unittest.skipUnless(HAVE_RS, "pyrealsense2 not installed in this Python")
class Verify(unittest.TestCase):
    def test_correct_calibration_predicts_new_pose(self):
        frame, targets, clicks = scene([165.8, 112.6])
        r = verify(frame, targets, clicks, calib())
        self.assertEqual(r["dots_used"], 4)
        self.assertLess(r["max_error_mm"], 1e-6)

    def test_wrong_offset_shows_up(self):
        frame, targets, clicks = scene([165.8, 112.6])
        r = verify(frame, targets, clicks, calib(t=T_TRUE + [5.0, 0.0]))
        self.assertAlmostEqual(r["rms_mm"], 5.0, places=6)
        np.testing.assert_allclose(r["mean_error_xy_mm"], [5.0, 0.0], atol=1e-6)

    def test_wrong_rotation_shows_up(self):
        frame, targets, clicks = scene([165.8, 112.6])
        r = verify(frame, targets, clicks, calib(R=np.eye(2)))
        self.assertGreater(r["max_error_mm"], 20.0)

    def test_missing_depth_dot_rejected(self):
        frame, targets, clicks = scene([165.8, 112.6])
        frame["depth_raw_aligned"][75:86, 115:126] = 0  # no depth around dot D at (120, 80)
        r = verify(frame, targets, clicks, calib())
        self.assertEqual(r["dots_used"], 3)
        self.assertTrue(any(d["status"].startswith("rejected") for d in r["dots"]))


class Compatibility(unittest.TestCase):
    def test_tool_mismatch_refused(self):
        _, targets, _ = scene([0, 0])
        with self.assertRaises(CalibrationError):
            check_compatible(calib(tool="nozzle"), targets)

    def test_same_recording_refused(self):
        _, targets, _ = scene([0, 0])
        with self.assertRaises(CalibrationError):
            check_compatible(calib(rec="pose2.db3"), targets)

    def test_not_a_calibration_refused(self):
        _, targets, _ = scene([0, 0])
        with self.assertRaises(CalibrationError):
            check_compatible({"schema": "x"}, targets)


if __name__ == "__main__":
    unittest.main()
