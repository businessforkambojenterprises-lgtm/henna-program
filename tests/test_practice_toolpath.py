"""Step 10 checks: practice output refuses unsafe values and contains only allowed commands."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.practice_toolpath import (HEADER, PracticeError, check_params, gcode_lines,
                                             independent_check, to_machine_strokes)

P = {"draw_z": -88.0, "z_floor": -95.0, "draw_feed_mm_min": 300.0, "travel_feed_mm_min": 1000.0, "travel_z": -60.0}
R = np.array([[0.0, -1.0], [-1.0, 0.0]])
T = np.array([-47.0, 17.8])
HEAD = [145.8, 42.6]
SEG = [[[-10.0, 0.0, 200.0], [-5.0, 5.0, 200.0], [0.0, 10.0, 200.0]], [[10.0, 0.0, 200.0], [12.0, -3.0, 200.0]]]


def build(p=P):
    strokes = to_machine_strokes(SEG, HEAD, R, T)
    return strokes, gcode_lines(strokes, p)


class Params(unittest.TestCase):
    def test_user_values_accepted(self):
        check_params(P)

    def test_draw_below_floor_refused(self):
        with self.assertRaises(PracticeError):
            check_params(dict(P, draw_z=-96.0))

    def test_travel_not_above_draw_refused(self):
        with self.assertRaises(PracticeError):
            check_params(dict(P, travel_z=-90.0))

    def test_feed_limits(self):
        for bad in (0, -5, 5000):
            with self.assertRaises(PracticeError):
                check_params(dict(P, draw_feed_mm_min=bad))


class Output(unittest.TestCase):
    def test_header_and_invalid_first_command(self):
        _, lines = build()
        self.assertEqual(lines[:3], HEADER)
        self.assertTrue(lines[2].startswith("$PRACTICE"))

    def test_only_motion_commands_no_valve(self):
        _, lines = build()
        body = "\n".join(lines[3:])
        for bad in ("M3", "M4", "M5", "M7", "M8", "M9", "S", "G92", "G10", "$"):
            self.assertNotIn(bad, body)

    def test_breaks_become_separate_strokes(self):
        strokes, lines = build()
        self.assertEqual(len(strokes), 2)
        self.assertEqual(sum(1 for l in lines if l == "G53 G1 Z-88.000 F300"), 2)

    def test_independent_check_passes_and_simulates(self):
        _, lines = build()
        moves = independent_check(lines, P)
        self.assertEqual(sum(1 for m in moves if m["kind"] == "draw"), 3)
        self.assertTrue(all(m["to"][2] in (-88.0, -60.0) for m in moves))

    def test_independent_check_catches_tampering(self):
        _, lines = build()
        bad = [l.replace("Z-88.000", "Z-96.000") for l in lines]
        with self.assertRaises(PracticeError):
            independent_check(bad, P)
        with self.assertRaises(PracticeError):
            independent_check(lines[:3] + ["M3 S1000"] + lines[3:], P)

    def test_path_outside_travel_refused(self):
        with self.assertRaises(PracticeError):
            to_machine_strokes([[[0, 0, 200], [0, -200, 200]]], HEAD, R, T)


if __name__ == "__main__":
    unittest.main()
