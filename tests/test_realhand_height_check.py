"""Real-hand height check (camera only): the pure helpers, no recording or hardware."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import realhand_height_check as rh  # noqa: E402


class RealHandHeightTests(unittest.TestCase):
    def test_contact_z_is_studios_mapping(self):
        # head at Z -40, k 141.47: a surface 141.47 mm from the camera is at the head's Z; 10 mm closer is 10 mm higher
        ident = lambda d: d  # noqa: E731
        self.assertAlmostEqual(rh.contact_z(141.47, 141.47, -40.0, ident), -40.0)
        self.assertAlmostEqual(rh.contact_z(131.47, 141.47, -40.0, ident), -30.0)

    def test_point_depth_ignores_holes_and_edges(self):
        raw = np.full((20, 20), 1500, np.uint16)          # 150 mm at 0.0001 m per unit
        raw[9:12, 9:12] = 0
        raw[10, 10] = 1400
        self.assertAlmostEqual(rh.point_depth_mm(raw, 0.0001, (10, 10)), 150.0)
        self.assertIsNone(rh.point_depth_mm(raw, 0.0001, (25, 3)))
        self.assertIsNone(rh.point_depth_mm(np.zeros((5, 5), np.uint16), 0.0001, (2, 2)))

    def test_top_location_finds_the_raised_spot(self):
        raw = np.full((40, 60), 1500, np.uint16)
        raw[5:9, 40:44] = 1200                              # a raised thumb, 16 px = 0.67 % (> Studio's 0.5 %)
        inside = np.ones_like(raw, bool)
        u, v = rh.top_location(raw, 0.0001, inside)
        self.assertAlmostEqual(u, 41.5)
        self.assertAlmostEqual(v, 6.5)

    def test_nearest_and_verdict(self):
        name, d = rh.nearest(["wrist", "thumb_tip"], [[0, 0], [10, 0]], (9, 1))
        self.assertEqual(name, "thumb_tip")
        self.assertAlmostEqual(d, 2 ** 0.5)
        self.assertEqual(rh.verdict(-31.79, -20.0, 4.0), {"clears": True, "margin_mm": 7.79, "travel_z_needed": -27.79})
        v = rh.verdict(-23.4, -20.0, 4.0)
        self.assertFalse(v["clears"])
        self.assertEqual(v["margin_mm"], -0.6)


if __name__ == "__main__":
    unittest.main()
