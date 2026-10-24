"""machine_z_home_moved.py (user 2026-10-24: "floor z-40 is also changed, i've changed position of limit switches" - the
Z switch moved up 9 mm, tip at Z 0 now 80 mm above the bed, was 71): the floor goes down with it in the registry and
the calibration together, the move and its time are recorded, travel Z and the scan position stay; Studio's bed
height takes the move into account and refuses designs, programs and runs from scans made before it."""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_z_home_moved as tool  # noqa: E402
from henna_preview.machines import Profiles  # noqa: E402

CAL = "machines/ball-screw/calibration_test.json"


class ZHomeMovedTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "machines", "ball-screw"))
        motion = {"scan_pose_mpos": [65.7, 80.0, -1.0], "travel_z": -0.5, "firmware_z_floor": -40.0}
        reg = {"schema": "henna_machines/1", "machines": [
            {"id": "ball-screw", "name": "Ball screw (new machine)", "calibration": CAL, "work_area_mm": [225.0, 176.0],
             "motion": motion}]}
        with open(os.path.join(self.root, "machines", "registry.json"), "w", encoding="utf-8") as f:
            json.dump(reg, f)
        with open(os.path.join(self.root, CAL), "w", encoding="utf-8") as f:
            json.dump({"capture_pose_mpos": [65.7, 80.0, -1.0], "travel_z": -0.5, "firmware_z_floor": -40.0,
                       "xy_calibration": "x.json"}, f)
        self.profiles = Profiles(self.root)

    def test_floor_moves_with_the_switch_and_the_move_is_recorded(self):
        now = dt.datetime(2026, 10, 24, 14, 40, 0)
        rec = tool.record_move(self.profiles, "ball-screw", 9.0, now=now)
        self.assertEqual((rec["floor_before"], rec["floor"], rec["total_moved_mm"]), (-40.0, -49.0, 9.0))
        m = self.profiles.get("ball-screw")
        self.assertEqual(m["motion"]["firmware_z_floor"], -49.0)
        self.assertEqual((m["motion"]["travel_z"], m["motion"]["scan_pose_mpos"]), (-0.5, [65.7, 80.0, -1.0]))
        c = self.profiles.cal_of(m)
        self.assertEqual((c["firmware_z_floor"], c["z_home_moved_mm"], c["z_home_moved_at"]),
                         (-49.0, 9.0, "2026-10-24T14:40:00"))
        self.profiles.check_consistent(m)
        self.assertTrue(any(h.startswith("calibration_test.before-z-home-") for h in
                            os.listdir(os.path.join(self.root, "machines", "history"))))
        rec2 = tool.record_move(self.profiles, "ball-screw", 2.0, now=now)          # a second move adds up
        self.assertEqual((rec2["floor"], rec2["total_moved_mm"]), (-51.0, 11.0))

    def test_refusals_and_dry_run(self):
        mo = self.profiles.motion(self.profiles.get("ball-screw"))
        for bad in (0.0, -3.0, 31.0):
            with self.assertRaises(ValueError):
                tool.new_floor(mo, bad)
        with self.assertRaisesRegex(ValueError, "below the scan position"):
            tool.new_floor(mo, 9.0, floor=-0.5)
        saved = tool.ROOT
        tool.ROOT = self.root
        try:
            self.assertEqual(tool.main(["--machine", "ball-screw", "--moved-up", "9"]), 0)
        finally:
            tool.ROOT = saved
        self.assertEqual(self.profiles.motion(self.profiles.get("ball-screw"))["firmware_z_floor"], -40.0)


class StudioTests(unittest.TestCase):
    def test_bed_height_and_old_scans(self):
        import webui_server_v27 as v27
        import webui_server_v29 as v29
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        xy = os.path.join(d, "xy.json")
        with open(xy, "w", encoding="utf-8") as f:
            json.dump({"inputs": {"head_pose_at_capture": {"z": -5.0}, "dots": [{"camera_mm": [0, 0, 210.0]}]}}, f)
        saved_root = v27.base.ROOT
        v27.base.ROOT = d
        try:
            self.assertAlmostEqual(v27._paper_z({"xy_calibration": "xy.json"}, 140.0), -75.0)
            self.assertAlmostEqual(v27._paper_z({"xy_calibration": "xy.json", "z_home_moved_mm": 9.0}, 140.0), -84.0)
        finally:
            v27.base.ROOT = saved_root
        saved_cal = v29.v10.cal
        v29.v10.cal = lambda: {"z_home_moved_at": "2026-10-24T14:40:00"}
        try:
            old = {"steps": {"scan": {"name": "20261024_140514_789315.db3"}}}
            new = {"steps": {"scan": {"name": "20261024_150000_123456.db3"}}}
            with self.assertRaisesRegex(ValueError, "before the Z home switch was moved"):
                v29._z_home_check(old)
            v29._z_home_check(new)
            v29._z_home_check({"steps": {}})                          # no scan yet: nothing to refuse
            v29.v10.cal = lambda: {}
            v29._z_home_check(old)                                    # never moved: as before
        finally:
            v29.v10.cal = saved_cal


if __name__ == "__main__":
    unittest.main()
