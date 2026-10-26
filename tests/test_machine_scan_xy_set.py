"""machine_scan_xy_set.py (user 2026-10-26: "chnge scan position"): the scan position's X/Y change in the registry and
the approved calibration file together (Studio checks they agree), copies are kept, Z, travel Z and the floor stay,
a position outside the work area is refused; the machine link builds its scan route from the new values."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_scan_xy_set as tool  # noqa: E402
from henna_preview import machine_motion  # noqa: E402
from henna_preview.machines import Profiles  # noqa: E402

CAL = "machines/ball-screw/calibration_test.json"


class ScanXYTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "machines", "ball-screw"))
        motion = {"scan_pose_mpos": [65.7, 80.0, -0.5], "travel_z": -0.5, "firmware_z_floor": -49.0}
        reg = {"schema": "henna_machines/1", "machines": [
            {"id": "ball-screw", "name": "Ball screw (new machine)", "calibration": CAL, "work_area_mm": [225.0, 235.0],
             "motion": motion}]}
        with open(os.path.join(self.root, "machines", "registry.json"), "w", encoding="utf-8") as f:
            json.dump(reg, f)
        with open(os.path.join(self.root, CAL), "w", encoding="utf-8") as f:
            json.dump({"capture_pose_mpos": [65.7, 80.0, -0.5], "travel_z": -0.5, "firmware_z_floor": -49.0,
                       "xy_calibration": "x.json"}, f)
        self.profiles = Profiles(self.root)

    def test_both_files_change_together_and_the_route_follows(self):
        rec = tool.set_scan_xy(self.profiles, "ball-screw", 86.206, 235.0, stamp="20261026-130000")
        self.assertEqual(rec["scan_pose"], [86.206, 235.0, -0.5])
        m = self.profiles.get("ball-screw")
        self.assertEqual(m["motion"]["scan_pose_mpos"], [86.206, 235.0, -0.5])
        self.assertEqual((m["motion"]["travel_z"], m["motion"]["firmware_z_floor"]), (-0.5, -49.0))
        self.assertEqual(self.profiles.cal_of(m)["capture_pose_mpos"], [86.206, 235.0, -0.5])
        self.assertEqual(self.profiles.cal_of(m)["xy_calibration"], "x.json")
        self.profiles.check_consistent(m)
        hist = os.listdir(os.path.join(self.root, "machines", "history"))
        self.assertIn("calibration_test.before-scan-xy-20261026-130000.json", hist)
        self.assertIn("registry.before-scan-xy-20261026-130000.json", hist)
        v = machine_motion.values_for(m["motion"], m["work_area_mm"])
        self.assertIn("X86.206 Y235.000", " ".join(cmd for _, cmd in v["ROUTE"]))

    def test_outside_the_work_area_is_refused(self):
        for x, y in ((86.0, 235.5), (-1.0, 80.0), (226.0, 80.0)):
            with self.assertRaisesRegex(ValueError, "outside the work area"):
                tool.check_value(x, y, [225.0, 235.0])

    def test_dry_run_changes_nothing(self):
        saved = tool.ROOT
        tool.ROOT = self.root
        try:
            self.assertEqual(tool.main(["--machine", "ball-screw", "--x", "86.2", "--y", "235"]), 0)
        finally:
            tool.ROOT = saved
        self.assertEqual(self.profiles.motion(self.profiles.get("ball-screw"))["scan_pose_mpos"], [65.7, 80.0, -0.5])


if __name__ == "__main__":
    unittest.main()
