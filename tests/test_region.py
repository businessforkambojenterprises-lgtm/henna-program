"""Step 5 checks: outline geometry, missing-depth exclusion, units, acceptance rules."""
import json
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.region import (RegionError, check_polygon, convex_hull, evaluate_region, polygon_mask,
                                  region_record, require_accepted, suggest_region)

try:
    import pyrealsense2  # noqa: F401
    HAVE_RS = True
except ImportError:
    HAVE_RS = False

INTR = {"width": 20, "height": 10, "fx": 100.0, "fy": 100.0, "ppx": 10.0, "ppy": 5.0,
        "model": "brown_conrady", "coeffs": [0, 0, 0, 0, 0]}


def bundle(raw):
    return {"depth_raw_aligned": raw, "depth_scale_m_per_unit": 1e-4, "colour_intrinsics": INTR,
            "source": {"recording_sha256": "abc", "frame_index": 7, "recording": "x", "hand_run": "y"}}


class Geometry(unittest.TestCase):
    def test_hull_drops_inner_point(self):
        hull = convex_hull([[0, 0], [10, 0], [10, 10], [0, 10], [5, 5]])
        self.assertEqual(len(hull), 4)

    def test_margin_pulls_toward_centre(self):
        lm = np.zeros((21, 2))
        for i, p in zip((0, 1, 5, 9, 13, 17), [[0, 0], [100, 0], [100, 100], [0, 100], [50, 0], [0, 50]]):
            lm[i] = p
        poly = np.array(suggest_region(lm, 0.5))
        self.assertAlmostEqual(poly[:, 0].min(), 25.0)
        self.assertAlmostEqual(poly[:, 0].max(), 75.0)
        self.assertEqual(np.array(suggest_region(lm, 0.0))[:, 0].max(), 100.0)

    def test_bad_margin_and_landmarks_rejected(self):
        with self.assertRaises(RegionError):
            suggest_region(np.zeros((21, 2)), 0.99)
        with self.assertRaises(RegionError):
            suggest_region(np.zeros((5, 2)), 0.2)

    def test_mask_inside_outside(self):
        m = polygon_mask([[2, 2], [8, 2], [8, 6], [2, 6]], 10, 8)
        self.assertTrue(m[4, 5])
        self.assertFalse(m[0, 0])
        self.assertFalse(m[7, 9])

    def test_invalid_outlines_rejected(self):
        for poly in ([[0, 0], [5, 5]], [[0, 0], [5, 0], [float("nan"), 3]],
                     [[0, 0], [50, 0], [5, 5]], [[0, 0], [5, 5], [9, 9]]):
            with self.assertRaises(RegionError):
                check_polygon(poly, 10, 10)


@unittest.skipUnless(HAVE_RS, "pyrealsense2 not installed in this Python")
class MeasuredPoints(unittest.TestCase):
    def test_missing_depth_excluded_not_filled(self):
        raw = np.full((10, 20), 2500, np.uint16)  # 250 mm
        raw[4:6, 9:11] = 0
        res = evaluate_region(bundle(raw), [[5, 1], [15, 1], [15, 9], [5, 9]])
        s = res["stats"]
        self.assertEqual(s["missing_px_excluded"], 4)
        self.assertEqual(s["measured_px"] + s["missing_px_excluded"], s["inside_px"])
        self.assertEqual(len(res["points_mm"]), s["measured_px"])
        self.assertTrue(np.isfinite(res["points_mm"]).all())
        self.assertEqual(s["depth_mm_range"], [250.0, 250.0])

    def test_units_mm_at_known_pixels(self):
        raw = np.full((10, 20), 2500, np.uint16)
        res = evaluate_region(bundle(raw), [[9.5, 4.5], [12.5, 4.5], [12.5, 5.5], [9.5, 5.5]])
        pts = {tuple(uv): p for uv, p in zip(res["pixels_uv"].tolist(), res["points_mm"])}
        np.testing.assert_allclose(pts[(10, 5)], [0.0, 0.0, 250.0], atol=1e-6)   # principal point
        np.testing.assert_allclose(pts[(12, 5)], [5.0, 0.0, 250.0], atol=1e-6)   # 2 px * 250/100


class Acceptance(unittest.TestCase):
    def rec(self, accepted):
        b = bundle(np.ones((10, 20), np.uint16))
        return region_record(b, [[1, 1], [5, 1], [5, 5]], 0.2, {}, accepted, edited=False)

    def test_unaccepted_refused(self):
        with self.assertRaises(RegionError):
            require_accepted(self.rec(False), "abc", 7)

    def test_other_frame_or_recording_refused(self):
        with self.assertRaises(RegionError):
            require_accepted(self.rec(True), "abc", 8)
        with self.assertRaises(RegionError):
            require_accepted(self.rec(True), "zzz", 7)

    def test_accepted_matching_ok_and_motion_false(self):
        r = require_accepted(json.loads(json.dumps(self.rec(True))), "abc", 7)
        self.assertIs(r["motion_ready"], False)


if __name__ == "__main__":
    unittest.main()
