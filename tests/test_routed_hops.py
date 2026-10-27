"""A jump routed round tall skin is not also planned as a low hop (2026-10-27: "step 23 refused: program text does not
contain exactly the planned low hops" - Studio's gap route replaced a planned low hop, so step 23's own count of the low
hops in the text no longer matched its plan)."""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import step_path_clearance as spc  # noqa: E402
from henna_preview import hop_path as hp  # noqa: E402

TRAVEL = -0.5


def stroke(x0, y):
    pts = np.array([[x0, y, -45.0, -39.0, -44.0], [x0 + 5, y, -45.0, -39.0, -44.0]])
    return pts


class RoutedHopTests(unittest.TestCase):
    def setUp(self):
        self.paths = [stroke(100, 100), stroke(100, 110), stroke(100, 120)]
        self.sample = lambda pts: -45.0                                    # flat skin: every hop can be low

    def test_the_routed_jump_rises_to_travel_the_others_stay_low(self):
        gaps = [{"from": [105.0, 110.0], "to": [100.0, 120.0], "waypoints": [[90.0, 115.0]]}]
        plan = spc.wrap_plan(hp.plan_hops, {"gaps": gaps})
        hops = plan(self.paths, self.sample, TRAVEL)
        self.assertEqual([h["low"] for h in hops], [True, False])
        self.assertEqual(hops[1]["z"], TRAVEL)
        self.assertIn("routed", hops[1]["reason"])

    def test_no_gaps_is_the_plain_plan(self):
        self.assertIs(spc.wrap_plan(hp.plan_hops, None), hp.plan_hops)
        self.assertIs(spc.wrap_plan(hp.plan_hops, {"gaps": []}), hp.plan_hops)


if __name__ == "__main__":
    unittest.main()
