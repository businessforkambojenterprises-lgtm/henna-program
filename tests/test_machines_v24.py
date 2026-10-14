"""Studio v24 machine profiles: names, one calibration per machine, locks for a machine without a calibration."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webui_server as base  # noqa: E402
import webui_server_v10 as v10  # noqa: E402

_saved = {}


def _temp_log(tmp):
    """A copy of the validation log's header only: tests must never write rows into the real log."""
    p = os.path.join(tmp, "validation_log_test.csv")
    with open(v10.LOG_FILE, encoding="utf-8") as f, open(p, "w", encoding="utf-8", newline="\n") as g:
        g.write(f.readline())
    return p


def _test_registry(tmp):
    """The real registry with the ball-screw machine back in calibration mode (as before its approval on 2026-10-14):
    these tests check the locks of an UNCALIBRATED machine and must not depend on the live machines' state."""
    with open(os.path.join(ROOT, "machines", "registry.json"), encoding="utf-8") as f:
        r = json.load(f)
    for m in r["machines"]:
        if m["id"] == "ball-screw":
            m["calibration"] = None
            m.pop("approved", None)
            m["calibration_mode"] = "test fixture: uncalibrated"
    os.makedirs(os.path.join(tmp, "machines"), exist_ok=True)
    p = os.path.join(tmp, "machines", "registry.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2)
    return p


def setUpModule():
    _saved.update(cal=v10.cal, log_row=v10.log_row, out_root=base.OUT_ROOT, log_file=v10.LOG_FILE)
    import webui_server_v24
    global v24
    v24 = webui_server_v24
    # this server's own state, set explicitly (another test module may have imported it first and undone it)
    v10.cal, v10.log_row = v24.profiles.active_cal, v24.log_row
    v24.machine._calibration = v24.profiles.active_cal
    _saved["state_path"], _saved["registry_path"] = v24.profiles.state_path, v24.profiles.registry_path
    _saved["tmp"] = tempfile.mkdtemp()
    v24.profiles.registry_path = _test_registry(_saved["tmp"])
    v10.LOG_FILE = _temp_log(_saved["tmp"])
    base.OUT_ROOT = os.path.join(_saved["tmp"], "webui")
    os.makedirs(base.OUT_ROOT)
    v24.profiles.state_path = os.path.join(_saved["tmp"], "active_machine.json")


def tearDownModule():
    v10.cal, v10.log_row, base.OUT_ROOT = _saved["cal"], _saved["log_row"], _saved["out_root"]
    v10.LOG_FILE, v24.profiles.state_path = _saved["log_file"], _saved["state_path"]
    v24.profiles.registry_path = _saved["registry_path"]
    v24.machine._calibration = _saved["cal"]
    shutil.rmtree(_saved["tmp"], True)


def choose(mid):
    v24.machines_select({"id": mid, "confirm_machine": True})


class MachineProfileTests(unittest.TestCase):
    def setUp(self):
        if os.path.exists(v24.profiles.state_path):
            os.remove(v24.profiles.state_path)
        self.logged = []
        self._real_log = v24._log_row
        v24._log_row = lambda *a, **k: self.logged.append((a, k))

    def tearDown(self):
        v24._log_row = self._real_log

    def test_registry_and_untouched_belt_calibration(self):
        names = {m["id"]: m for m in v24.profiles.machines()}
        self.assertEqual(set(names), {"belt-drive", "ball-screw"})
        self.assertIsNone(names["ball-screw"]["calibration"])
        with open(os.path.join(ROOT, "current_calibration.json"), encoding="utf-8") as f:
            original = json.load(f)
        choose("belt-drive")
        self.assertEqual(v10.cal(), original)
        self.assertEqual(v24.machine._calibration(), original)

    def test_no_machine_chosen_means_no_job_and_no_jog(self):
        with self.assertRaisesRegex(v24.MachineProfileError, "Choose the machine first"):
            v24.job_new({"subject": "prosthetic"})
        with self.assertRaises(v24.MachineProfileError):
            v24.jog({"axis": "Z", "direction": 1, "step_mm": 1})

    def test_choosing_needs_a_tick(self):
        with self.assertRaises(ValueError):
            v24.machines_select({"id": "belt-drive"})
        choose("ball-screw")
        self.assertEqual(v24.profiles.active_id(), "ball-screw")
        self.assertIn("Machine chosen", self.logged[-1][0][2])

    def test_uncalibrated_machine_is_locked(self):
        choose("ball-screw")
        with self.assertRaisesRegex(v24.MachineProfileError, "not calibrated yet"):
            v10.cal()
        job = v24.job_new({"subject": "prosthetic"})
        self.assertEqual(job["machine"], "ball-screw")
        for path in ("/api/machine/scan_position", "/api/studio/scan", "/api/studio/check_design",
                     "/api/studio/program", "/api/machine/run"):
            with self.assertRaisesRegex(v24.MachineProfileError, "not calibrated yet"):
                v24.Handler.POST_API[path][0]({"job": job["id"], "confirm_clear": True, "confirm_estop": True})
        for path in ("/api/calibration/scan_position", "/api/calibration/check", "/api/calibration/reference",
                     "/api/cam/record"):
            with self.assertRaisesRegex(v24.MachineProfileError, "not calibrated yet"):
                v24.Handler.POST_API[path][0]({"confirm_empty": True, "confirm_reference": True})
        self.assertTrue(v24.cal_status()["not_calibrated"])

    def test_jog_without_work_area_is_z_up_only(self):
        choose("ball-screw")
        calls = []
        real, real_machines = v24._JOG, v24.profiles.machines
        no_area = [dict(m, work_area_mm=None) if m["id"] == "ball-screw" else m for m in real_machines()]
        v24.profiles.machines = lambda: no_area                  # a machine whose work area is not known
        v24._JOG = lambda p: calls.append(p) or {"ok": True}
        try:
            for bad in ({"axis": "X", "direction": 1}, {"axis": "Y", "direction": -1}, {"axis": "Z", "direction": -1}):
                with self.assertRaisesRegex(v24.MachineProfileError, "only Z up"):
                    v24.jog(dict(bad, step_mm=1))
            v24.jog({"axis": "Z", "direction": 1, "step_mm": 1})
            self.assertEqual(len(calls), 1)
            v24.profiles.machines = real_machines
            choose("belt-drive")                         # measured work area: all axes go to the link's own limits
            v24.jog({"axis": "X", "direction": 1, "step_mm": 1})
            self.assertEqual(len(calls), 2)
        finally:
            v24._JOG, v24.profiles.machines = real, real_machines

    def test_jog_on_uncalibrated_machine_reaches_the_link(self):
        choose("ball-screw")
        seen = []
        real = v24._JOG
        v24._JOG = lambda p: seen.append(v24.machine._calibration()) or {"ok": True}
        try:
            v24.jog({"axis": "X", "direction": 1, "step_mm": 10})       # provisional work area: X/Y allowed
        finally:
            v24._JOG = real
        self.assertEqual(seen[0]["travel_z"], -20.0)
        self.assertEqual(seen[0]["capture_pose_mpos"], [145.8, 42.6, -40.0])
        with self.assertRaisesRegex(v24.MachineProfileError, "not calibrated"):
            v24.machine._calibration()                                   # restored: still refuses afterwards
        with self.assertRaisesRegex(v24.MachineProfileError, "not calibrated"):
            v10.cal()

    def test_job_of_another_machine_is_refused(self):
        choose("ball-screw")
        job = v24.job_new({"subject": "prosthetic"})
        choose("belt-drive")
        with self.assertRaisesRegex(v24.MachineProfileError, "made on Ball screw"):
            v24.Handler.POST_API["/api/studio/program"][0]({"job": job["id"]})
        old = v24.v11.job_new({"subject": "prosthetic"})     # a job from before v24: no machine
        with self.assertRaisesRegex(v24.MachineProfileError, "unknown machine"):
            v24._job_machine_check({"job": old["id"]}, strict=True)
        v24._job_machine_check({"job": old["id"]}, strict=False)   # camera-only steps stay possible

    def test_recording_without_machine_note_is_refused(self):
        choose("belt-drive")
        import webui_server_v2 as v2
        keep = v2.resolve_recording
        v2.resolve_recording = lambda p: os.path.join(_saved["tmp"], "old.db3")
        try:
            with self.assertRaisesRegex(v24.MachineProfileError, "not made on the Belt drive"):
                v24.use_recording({"path": "old.db3"})
        finally:
            v2.resolve_recording = keep

    def test_log_rows_name_the_machine(self):
        choose("ball-screw")
        self.logged.clear()
        v10.log_row("log.csv", 0, "step", None, "cmd", "exp", "meas", "res", "rec", "a note")
        v10.log_row("log.csv", 0, "step2")
        self.assertEqual(self.logged[0][0][9], "a note; machine ball-screw")
        self.assertEqual(self.logged[1][1]["notes"], "machine ball-screw")

    def test_page(self):
        with open(v24.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        for s in ('id="btnmachine"', 'id="mlist"', "function loadMachines", "/api/machines/select",
                  "AM.work_area_mm", "not_calibrated"):
            self.assertIn(s, page)


if __name__ == "__main__":
    unittest.main()
