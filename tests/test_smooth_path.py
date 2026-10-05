"""Step 16 checks: smoothing never lets the nozzle go below raw skin + margin, and corners become small."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.plan_design import plan_points
from henna_preview.smooth_path import build_smooth_strokes, corner_stats, smooth, thin

HEAD = {"x": 0.0, "y": 0.0, "z": -20.0}
K = 141.1


def fake_measured(plan, skin_fn, holes=()):
    out = []
    for i, (x, y) in enumerate(plan):
        if i in holes:
            out.append(("missing_depth", None))
            continue
        skin = skin_fn(x, y)
        d = HEAD["z"] - skin + K  # invert skin = head_z - (d - k)
        out.append(("ok", np.array([0.0, 0.0, d])))
    return out


class Smooth(unittest.TestCase):
    def setUp(self):
        self.plan, self.closed = plan_points("circle", 20.0, (50.0, 50.0), spacing_mm=0.5)
        rng = np.random.default_rng(1)
        self.noise = rng.normal(0, 0.4, len(self.plan))
        self.bump = lambda x, y: -60.0 + 0.5 * (x - 50.0)  # 10 mm slope across the circle

    def measured(self, holes=()):
        noisy = {tuple(p): n for p, n in zip(map(tuple, self.plan), self.noise)}
        return fake_measured(self.plan, lambda x, y: self.bump(x, y) + noisy[(x, y)], holes)

    def test_floor_holds_on_raw_skin_everywhere(self):
        strokes, st = build_smooth_strokes(self.plan, self.closed, self.measured(), HEAD, K, None, 3.0, 1.0)
        for s in strokes:
            self.assertTrue((s[:, 3] >= s[:, 4] - 1e-9).all())
            self.assertTrue((s[:, 4] == s[:, 2] + 1.0).all())

    def test_xy_is_exact_plan_circle(self):
        strokes, _ = build_smooth_strokes(self.plan, self.closed, self.measured(), HEAD, K, None, 3.0, 1.0)
        r = np.hypot(strokes[0][:, 0] - 50, strokes[0][:, 1] - 50)
        np.testing.assert_allclose(r, 10.0, atol=1e-9)
        cs = corner_stats(strokes)
        self.assertLess(cs["direction_change_deg_max"], 12.0)

    def test_thinning_keeps_points_that_protect_the_floor(self):
        z = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
        floor = np.array([-1.0, -1.0, 0.5, -1.0, -1.0])  # a bump in the middle
        keep = thin(np.zeros((5, 2)), np.maximum(z, floor), floor, 4)
        self.assertIn(2, keep)

    def test_hole_breaks_path(self):
        strokes, st = build_smooth_strokes(self.plan, self.closed, self.measured(holes=range(10, 14)), HEAD, K, None,
                                           3.0, 1.0)
        self.assertEqual(st["rejected_missing_depth"], 4)
        self.assertGreaterEqual(len(strokes), 1)
        self.assertLess(sum(len(s) for s in strokes), len(self.plan))

    def test_wrap_smoothing_is_continuous(self):
        v = np.r_[np.zeros(10), np.ones(10)]
        s = smooth(v, 5, True)
        self.assertEqual(len(s), len(v))
        self.assertAlmostEqual(s[0], 0.4)  # wraps around from the 1s at the end


if __name__ == "__main__":
    unittest.main()
