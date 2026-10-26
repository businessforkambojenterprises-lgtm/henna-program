"""A refused scan whose file Windows still holds open (2026-10-26: "[WinError 32] ... .db3 -> .db3.rejected" hid the
real reason, "only 19 frame pairs recorded"): the rename never raises, and the scan's own error is the message."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import web_recorder  # noqa: E402


class RejectTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "scan.db3")
        with open(self.path, "wb") as f:
            f.write(b"x")

    def test_renamed(self):
        self.assertEqual(web_recorder.reject(self.path), self.path + ".rejected")
        self.assertFalse(os.path.exists(self.path))

    def test_a_held_file_is_left_and_nothing_raises(self):
        with patch.object(web_recorder.os, "replace", side_effect=PermissionError(32, "in use")), \
                patch.object(web_recorder.time, "sleep"):
            self.assertIsNone(web_recorder.reject(self.path))
        self.assertTrue(os.path.exists(self.path))

    def test_missing_file(self):
        self.assertIsNone(web_recorder.reject(os.path.join(self.dir, "none.db3")))


if __name__ == "__main__":
    unittest.main()
