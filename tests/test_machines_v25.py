"""Studio v25: per-machine motion values and calibration mode - the exact commands each machine receives."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from henna_preview import machine_motion  # noqa: E402  (captures the original belt-drive values first)
import webui_server as base  # noqa: E402
import webui_server_v10 as v10  # noqa: E402
from test_grbl_scan_link import FakeSerial  # noqa: E402

BELT_ROUTE = (("raise_to_travel", "$J=G21 G90 G53 Z-20.000 F300"),
              ("move_xy", "$J=G21 G90 G53 X145.800 Y42.600 F1000"),
              ("lower_to_capture", "$J=G21 G90 G53 Z-40.000 F300"))
_saved = {}


def setUpModule():
    _saved.update(cal=v10.cal, log_row=v10.log_row, out_root=base.OUT_ROOT, log_file=v10.LOG_FILE)
    global v25
    import webui_server_v25
    v25 = webui_server_v25
    v10.cal, v10.log_row = v25.cal, v25.v24.log_row                    # this server's own state, set explicitly
    v25.machine._calibration = v25.link_cfg
    _saved["state_path"], _saved["registry_path"] = v25.profiles.state_path, v25.profiles.registry_path
    _saved["tmp"] = tempfile.mkdtemp()
    from test_machines_v24 import _temp_log, _test_registry
    v25.profiles.registry_path = _test_registry(_saved["tmp"])
    v10.LOG_FILE = _temp_log(_saved["tmp"])
    base.OUT_ROOT = os.path.join(_saved["tmp"], "webui")
    os.makedirs(base.OUT_ROOT)
    v25.profiles.state_path = os.path.join(_saved["tmp"], "active_machine.json")
    v25.v24._log_row = lambda *a, **k: None


def tearDownModule():
    machine_motion.apply(machine_motion.BELT_DRIVE)
    v10.cal, v10.log_row, base.OUT_ROOT = _saved["cal"], _saved["log_row"], _saved["out_root"]
    v10.LOG_FILE, v25.profiles.state_path = _saved["log_file"], _saved["state_path"]
    v25.profiles.registry_path = _saved["registry_path"]
    v25.machine._calibration = _saved["cal"]
    shutil.rmtree(_saved["tmp"], True)


def choose(mid):
    v25.machines_select({"id": mid, "confirm_machine": True})


def wait_until(pred, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


class MotionValueTests(unittest.TestCase):
    def tearDown(self):
        machine_motion.apply(machine_motion.BELT_DRIVE)

    def test_belt_drive_values_are_exactly_the_old_code(self):
        self.assertEqual(machine_motion.BELT_DRIVE["ROUTE"], BELT_ROUTE)
        self.assertEqual(machine_motion.BELT_DRIVE["POSE"], (145.8, 42.6, -40.0))
        belt = v25.profiles.get("belt-drive")
        self.assertEqual(machine_motion.values_for(belt["motion"], belt["work_area_mm"]), machine_motion.BELT_DRIVE)
        v25.profiles.check_consistent(belt)                       # registry == current_calibration.json

    def test_bad_values_are_refused(self):
        with self.assertRaises(ValueError):
            machine_motion.values_for({"scan_pose_mpos": [80, 80, -10], "travel_z": -20, "firmware_z_floor": -76}, [225, 176])
        with self.assertRaises(ValueError):
            machine_motion.values_for({"scan_pose_mpos": [80, 80, -20], "travel_z": -20, "firmware_z_floor": -10}, [225, 176])

    def test_registry_mismatch_is_refused(self):
        belt = dict(v25.profiles.get("belt-drive"))
        belt["motion"] = dict(belt["motion"], scan_pose_mpos=[145.8, 42.6, -41.0])
        with self.assertRaisesRegex(v25.MachineProfileError, "disagree"):
            v25.profiles.check_consistent(belt)


class LinkCommandTests(unittest.TestCase):
    """Home + Move to scan position through a fake controller: the exact command list per machine."""

    def run_route(self, mid):
        choose(mid)
        from henna_preview.grbl_program_link_v22 import GrblProgramLinkV22
        fake = FakeSerial(state="Alarm")
        link = GrblProgramLinkV22(v25.link_cfg, opener=lambda p: fake, poll_s=0.01, fresh_s=0.3,
                                  homing_timeout=1.0, move_timeout=1.0, status_timeout=0.3)
        try:
            link.connect("FAKE", wait_s=0.5)
            link.start_homing()
            self.assertTrue(wait_until(lambda: link.status()["homed"]), link.status())
            link.start_scan_position()
            self.assertTrue(wait_until(lambda: link.status()["capture_ready"]), link.status())
            return fake.commands(), link.status()["mpos"]
        finally:
            if not link.status()["busy"]:
                link.disconnect()

    def tearDown(self):
        machine_motion.apply(machine_motion.BELT_DRIVE)

    def test_belt_drive_route_unchanged(self):
        cmds, pos = self.run_route("belt-drive")
        self.assertEqual(cmds, ["$HZ", "$H"] + [c for _, c in BELT_ROUTE])
        self.assertEqual(pos, [145.8, 42.6, -40.0])

    def test_ball_screw_route(self):
        cmds, pos = self.run_route("ball-screw")
        import json                                               # travel Z from the registry (-1 since 2026-10-22)
        with open(os.path.join(ROOT, "machines", "registry.json"), encoding="utf-8") as f:
            travel = next(m for m in json.load(f)["machines"] if m["id"] == "ball-screw")["motion"]["travel_z"]
        self.assertEqual(cmds, ["$HZ", "$H", f"$J=G21 G90 G53 Z{travel:.3f} F300",
                                "$J=G21 G90 G53 X65.700 Y80.000 F1000", "$J=G21 G90 G53 Z-5.000 F300"])
        self.assertEqual(pos, [65.7, 80.0, -5.0])


class CalibrationModeTests(unittest.TestCase):
    def tearDown(self):
        machine_motion.apply(machine_motion.BELT_DRIVE)

    def test_ball_screw_scans_but_no_programs(self):
        choose("ball-screw")
        c = v10.cal()
        self.assertEqual(c["capture_pose_mpos"], [65.7, 80.0, -5.0])
        self.assertNotIn("xy_calibration", c)
        job = v25.v24.job_new({"subject": "prosthetic"})
        ok = []
        for path in sorted(v25.SCAN_JOB):
            v25.guarded(path, lambda p, path=path: ok.append(path))({"job": job["id"]})
        for path in sorted(v25.SCAN_ONLY):
            v25.guarded(path, lambda p, path=path: ok.append(path))({})
        self.assertEqual(len(ok), len(v25.SCAN_JOB) + len(v25.SCAN_ONLY))
        for path in sorted(v25.FULL_CAL_JOB):
            with self.assertRaisesRegex(v25.MachineProfileError, "not calibrated"):
                v25.guarded(path, lambda p: None)({"job": job["id"]})
        for path in sorted(v25.FULL_CAL_ONLY):
            with self.assertRaisesRegex(v25.MachineProfileError, "not calibrated"):
                v25.guarded(path, lambda p: None)({})
        st = v25.cal_status()
        self.assertTrue(st["not_calibrated"] and st["calibration_mode"])

    def test_belt_drive_full_calibration(self):
        choose("belt-drive")
        with open(os.path.join(ROOT, "current_calibration.json"), encoding="utf-8") as f:
            self.assertEqual(v10.cal(), json.load(f))
        job = v25.v24.job_new({"subject": "prosthetic"})
        ran = []
        for path in sorted(v25.FULL_CAL_JOB | v25.SCAN_JOB):
            v25.guarded(path, lambda p, path=path: ran.append(path))({"job": job["id"]})
        self.assertEqual(len(ran), len(v25.FULL_CAL_JOB | v25.SCAN_JOB))

    def test_page(self):
        with open(v25.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("calibration mode", page)
        self.assertIn("CALIBRATION MODE", page)


if __name__ == "__main__":
    unittest.main()
