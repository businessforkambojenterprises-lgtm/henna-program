"""Step 12 checks: nozzle simulation path keeps every point above its own skin floor."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.nozzle_toolpath import (HEADER, NozzlePathError, check_params, gcode_lines,
                                           independent_check, map_strokes)

P = {"standoff_mm": 3.0, "floor_above_skin_mm": 1.0, "draw_feed_mm_min": 300.0, "travel_feed_mm_min": 1000.0,
     "travel_z": -20.0, "firmware_z_floor": -97.0}
HEAD = {"x": 104.5, "y": 96.2, "z": -20.0}
CAL = {"R": [[0.0, -1.0], [-1.0, 0.0]], "t_mm": [-52.0, 31.1]}
K = 141.1
SEG = [[[0.0, 0.0, 180.0], [5.0, 0.0, 182.0], [5.0, 5.0, 185.0]]]


def build(p=P, seg=SEG):
    s = map_strokes(seg, HEAD, CAL, K, p)
    lines, floors = gcode_lines(s, p)
    return s, lines, floors


class Mapping(unittest.TestCase):
    def test_z_follows_skin_plus_standoff(self):
        s, _, _ = build()
        skin = HEAD["z"] - (np.array([180.0, 182.0, 185.0]) - K)
        np.testing.assert_allclose(s[0][:, 2], skin)
        np.testing.assert_allclose(s[0][:, 3], skin + 3.0)
        np.testing.assert_allclose(s[0][:, 4], skin + 1.0)

    def test_travel_must_clear_drawing(self):
        with self.assertRaises(NozzlePathError):
            build(dict(P, travel_z=-70.0))

    def test_outside_travel_refused(self):
        with self.assertRaises(NozzlePathError):
            build(seg=[[[0, 0, 180], [0, -300, 180]]])

    def test_bad_params(self):
        for bad in ({"standoff_mm": 0.5}, {"floor_above_skin_mm": 0}, {"draw_feed_mm_min": 9000}):
            with self.assertRaises(NozzlePathError):
                check_params(dict(P, **bad))


class Output(unittest.TestCase):
    def test_header_and_only_motion(self):
        _, lines, floors = build()
        self.assertEqual(lines[:3], HEADER)
        body = "\n".join(lines[3:])
        for bad in ("M3", "M4", "M5", "M7", "M8", "M9", " S", "G92", "G10", "$"):
            self.assertNotIn(bad, body)
        self.assertEqual(independent_check(lines, floors, P), 3)

    def test_check_catches_point_below_its_floor(self):
        _, lines, floors = build()
        bad = list(lines)
        j = next(i for i, l in enumerate(bad) if l.startswith("G53 G1"))
        bad[j] = re.sub(r"Z-?\d+\.\d+", f"Z{floors[0] - 0.5:.3f}", bad[j])
        with self.assertRaises(NozzlePathError):
            independent_check(bad, floors, P)

    def test_check_catches_added_command(self):
        _, lines, floors = build()
        with self.assertRaises(NozzlePathError):
            independent_check(lines[:3] + ["M8"] + lines[3:], floors, P)


import re  # noqa: E402

if __name__ == "__main__":
    unittest.main()
