"""Raspberry Pi stage 3a check: the pure helpers (no camera, no Pi needed)."""
import importlib.util
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
_spec = importlib.util.spec_from_file_location("stage3a_pi_check", os.path.join(ROOT, "raspberry_pi", "stage3a_pi_check.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

from henna_preview.web_recorder import _depth_rgb  # noqa: E402


class TestHelpers(unittest.TestCase):
    def test_throttled(self):
        self.assertEqual(check.decode_throttled("throttled=0x0"), [])
        self.assertEqual(check.decode_throttled("throttled=0x50005"),
                         ["under-voltage now", "throttled now", "under-voltage since boot", "throttled since boot"])

    def test_swaps(self):
        text = ("Filename\tType\tSize\tUsed\tPriority\n/var/swap file 4194300 102400 -2\n"
                "/dev/zram0 partition 2097148 0 100\n")
        s = check.classify_swaps(text)
        self.assertEqual([(x["kind"], x["size_mb"], x["used_mb"]) for x in s], [("file", 4095, 100), ("zram", 2047, 0)])

    def test_storage_kind(self):
        self.assertEqual(check.storage_kind("/dev/mmcblk0p2"), "SD card")
        self.assertEqual(check.storage_kind("/dev/nvme0n1p2"), "NVMe SSD")
        self.assertEqual(check.storage_kind("/dev/sda1"), "USB drive")
        self.assertEqual(check.storage_kind(""), "unknown")

    def test_gap_stats(self):
        g = check.gap_stats([0.0, 0.25, 0.5, 2.5, 2.75])
        self.assertEqual((g["ticks"], g["max_gap_s"], g["gaps_over_fresh"]), (5, 2.0, 1))
        self.assertIsNone(check.gap_stats([1.0])["max_gap_s"])

    def test_lut_matches_studio_preview_colours(self):
        lut = check.preview_lut()
        d = np.random.default_rng(1).integers(0, 65536, (48, 85), dtype=np.uint16)
        d[:3] = 0
        np.testing.assert_array_equal(lut[d], _depth_rgb(d))

    def test_verdict_flags_stall_and_sd_swap(self):
        r = {"system": {"root_storage": "SD card", "throttled_warnings": [],
                        "swaps": [{"name": "/var/swap", "kind": "file", "size_mb": 4095, "used_mb": 0}]},
             "disk": {"documents_write_mb_s": 22.0},
             "recordings": {"documents_5s": {"status_loop": {"max_gap_s": 2.1, "gaps_over_fresh": 1},
                                             "log_loop": {"max_gap_s": 3.0}, "frame_pairs": 140, "total_s": 19.0,
                                             "depth_units_m": 9.999999747378752e-05},
                            "ram_5s": {"status_loop": {"max_gap_s": 0.3, "gaps_over_fresh": 0},
                                       "log_loop": {"max_gap_s": 0.3}, "frame_pairs": 150, "total_s": 9.0,
                                       "depth_units_m": 9.999999747378752e-05}}}
        flags = {name: flag for flag, name, _ in check.verdict(r)}
        self.assertEqual(flags, {"documents_disk_speed": "NO ", "no_swap_on_sd": "NO ", "power_and_temperature": "OK ",
                                 "recording_documents_5s": "NO ", "recording_ram_5s": "OK "})


if __name__ == "__main__":
    unittest.main()
