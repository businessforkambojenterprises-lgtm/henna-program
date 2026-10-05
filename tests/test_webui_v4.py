"""The v4 web UI only places designs on accepted regions inside this project's outputs."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server_v4 as ws4
from henna_preview.region import RegionError


class RegionIds(unittest.TestCase):
    def test_bad_ids_rejected(self):
        for bad in (None, "", "cli_check", "x:cli_check", "cli:../step04/first_run", "web:..\\..\\x", "cli:a b"):
            with self.assertRaises(ValueError):
                ws4.region_dir_from_id(bad)

    def test_unaccepted_region_refused(self):
        unaccepted = [r for r in ws4.list_regions() if not r["accepted"]]
        if not unaccepted:
            self.skipTest("no unaccepted region on this PC")
        with self.assertRaises(RegionError):
            ws4.loaded(unaccepted[0]["id"])
        with self.assertRaises(RegionError):
            ws4.design_preview({"region": unaccepted[0]["id"], "shape": "circle", "size_mm": 10,
                                "centre": [100, 100], "rotation_deg": 0, "jump_mm": 3})

    def test_design_args_validation(self):
        for bad in ({"shape": "circle", "size_mm": "x", "centre": [1, 2]},
                    {"shape": "circle", "size_mm": 10, "centre": [1]},
                    {"shape": "circle", "size_mm": 10}):
            with self.assertRaises(ValueError):
                ws4.design_args(bad)


if __name__ == "__main__":
    unittest.main()
