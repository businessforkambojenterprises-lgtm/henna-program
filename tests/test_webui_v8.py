"""v8 refuses a targets file whose head pose belongs to a different recording."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server_v8 as ws8

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T1 = os.path.join(ROOT, "validation", "S2026-10-03-A", "pencil_targets_v2_pose1.json")
T2 = os.path.join(ROOT, "validation", "S2026-10-03-A", "pencil_targets_v2_pose2.json")


DOCS = os.path.join(os.path.expanduser("~"), "Documents")      # this OS's own path form (also on the Raspberry Pi)


@unittest.skipUnless(os.path.isfile(T1) and os.path.isfile(T2), "session targets not present")
class Pairing(unittest.TestCase):
    def test_matching_pair_ok(self):
        ws8.check_pairing({"targets": T1, "recording": os.path.join(DOCS, "20261003_161018.db3")})
        ws8.check_pairing({"targets": T2, "recording": os.path.join(DOCS, "20261003_161432.db3")})

    def test_mixed_pair_refused(self):
        with self.assertRaises(ValueError):
            ws8.check_pairing({"targets": T2, "recording": os.path.join(DOCS, "20261003_161018.db3")})

    def test_targets_outside_validation_refused(self):
        with self.assertRaises(ValueError):
            ws8.check_pairing({"targets": __file__, "recording": "x.db3"})


if __name__ == "__main__":
    unittest.main()
