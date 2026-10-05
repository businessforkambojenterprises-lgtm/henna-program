"""The v3 web UI only opens hand-detection runs inside this project's output folders."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server_v3 as ws3


class RunIds(unittest.TestCase):
    def test_bad_ids_rejected(self):
        for bad in (None, "", "first_run", "x:first_run", "cli:../../palm_3d_scans", "web:..\\..\\x",
                    "cli:first run", "cli:" + "a" * 100):
            with self.assertRaises(ValueError):
                ws3.run_dir_from_id(bad)

    def test_margin_validation(self):
        self.assertEqual(ws3.margin_of({}), ws3.DEFAULT_MARGIN)
        for bad in (-0.1, 0.95, "abc"):
            with self.assertRaises(ValueError):
                ws3.margin_of({"margin": bad})

    def test_accept_requires_confirmation(self):
        runs = ws3.list_hand_runs()
        if not runs:
            self.skipTest("no hand-detection runs on this PC")
        with self.assertRaises(ValueError):
            ws3.region_accept({"run": runs[0]["id"], "polygon": [[1, 1], [50, 1], [50, 50]]})


if __name__ == "__main__":
    unittest.main()
