"""machine_scan_z_set.py (user 2026-10-24: "yes, scan Z -1"): the scan position's Z changes in the registry and the
approved calibration file together (Studio checks they agree), copies are kept, X/Y, travel Z and the floor stay, and
a scan Z above travel Z or at the floor is refused; the machine link builds its scan route from the new values."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_scan_z_set as tool  # noqa: E402
from henna_preview import machine_motion  # noqa: E402
from henna_preview.machines import Profiles  # noqa: E402

CAL = "machines/ball-screw/calibration_test.json"


class ScanZTests(unittest.TestCase):
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
        with open(os.path.join(self.root, CAL), "w", encoding="utf-8") as f:
            json.dump({"capture_pose_mpos": [65.7, 80.0, -5.0], "travel_z": -1.0, "firmware_z_floor": -40.0,
                       "xy_calibration": "x.json"}, f)
        self.profiles = Profiles(self.root)

    def test_both_files_change_together_and_the_route_follows(self):
        rec = tool.set_scan_z(self.profiles, "ball-screw", -1.0, stamp="20261024-143000")
        self.assertEqual((rec["scan_pose_before"], rec["scan_pose"]), ([65.7, 80.0, -5.0], [65.7, 80.0, -1.0]))
        m = self.profiles.get("ball-screw")
        self.assertEqual(m["motion"]["scan_pose_mpos"], [65.7, 80.0, -1.0])
        self.assertEqual((m["motion"]["travel_z"], m["motion"]["firmware_z_floor"]), (-1.0, -40.0))
        self.assertEqual(self.profiles.cal_of(m)["capture_pose_mpos"], [65.7, 80.0, -1.0])
        self.assertEqual(self.profiles.cal_of(m)["xy_calibration"], "x.json")
        self.profiles.check_consistent(m)
        hist = os.listdir(os.path.join(self.root, "machines", "history"))
        self.assertIn("calibration_test.before-scan-z-20261024-143000.json", hist)
        v = machine_motion.values_for(m["motion"], m["work_area_mm"])
        self.assertEqual(v["ROUTE"][-1], ("lower_to_capture", "$J=G21 G90 G53 Z-1.000 F300"))

    def test_refusals(self):
        mo = self.profiles.motion(self.profiles.get("ball-screw"))
        for bad, why in ((0.0, "at or below travel Z"), (-0.5, "at or below travel Z"), (-40.0, "above the Z floor")):
            with self.assertRaisesRegex(ValueError, why):
                tool.check_value(bad, mo)
        self.assertEqual(tool.check_value(-1.0, mo), -1.0)

    def test_dry_run_changes_nothing(self):
        saved = tool.ROOT
        tool.ROOT = self.root
        try:
            self.assertEqual(tool.main(["--machine", "ball-screw", "--scan-z", "-1"]), 0)
        finally:
            tool.ROOT = saved
        self.assertEqual(self.profiles.motion(self.profiles.get("ball-screw"))["scan_pose_mpos"], [65.7, 80.0, -5.0])


if __name__ == "__main__":
    unittest.main()
