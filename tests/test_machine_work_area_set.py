"""machine_work_area_set.py (user 2026-10-24: "y can travel 42mm extra"): the measured work area goes into the registry
with how it was measured; sizes outside 50 mm .. the controller's travel are refused; and every X/Y check follows the
machine's work area - the link, both re-reads and the planners' stroke checks (nozzle_toolpath, dry_run), also in
the step scripts' own processes (apply_active_work_area)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_work_area_set as tool  # noqa: E402
from henna_preview import dry_run, machine_motion, nozzle_toolpath  # noqa: E402
from henna_preview import grbl_program_link as gpl  # noqa: E402
from henna_preview import program_reread as rr1  # noqa: E402
from henna_preview import program_reread_v2 as rr2  # noqa: E402
from henna_preview.machines import Profiles  # noqa: E402


class WorkAreaTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "machines"))
        reg = {"schema": "henna_machines/1", "active": "ball-screw", "machines": [
            {"id": "ball-screw", "name": "Ball screw (new machine)", "work_area_mm": [225.0, 176.0],
             "motion": {"scan_pose_mpos": [65.7, 80.0, -0.5], "travel_z": -0.5, "firmware_z_floor": -49.0}}]}
        with open(os.path.join(self.root, "machines", "registry.json"), "w", encoding="utf-8") as f:
            json.dump(reg, f)
        os.makedirs(os.path.join(self.root, "outputs"))
        with open(os.path.join(self.root, "outputs", "active_machine.json"), "w", encoding="utf-8") as f:
            json.dump({"id": "ball-screw"}, f)
        self.profiles = Profiles(self.root)
        self.saved = (gpl.X_MAX, gpl.Y_MAX, rr1.X_MAX, rr1.Y_MAX, rr2.X_MAX, rr2.Y_MAX,
                      dict(nozzle_toolpath.XY_LIMITS), dict(dry_run.XY_LIMITS))

    def tearDown(self):
        (gpl.X_MAX, gpl.Y_MAX, rr1.X_MAX, rr1.Y_MAX, rr2.X_MAX, rr2.Y_MAX,
         nozzle_toolpath.XY_LIMITS, dry_run.XY_LIMITS) = self.saved

    def test_measured_area_is_kept_with_how(self):
        rec = tool.set_work_area(self.profiles, "ball-screw", 225, 213, "Y +42 mm past 176, ruler, 5 mm margin",
                                 stamp="20261024-160000")
        self.assertEqual((rec["work_area_before"], rec["work_area"]), ([225.0, 176.0], [225.0, 213.0]))
        m = self.profiles.get("ball-screw")
        self.assertEqual(m["work_area_mm"], [225.0, 213.0])
        self.assertIn("MEASURED 20261024-160000", m["work_area_status"])
        self.assertTrue(any(h.startswith("registry_") for h in os.listdir(os.path.join(self.root, "machines", "history"))))

    def test_refusals(self):
        for x, y in ((225, 251), (401, 176), (40, 176)):
            with self.assertRaises(ValueError):
                tool.check_value(x, y)

    def test_every_xy_check_follows_the_machine(self):
        tool.set_work_area(self.profiles, "ball-screw", 225, 213, "test")
        self.assertEqual(machine_motion.apply_active_work_area(self.root), (225.0, 213.0))
        self.assertEqual((gpl.Y_MAX, rr1.Y_MAX, rr2.Y_MAX), (213.0, 213.0, 213.0))
        self.assertEqual(nozzle_toolpath.XY_LIMITS["y"], (0.0, 213.0))
        self.assertEqual(dry_run.XY_LIMITS["y"], (0.0, 213.0))
        self.assertIsNone(machine_motion.apply_active_work_area(os.path.join(self.root, "nothing")))


if __name__ == "__main__":
    unittest.main()
