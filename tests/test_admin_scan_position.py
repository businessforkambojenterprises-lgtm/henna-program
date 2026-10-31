"""Admin -> Machine -> Scan position (user 2026-10-31: "also give option to edit move to scan in admin panel"): the
position is saved through the same two-file change as machine_scan_xy_set.py / machine_scan_z_set.py, only with the
machine disconnected, both numbers are checked before either file is written, and it needs an unlocked Admin session."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview.machines import Profiles  # noqa: E402

CAL = "machines/belt/calibration_test.json"


def load_pc():
    """webui_server_pc swaps v29's Handler on import - put it back afterwards for the other tests."""
    import webui_server_v29 as v29
    saved = v29.Handler
    import webui_server_pc as pc
    return pc, lambda: (setattr(v29, "Handler", saved), sys.modules.pop("webui_server_pc", None))


pc = None


class AdminScanPositionTests(unittest.TestCase):
    def setUp(self):
        global pc
        pc, undo = load_pc()
        self.addCleanup(undo)
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        os.makedirs(os.path.join(self.root, "machines", "belt"))
        motion = {"scan_pose_mpos": [110.2, 42.6, -40.0], "travel_z": -20.0, "firmware_z_floor": -189.0}
        reg = {"schema": "henna_machines/1", "machines": [
            {"id": "belt", "name": "Belt", "calibration": CAL, "work_area_mm": [225.0, 176.0], "motion": motion}]}
        with open(os.path.join(self.root, "machines", "registry.json"), "w", encoding="utf-8") as f:
            json.dump(reg, f)
        with open(os.path.join(self.root, CAL), "w", encoding="utf-8") as f:
            json.dump({"capture_pose_mpos": [110.2, 42.6, -40.0], "travel_z": -20.0, "firmware_z_floor": -189.0}, f)
        self.profiles = Profiles(self.root)
        self.profiles.set_active = None
        self.status = {"connected": False}
        for name, target in (("profiles", self.profiles),):
            p = patch.object(pc.v26, name, target)
            p.start()
            self.addCleanup(p.stop)
        self.profiles.active_id = lambda: "belt"
        self.profiles.active = lambda: self.profiles.get("belt")
        machine = MagicMock()
        machine.status.side_effect = lambda: self.status
        for p in (patch.object(pc.studio, "machine", machine), patch.object(pc.v26, "apply_active"),
                  patch.object(pc.v26, "_log")):
            p.start()
            self.addCleanup(p.stop)

    def pose(self):
        return self.profiles.motion(self.profiles.get("belt"))["scan_pose_mpos"]

    def test_saves_both_files_and_applies(self):
        r = pc.scan_position_save({"confirm": True, "x": "70.201", "y": "42.599", "z": "-170"})
        self.assertEqual(self.pose(), [70.201, 42.599, -170.0])
        cal = self.profiles.cal_of(self.profiles.get("belt"))
        self.assertEqual(cal["capture_pose_mpos"], [70.201, 42.599, -170.0])
        self.assertEqual(r["scan_pose"], [70.201, 42.599, -170.0])
        pc.v26.apply_active.assert_called_once()

    def test_refused_while_connected(self):
        self.status = {"connected": True}
        with self.assertRaises(pc.studio.MachineError):
            pc.scan_position_save({"confirm": True, "x": 70, "y": 42, "z": -170})
        self.assertEqual(self.pose(), [110.2, 42.6, -40.0])

    def test_bad_z_writes_nothing(self):
        for z in (-190, -10):                                   # below the floor / above travel Z
            with self.assertRaises(ValueError):
                pc.scan_position_save({"confirm": True, "x": 70, "y": 42, "z": z})
        with self.assertRaises(ValueError):                     # X outside the work area
            pc.scan_position_save({"confirm": True, "x": 300, "y": 42, "z": -100})
        self.assertEqual(self.pose(), [110.2, 42.6, -40.0])

    def test_needs_confirm_and_numbers(self):
        with self.assertRaises(ValueError):
            pc.scan_position_save({"x": 70, "y": 42, "z": -100})
        with self.assertRaises(ValueError):
            pc.scan_position_save({"confirm": True, "x": "abc", "y": 42, "z": -100})

    def test_view_gives_head_only_when_connected_homed_idle(self):
        self.assertIsNone(pc.scan_position_view()["head"])
        self.status = {"connected": True, "homed": True, "state": "Idle", "mpos": [1, 2, -3]}
        self.assertEqual(pc.scan_position_view()["head"], [1, 2, -3])

    def test_route_is_admin_only_and_the_page_script_posts_with_the_token(self):
        self.assertIn("/api/admin/scan_position", pc.ADMIN_PATHS)
        self.assertTrue(pc.needs_admin("/api/admin/scan_position"))
        with open(os.path.join(ROOT, "webui", "pc_admin.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("admin\/(paste_setup|scan_position)$", js)


if __name__ == "__main__":
    unittest.main()
