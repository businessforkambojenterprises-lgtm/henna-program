"""The v2 web UI only reads recordings from approved folders."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server_v2 as ws2


class ApprovedRecordings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ok = os.path.join(self.tmp, "Documents")
        self.other = os.path.join(self.tmp, "Other")
        os.makedirs(self.ok)
        os.makedirs(self.other)
        for d in (self.ok, self.other):
            open(os.path.join(d, "rec.bag"), "wb").close()
        open(os.path.join(self.ok, "notes.txt"), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_inside_accepted(self):
        ws2.resolve_recording(os.path.join(self.ok, "rec.bag"), [self.ok])

    def test_outside_and_escape_rejected(self):
        for p in (os.path.join(self.other, "rec.bag"), os.path.join(self.ok, "..", "Other", "rec.bag")):
            with self.assertRaises(ValueError):
                ws2.resolve_recording(p, [self.ok])

    def test_only_recordings_listed(self):
        names = [i["name"] for i in ws2.list_recordings([self.ok])]
        self.assertEqual(names, ["rec.bag"])


if __name__ == "__main__":
    unittest.main()
