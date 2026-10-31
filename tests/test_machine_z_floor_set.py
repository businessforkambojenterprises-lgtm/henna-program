"""machine_z_floor_set.py (user 2026-10-31: belt drive Z end -190): the floor changes in the registry and the approved
calibration file together, copies are kept, scan and travel Z stay, a floor at or above them is refused, the machine id
is required (the ball-screw machine keeps its own floor)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_z_floor_set as tool  # noqa: E402
from henna_preview.machines import Profiles  # noqa: E402

CAL = "machines/belt/calibration_test.json"


class ZFloorTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "machines", "belt"))
        motion = {"scan_pose_mpos": [110.2, 42.6, -40.0], "travel_z": -20.0, "firmware_z_floor": -97.0}
        other = {"scan_pose_mpos": [65.7, 150.0, -0.5], "travel_z": -0.5, "firmware_z_floor": -49.0}
        reg = {"schema": "henna_machines/1", "machines": [
            {"id": "belt", "name": "Belt", "calibration": CAL, "work_area_mm": [225.0, 176.0], "motion": motion},
            {"id": "ball", "name": "Ball", "calibration": None, "work_area_mm": [225.0, 235.0], "motion": other}]}
        with open(os.path.join(self.root, "machines", "registry.json"), "w", encoding="utf-8") as f:
            json.dump(reg, f)
        with open(os.path.join(self.root, CAL), "w", encoding="utf-8") as f:
            json.dump({"capture_pose_mpos": [110.2, 42.6, -40.0], "travel_z": -20.0, "firmware_z_floor": -97.0}, f)
        self.profiles = Profiles(self.root)

    def test_both_files_change_together_and_copies_are_kept(self):
        rec = tool.set_floor(self.profiles, "belt", -189.0, stamp="20261031-130000")
        self.assertEqual((rec["z_floor_before"], rec["z_floor"], rec["controller_132_to_set"]), (-97.0, -189.0, 190.0))
        m = self.profiles.get("belt")
        self.assertEqual(self.profiles.motion(m)["firmware_z_floor"], -189.0)
        self.assertEqual(self.profiles.motion(m)["scan_pose_mpos"], [110.2, 42.6, -40.0])
        self.assertEqual(self.profiles.motion(m)["travel_z"], -20.0)
        self.assertEqual(self.profiles.cal_of(m)["firmware_z_floor"], -189.0)
        hist = os.listdir(os.path.join(self.root, "machines", "history"))
        self.assertTrue(any("before-z-floor-20261031-130000" in n for n in hist))

    def test_the_other_machine_is_untouched(self):
        tool.set_floor(self.profiles, "belt", -189.0)
        self.assertEqual(self.profiles.motion(self.profiles.get("ball"))["firmware_z_floor"], -49.0)

    def test_floor_at_or_above_scan_or_travel_z_is_refused(self):
        for bad in (-40.0, -20.0, -10.0):
            with self.assertRaises(ValueError):
                tool.check_value(bad, self.profiles.motion(self.profiles.get("belt")))

    def test_machine_id_is_required(self):
        with self.assertRaises(SystemExit):
            tool.main(["--floor", "-189"])


if __name__ == "__main__":
    unittest.main()
