"""Checks that the web UI only reads files from approved folders."""
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server as ws


class ApprovedFolders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ok_dir = os.path.join(self.tmp, "approved")
        self.other = os.path.join(self.tmp, "other")
        os.makedirs(self.ok_dir)
        os.makedirs(self.other)
        self.good = os.path.join(self.ok_dir, "scan.npz")
        np.savez(self.good, median=np.ones((2, 2)), intr=np.array([1.0, 1.0, 0.5, 0.5]))
        self.outside = os.path.join(self.other, "scan.npz")
        shutil.copy(self.good, self.outside)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_file_inside_is_accepted(self):
        self.assertEqual(os.path.normcase(ws.resolve_input(self.good, [self.ok_dir])),
                         os.path.normcase(os.path.realpath(self.good)))

    def test_file_outside_is_rejected(self):
        with self.assertRaises(ValueError):
            ws.resolve_input(self.outside, [self.ok_dir])

    def test_dotdot_escape_is_rejected(self):
        sneaky = os.path.join(self.ok_dir, "..", "other", "scan.npz")
        with self.assertRaises(ValueError):
            ws.resolve_input(sneaky, [self.ok_dir])

    def test_wrong_extension_and_missing_file_rejected(self):
        txt = os.path.join(self.ok_dir, "notes.txt")
        open(txt, "w").close()
        with self.assertRaises(ValueError):
            ws.resolve_input(txt, [self.ok_dir])
        with self.assertRaises(ValueError):
            ws.resolve_input(os.path.join(self.ok_dir, "nope.npz"), [self.ok_dir])

    def test_listing_flags_incompatible_files(self):
        np.savez(os.path.join(self.ok_dir, "bed.npz"), bed=np.ones((2, 2)))
        items = {i["name"]: i for i in ws.list_inputs([self.ok_dir])}
        self.assertTrue(items["scan.npz"]["compatible"])
        self.assertFalse(items["bed.npz"]["compatible"])

    def test_bad_parameters_rejected(self):
        ws.APPROVED_DIRS, saved = [self.ok_dir], ws.APPROVED_DIRS
        try:
            ws.validate_params({"path": self.good, "depth_units": "mm"})  # valid baseline
            with self.assertRaises(ValueError):
                ws.validate_params({"path": self.good, "depth_units": "inches"})
            with self.assertRaises(ValueError):
                ws.validate_params({"path": self.good, "depth_units": "mm", "min_valid_frac": 2})
        finally:
            ws.APPROVED_DIRS = saved


if __name__ == "__main__":
    unittest.main()
