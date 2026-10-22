"""machine_travel_z_set.py (user 2026-10-22: "fix this anyhow in the software"): travel Z changes in the registry and
the approved calibration file together (Studio checks they agree), copies of both are kept, the scan position and
floor stay, and values above the top, below the scan Z or at the floor are refused."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_travel_z_set as tool  # noqa: E402
from henna_preview.machines import Profiles  # noqa: E402

CAL = "machines/ball-screw/calibration_test.json"


class TravelZTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "machines", "ball-screw"))
        motion = {"scan_pose_mpos": [65.7, 80.0, -5.0], "travel_z": -5.0, "firmware_z_floor": -40.0}
        reg = {"schema": "henna_machines/1", "machines": [
            {"id": "ball-screw", "name": "Ball screw (new machine)", "calibration": CAL, "work_area_mm": [225.0, 176.0],
             "motion": motion}]}
        with open(os.path.join(self.root, "machines", "registry.json"), "w", encoding="utf-8") as f:
            json.dump(reg, f)
        with open(os.path.join(self.root, CAL), "w", encoding="utf-8") as f:
            json.dump({"capture_pose_mpos": [65.7, 80.0, -5.0], "travel_z": -5.0, "firmware_z_floor": -40.0,
                       "xy_calibration": "x.json"}, f)
        self.profiles = Profiles(self.root)

    def test_both_files_change_together_and_copies_are_kept(self):
        rec = tool.set_travel_z(self.profiles, "ball-screw", -1.0, stamp="20261022-130000")
        self.assertEqual((rec["travel_z_before"], rec["travel_z"]), (-5.0, -1.0))
        self.assertEqual(rec["skin_allowed_under_travel_up_to_z"], -10.0)
        m = self.profiles.get("ball-screw")
        self.assertEqual(m["motion"]["travel_z"], -1.0)
        self.assertEqual(m["motion"]["scan_pose_mpos"], [65.7, 80.0, -5.0])
        self.assertEqual(self.profiles.cal_of(m)["travel_z"], -1.0)
        self.assertEqual(self.profiles.cal_of(m)["xy_calibration"], "x.json")
        self.profiles.check_consistent(m)
        hist = os.listdir(os.path.join(self.root, "machines", "history"))
        self.assertIn("calibration_test.before-travel-z-20261022-130000.json", hist)
        self.assertTrue(any(h.startswith("registry_") for h in hist))

    def test_refusals(self):
        mo = self.profiles.motion(self.profiles.get("ball-screw"))
        for bad, why in ((0.0, "or lower"), (-0.2, "or lower"), (-6.0, "scan position"), (-40.0, "scan position")):
            with self.assertRaisesRegex(ValueError, why):
                tool.check_value(bad, mo)
        self.assertEqual(tool.check_value(-5.0, mo), -5.0)
        self.assertEqual(tool.check_value(-0.5, mo), -0.5)

    def test_dry_run_changes_nothing(self):
        saved = tool.ROOT
        tool.ROOT = self.root
        try:
            self.assertEqual(tool.main(["--machine", "ball-screw", "--travel-z", "-1"]), 0)
        finally:
            tool.ROOT = saved
        self.assertEqual(self.profiles.get("ball-screw")["motion"]["travel_z"], -5.0)


if __name__ == "__main__":
    unittest.main()
