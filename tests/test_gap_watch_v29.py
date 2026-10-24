"""Live gap under the nozzle (user 2026-10-24: "I want realsense to check realtime distance for better accuracy" ->
"build A, pause below 4 mm or above 9 mm"): while the nozzle DRAWS (its planned gap is the nozzle height), the gap
measured live under it must stay 4-9 mm, else the hand watch pauses (verdict "gap"); jumps, rises, air runs and jobs
without a nozzle height are not judged; Studio gives the watch the job's nozzle height."""
import copy
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import hand_watch as hw  # noqa: E402


def skin(z, x=(100, 140), y=(80, 120)):
    xs, ys = np.meshgrid(np.arange(x[0], x[1], 0.5), np.arange(y[0], y[1], 0.5))
    return np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, float(z))])


class GapTests(unittest.TestCase):
    def setUp(self):
        self.ref = hw.Reference(skin(-30.0))
        self.ref.standoff_mm = 6.0

    def test_drawing_at_the_planned_height_is_ok_and_reported(self):
        r = hw.evaluate(self.ref, skin(-30.0), (120.0, 100.0, -24.0))
        self.assertEqual(r["verdict"], "ok")
        self.assertTrue(r["drawing"])
        self.assertAlmostEqual(r["near_gap_mm"], 6.0)
        self.assertAlmostEqual(r["planned_gap_mm"], 6.0)

    def test_too_close_or_too_far_while_drawing_pauses(self):
        r = hw.evaluate(self.ref, skin(-27.5), (120.0, 100.0, -24.0))       # skin 2.5 mm higher: gap 3.5
        self.assertEqual(r["verdict"], "gap")
        self.assertIn("3.5 mm above the skin under it (planned 6 mm; allowed 4 to 9 mm", hw.message(r))
        r = hw.evaluate(self.ref, skin(-33.5), (120.0, 100.0, -24.0))       # skin 3.5 mm lower: gap 9.5
        self.assertEqual(r["verdict"], "gap")
        for z in (-28.0, -33.0):                                            # gap 4 / 9: still allowed
            self.assertEqual(hw.evaluate(self.ref, skin(z), (120.0, 100.0, -24.0))["verdict"], "ok", z)

    def test_not_judged_on_jumps_in_air_runs_or_without_a_height(self):
        r = hw.evaluate(self.ref, skin(-30.0), (120.0, 100.0, -1.0))         # at travel height: planned 29 mm
        self.assertEqual(r["verdict"], "ok")
        self.assertFalse(r["drawing"])
        air = hw.air_reference(self.ref)                                    # air run: the area must be empty
        r = hw.evaluate(air, skin(-70.0), (120.0, 100.0, -24.0))
        self.assertNotEqual(r["verdict"], "gap")
        plain = copy.copy(self.ref)
        plain.standoff_mm = None
        self.assertEqual(hw.evaluate(plain, skin(-27.5), (120.0, 100.0, -24.0))["verdict"], "ok")

    def test_studio_gives_the_watch_the_nozzle_height_and_pauses_on_it(self):
        import webui_server_v28 as v28
        import webui_server_v29 as v29
        saved = v29._REFERENCE
        v29._REFERENCE = lambda job: hw.Reference(skin(-30.0))
        try:
            ref = v29.reference_with_gap({"steps": {"design": {"standoff_mm": 6.0}}})
            self.assertEqual(ref.standoff_mm, 6.0)
            self.assertEqual(v29.reference_with_gap({"steps": {}}).standoff_mm, v28.REAL_MIN_STANDOFF_MM)
        finally:
            v29._REFERENCE = saved
        self.assertIs(v28.reference, v29.reference_with_gap)
        import inspect
        self.assertIn('("moved", "near", "gap")', inspect.getsource(v28.Watch._loop))


if __name__ == "__main__":
    unittest.main()
