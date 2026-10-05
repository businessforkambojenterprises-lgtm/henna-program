"""Step 13 checks: a plan-view circle stays round in machine X/Y on a sloped surface."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.calibration import predict
from henna_preview.plan_design import PlanError, plan_points, project_plan

try:
    import pyrealsense2  # noqa: F401
    HAVE_RS = True
except ImportError:
    HAVE_RS = False

W, H = 400, 300
INTR = {"width": W, "height": H, "fx": 300.0, "fy": 300.0, "ppx": 200.0, "ppy": 150.0,
        "model": "brown_conrady", "coeffs": [0, 0, 0, 0, 0]}
CAL = {"R": [[0.0, -1.0], [-1.0, 0.0]], "t_mm": [-52.0, 31.0]}
HEAD = {"x": 100.0, "y": 100.0, "z": -20.0}


def sloped_frame(slope=0.5):
    """Plane whose depth rises 0.5 mm per mm across the image (a steep palm-like slope)."""
    u = np.arange(W)
    z = 180.0 + slope * (u - 200) * 180.0 / 300.0
    raw = np.tile(np.round(z * 10).astype(np.uint16), (H, 1))
    return {"depth_raw_aligned": raw, "depth_scale_m_per_unit": 1e-4, "colour_intrinsics": INTR}


def centre_xy():
    return predict(np.array([[-25.0, 10.0]]), [HEAD["x"], HEAD["y"]], np.asarray(CAL["R"]), np.asarray(CAL["t_mm"]))[0]


class Plan(unittest.TestCase):
    def test_plan_is_round(self):
        p, closed = plan_points("circle", 20.0, (50.0, 50.0))
        self.assertTrue(closed)
        np.testing.assert_allclose(np.hypot(p[:, 0] - 50, p[:, 1] - 50), 10.0)

    def test_bad_params(self):
        with self.assertRaises(PlanError):
            plan_points("square", 20, (0, 0))
        with self.assertRaises(PlanError):
            plan_points("circle", 500, (0, 0))


@unittest.skipUnless(HAVE_RS, "pyrealsense2 not installed in this Python")
class Project(unittest.TestCase):
    def test_round_in_machine_xy_on_slope(self):
        c = centre_xy()
        plan, closed = plan_points("circle", 20.0, c)
        r = project_plan(plan, closed, sloped_frame(), np.ones((H, W), bool), HEAD, CAL)
        self.assertEqual(r["stats"]["segments"], 1)
        segs = r["segments_cam_xyz"][0]
        m = predict(segs[:, :2], [HEAD["x"], HEAD["y"]], np.asarray(CAL["R"]), np.asarray(CAL["t_mm"]))
        rad = np.hypot(m[:, 0] - c[0], m[:, 1] - c[1])
        self.assertLess(np.abs(rad - 10.0).max(), 0.6)  # round within the 0.5 mm pixel-match tolerance
        self.assertGreater(np.ptp(segs[:, 2]), 5.0)     # it really is on a slope

    def test_outside_region_breaks(self):
        c = centre_xy()
        plan, closed = plan_points("circle", 20.0, c)
        mask = np.ones((H, W), bool)
        mask[:, :158] = False  # cuts through the circle (pixel u ~141..175)
        r = project_plan(plan, closed, sloped_frame(), mask, HEAD, CAL)
        self.assertGreater(r["stats"]["rejected_outside_region"], 0)


if __name__ == "__main__":
    unittest.main()
