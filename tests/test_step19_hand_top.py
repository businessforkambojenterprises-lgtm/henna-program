"""Step 19 hand top: uses only usable (non-zero) median depth inside the grown landmark hull."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from step19_hand_path_avg import hand_top_from_raw

SCALE = 0.0001
K = 141.47


class HandTop(unittest.TestCase):
    def setUp(self):
        self.raw = np.full((100, 120), 2000.0)          # bed at 200 mm
        self.raw[30:70, 40:80] = 1700.0                 # hand back at 170 mm
        self.lm = np.array([[45, 35], [75, 35], [75, 65], [45, 65], [60, 50]], float)

    def test_top_is_highest_usable_skin(self):
        top = hand_top_from_raw(self.raw, SCALE, self.lm, K, -40.0, lambda d: d)
        self.assertAlmostEqual(top["min_depth_mm"], 170.0)
        self.assertAlmostEqual(top["hand_top_contact_z"], -40.0 - (170.0 - K), places=2)

    def test_unusable_pixels_are_ignored_not_filled(self):
        raw = self.raw.copy()
        raw[40:50, 50:60] = 0                           # rejected (moving) pixels
        top = hand_top_from_raw(raw, SCALE, self.lm, K, -40.0, lambda d: d)
        self.assertAlmostEqual(top["min_depth_mm"], 170.0)
        self.assertLess(top["pixels"], hand_top_from_raw(self.raw, SCALE, self.lm, K, -40.0, lambda d: d)["pixels"])


if __name__ == "__main__":
    unittest.main()
