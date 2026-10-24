"""Step 21: skin lower than the nozzle can reach is a clear refusal (2026-10-24: "design check failed: ...
NozzlePathError: line 8: Z or feed out of range" - fingers in the jig's cut-out, nozzle 6 mm above them at Z -40.3)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import step21_design_preview as s21  # noqa: E402

P = {"standoff_mm": 6.0, "floor_above_skin_mm": 1.0, "firmware_z_floor": -40.0, "travel_z": -1.0}


class TooLow(unittest.TestCase):
    def setUp(self):
        self.saved = (s21.read_svg, s21.place, s21.measure_under_plan, s21.build_smooth_strokes)
        s21.read_svg = lambda path: {"strokes": [{"filled_only": False, "source": "a"}, {"filled_only": False,
                                                                                         "source": "b"}]}
        s21.place = lambda design, fit, centre, rot, spacing_mm: (
            [{"plan": np.zeros((3, 2)), "closed": False, "source": "a", "length_mm": 5.0},
             {"plan": np.zeros((3, 2)), "closed": False, "source": "b", "length_mm": 5.0}], 1.0)
        s21.measure_under_plan = lambda plan, fr, mask, head, xy: [("ok", None)] * 3
        self.skin = iter([-30.0, -46.3])

        def build(plan, closed, measured, head, k, depth_fn, standoff, floor, jump_mm):
            z = next(self.skin)
            return [np.array([[120.0, 65.0, z, z + standoff, z + floor]] * 3)], {
                "max_raise_above_standoff_due_to_floor_mm": 0.0}
        s21.build_smooth_strokes = build

    def tearDown(self):
        s21.read_svg, s21.place, s21.measure_under_plan, s21.build_smooth_strokes = self.saved

    def test_names_the_low_skin(self):
        cfg = {"design": {"svg": "x.svg", "fit_mm": 30.0, "centre_machine_xy": [120.0, 65.0], "jump_mm": 3.0}}
        out = s21.plan_design(cfg, None, None, {"x": 0, "y": 0, "z": -5}, None, 140.0, None, P)
        problems = out[3]
        self.assertEqual(len(problems), 1)
        self.assertIn("the skin under 1 stroke(s) is too low for the nozzle", problems[0])
        self.assertIn("Z -40.3 (skin Z -46.3, near X120 Y65)", problems[0])
        self.assertIn("lowest it reaches is Z -40", problems[0])


class PastTheTravel(TooLow):
    """2026-10-24: "NozzlePathError: line 570: Y outside travel" - a design reaching Y 177 on a machine that stops at
    Y 176 is a clear refusal saying how far and which way."""

    def setUp(self):
        super().setUp()
        self.skin = iter([-30.0, -30.0])
        build = s21.build_smooth_strokes

        def far(*a, **k):
            st, stats = build(*a, **k)
            st[0][:, 1] = 177.3                                  # the second stroke's Y past the travel
            return st, stats
        self.first = True

        def builder(*a, **k):
            if self.first:
                self.first = False
                return build(*a, **k)
            return far(*a, **k)
        s21.build_smooth_strokes = builder

    def test_names_the_axis_and_the_way(self):
        cfg = {"design": {"svg": "x.svg", "fit_mm": 55.0, "centre_machine_xy": [131.8, 149.8], "jump_mm": 3.0}}
        problems = s21.plan_design(cfg, None, None, {"x": 0, "y": 0, "z": -5}, None, 140.0, None, P)[3]
        self.assertEqual(len(problems), 1)
        self.assertIn("1.3 mm past the machine's Y travel (0 to 176 mm) - move it 2 mm or more to lower Y", problems[0])

    def test_names_the_low_skin(self):
        pass                                                     # (the parent's case; not this one)


if __name__ == "__main__":
    unittest.main()
