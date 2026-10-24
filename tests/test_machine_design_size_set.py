"""machine_design_size_set.py (user 2026-10-24: "increase size of design to 100mm"): the largest design size changes in
the machine's approved calibration file and in current_calibration.json (the rules the next approval copies), copies
of both are kept, nothing else changes, and sizes at or below the smallest or above MAX_LIMIT_MM are refused."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_design_size_set as tool  # noqa: E402
from henna_preview.machines import Profiles  # noqa: E402

CAL = "machines/ball-screw/calibration_test.json"


class DesignSizeTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "machines", "ball-screw"))
        motion = {"scan_pose_mpos": [65.7, 80.0, -5.0], "travel_z": -1.0, "firmware_z_floor": -40.0}
        reg = {"schema": "henna_machines/1", "machines": [
            {"id": "ball-screw", "name": "Ball screw (new machine)", "calibration": CAL, "work_area_mm": [225.0, 176.0],
             "motion": motion}]}
        with open(os.path.join(self.root, "machines", "registry.json"), "w", encoding="utf-8") as f:
            json.dump(reg, f)
        cal = {"capture_pose_mpos": [65.7, 80.0, -5.0], "travel_z": -1.0, "firmware_z_floor": -40.0,
               "xy_calibration": "x.json", "fit_mm_allowed": [10.0, 40.0], "standoff_mm_allowed": [3.0, 6.0]}
        with open(os.path.join(self.root, CAL), "w", encoding="utf-8") as f:
            json.dump(cal, f)
        with open(os.path.join(self.root, "current_calibration.json"), "w", encoding="utf-8") as f:
            json.dump({"fit_mm_allowed": [10.0, 40.0], "frames": 15}, f)
        self.profiles = Profiles(self.root)

    def test_both_files_change_and_copies_are_kept(self):
        rec = tool.set_design_size(self.profiles, "ball-screw", 100.0, stamp="20261024-140000")
        self.assertEqual((rec["fit_mm_allowed_before"], rec["fit_mm_allowed"]), ([10.0, 40.0], [10.0, 100.0]))
        c = self.profiles.cal_of(self.profiles.get("ball-screw"))
        self.assertEqual(c["fit_mm_allowed"], [10.0, 100.0])
        self.assertEqual((c["standoff_mm_allowed"], c["travel_z"], c["xy_calibration"]), ([3.0, 6.0], -1.0, "x.json"))
        with open(os.path.join(self.root, "current_calibration.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"fit_mm_allowed": [10.0, 100.0], "frames": 15})
        hist = os.listdir(os.path.join(self.root, "machines", "history"))
        self.assertIn("calibration_test.before-design-size-20261024-140000.json", hist)
        self.assertIn("current_calibration.before-design-size-20261024-140000.json", hist)

    def test_refusals(self):
        for bad in (10.0, 5.0, 151.0):
            with self.assertRaises(ValueError):
                tool.check_value(bad, [10.0, 40.0])
        self.assertEqual(tool.check_value(100, [10.0, 40.0]), 100.0)

    def test_dry_run_changes_nothing(self):
        saved = tool.ROOT
        tool.ROOT = self.root
        try:
            self.assertEqual(tool.main(["--machine", "ball-screw", "--max-mm", "100"]), 0)
        finally:
            tool.ROOT = saved
        self.assertEqual(self.profiles.cal_of(self.profiles.get("ball-screw"))["fit_mm_allowed"], [10.0, 40.0])


if __name__ == "__main__":
    unittest.main()
