"""Multi-frame median depth: uses only measured frames, rejects unstable/noisy pixels, never fills holes."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.depth_surface import MISSING, NOISY, UNSTABLE, VALID
from henna_preview.recording import RecordingError
from henna_preview.recording_multi import median_depth

SCALE = 0.0001  # D405: 0.1 mm per raw unit


class MedianDepth(unittest.TestCase):
    def stack(self, n=10, value=1640.0):
        return np.full((n, 3, 3), value)

    def test_median_ignores_missing_frames(self):
        s = self.stack()
        s[:2, 1, 1] = 0           # two dropouts
        s[2:, 1, 1] = [1630, 1640, 1640, 1640, 1650, 1640, 1640, 1640]
        out, status, _ = median_depth(s, SCALE)
        self.assertEqual(status[1, 1], VALID)
        self.assertEqual(out[1, 1], 1640.0)   # zeros did not pull the median down

    def test_rarely_measured_pixel_is_unusable(self):
        s = self.stack()
        s[:6, 0, 0] = 0           # measured in 4 of 10 frames
        out, status, _ = median_depth(s, SCALE, min_valid_frac=0.8)
        self.assertEqual(status[0, 0], UNSTABLE)
        self.assertEqual(out[0, 0], 0.0)

    def test_jumping_pixel_is_unusable(self):
        s = self.stack()
        s[:, 2, 2] = [1600, 1640] * 5   # 2 mm MAD
        out, status, _ = median_depth(s, SCALE, max_mad_mm=1.0)
        self.assertEqual(status[2, 2], NOISY)
        self.assertEqual(out[2, 2], 0.0)

    def test_never_measured_pixel_stays_missing_and_is_not_filled(self):
        s = self.stack()
        s[:, 0, 2] = 0
        out, status, info = median_depth(s, SCALE)
        self.assertEqual(status[0, 2], MISSING)
        self.assertEqual(out[0, 2], 0.0)
        self.assertTrue((out[status == VALID] == 1640.0).all())
        self.assertEqual(info["pixel_status"]["missing"], 1)

    def test_noise_is_reduced(self):
        rng = np.random.default_rng(0)
        s = 1640.0 + rng.normal(0, 5, (15, 40, 40))   # 0.5 mm frame noise
        out, status, _ = median_depth(s, SCALE)
        self.assertTrue((status == VALID).all())
        self.assertLess(np.std(out), 0.5 * np.std(s[0]))

    def test_rejects_wrong_shape(self):
        with self.assertRaises(RecordingError):
            median_depth(np.zeros((3, 3)), SCALE)


if __name__ == "__main__":
    unittest.main()
