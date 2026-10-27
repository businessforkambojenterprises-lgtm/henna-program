"""The scan read once and shared (user 2026-10-26: "scanning" -> "build 1 and 2"): the fast median gives exactly
numpy's nanmedian, and a second read of the same recording, window and thresholds comes from the cache - a changed
file or other settings are read again."""
import os
import shutil
import sys
import tempfile
import time
import unittest
import warnings
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import recording_multi as rm  # noqa: E402


class MedianTests(unittest.TestCase):
    def test_same_numbers_as_numpy(self):
        rng = np.random.default_rng(2)
        for n in (1, 2, 7, 15):
            a = rng.integers(1000, 3000, (n, 40, 50)).astype(float)
            a[rng.random(a.shape) < 0.2] = np.nan
            a[:, :3, :3] = np.nan                                       # never measured
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                want = np.nanmedian(a, axis=0)
            np.testing.assert_array_equal(rm.nanmedian0(a), want)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.rec = os.path.join(self.dir, "scan.db3")
        with open(self.rec, "wb") as f:
            f.write(b"recording")
        p = patch.object(rm, "CACHE_DIR", os.path.join(self.dir, "cache"))
        p.start()
        self.addCleanup(p.stop)
        self.calls = 0

    def fake_read(self, path, start, count, mv, mm):
        self.calls += 1
        a = np.full((4, 5), float(self.calls))
        return {"path": path, "frame_index": start + count // 2, "depth_raw_aligned": a,
                "depth_status": np.zeros((4, 5), np.uint8), "colour_rgb": np.zeros((4, 5, 3), np.uint8),
                "depth_raw_single_middle": np.ones((4, 5), np.uint16), "depth_scale_m_per_unit": 0.0001,
                "colour_intrinsics": {"fx": 1.0}, "frame": {"depth_frame_number": 7}, "multi": {"frames": count},
                "read_time_s": 0.1}

    def test_second_read_is_shared_and_identical(self):
        with patch.object(rm, "_read_frames_median", self.fake_read):
            a = rm.read_frames_median(self.rec, 29, 15, 0.8, 1.0)
            b = rm.read_frames_median(self.rec, 29, 15, 0.8, 1.0)
        self.assertEqual(self.calls, 1)
        for k in rm._ARRAYS:
            np.testing.assert_array_equal(a[k], b[k])
            self.assertEqual(a[k].dtype, b[k].dtype)
        self.assertEqual({k: v for k, v in a.items() if k not in rm._ARRAYS},
                         {k: v for k, v in b.items() if k not in rm._ARRAYS})

    def test_other_settings_or_a_changed_file_are_read_again(self):
        with patch.object(rm, "_read_frames_median", self.fake_read):
            rm.read_frames_median(self.rec, 29, 15, 0.8, 1.0)
            rm.read_frames_median(self.rec, 28, 15, 0.8, 1.0)            # another window
            rm.read_frames_median(self.rec, 29, 15, 0.9, 1.0)            # other thresholds
            self.assertEqual(self.calls, 3)
            time.sleep(0.02)
            with open(self.rec, "ab") as f:
                f.write(b"more")
            got = rm.read_frames_median(self.rec, 29, 15, 0.8, 1.0)
        self.assertEqual(self.calls, 4)
        self.assertEqual(float(got["depth_raw_aligned"][0, 0]), 4.0)


if __name__ == "__main__":
    unittest.main()
