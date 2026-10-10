"""Studio v14 server rules: 4 mm minimum, no paper steps, and the run guard. Fake machine only; no hardware."""
import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server_v14 as server  # noqa: E402
from tests.test_grbl_program_link import PROGRAM  # noqa: E402

JOB, PLAN, PROG = "20261010-120000-job", "20261010-120001-plan", "20261010-120002-program"
GCODE = "DESIGN_HOVER_TEST_PROSTHETIC_20261010-120002.gcode"
ALL_TICKS = {"confirm_prosthetic": True, "confirm_unmoved": True, "confirm_clear": True, "confirm_estop": True}


class V14Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name
        self.enterContext(patch.object(server.base, "OUT_ROOT", self.root))
        self.log = self.enterContext(patch.object(server.v10, "log_row"))
        self.enterContext(patch.object(server.v10.camera, "status", return_value={"state": "off"}))
        self.machine = self.enterContext(patch.object(server, "machine"))
        self.machine.status.return_value = {"connected": True, "busy": None, "mpos": [1.0, 2.0, -40.0], "program": None}
        self.write_job()

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def save(self, rel, obj):
        os.makedirs(os.path.dirname(self.path(rel)), exist_ok=True)
        with open(self.path(rel), "w", encoding="utf-8") as f:
            json.dump(obj, f)

    def write_job(self, subject="prosthetic", standoff=4.0, program_text=PROGRAM, studio="v14", sha=None):
        os.makedirs(self.path(PROG), exist_ok=True)
        gfile = self.path(PROG, GCODE)
        with open(gfile, "w", encoding="ascii", newline="\n") as f:
            f.write(program_text)
        with open(gfile, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        self.save(f"{PLAN}/plan.json", {"config": {"standoff_mm": standoff}})
        self.save(f"{PROG}/program.json", {"program_file": gfile, "standoff_mm": standoff, "use_offset": False})
        self.save(f"{PROG}/step22_report.json", {"global_floor_in_program": -64.7})
        program = {"program": PROG, "program_file": gfile, "reread": {"lines": 15}, "estimated_run_time_s": 60,
                   "standoff_mm": standoff, "use_offset": False}
        if studio:
            program.update(studio=studio, sha256=sha or digest)
        self.save(f"{JOB}/job.json", {
            "id": JOB, "created": "2026-10-10T12:00:00", "subject": subject, "subject_text": subject, "steps": {
                "scan": {"name": "20261010_115900_000001.db3", "status_line": "<Idle|MPos:145.800,42.600,-40.000|FS:0,0>"},
                "hand": {}, "region": {"id": "r"}, "design": {"svg": "designs/lotus.svg", "standoff_mm": standoff},
                "plan": {"plan": PLAN}, "program": program}})

    def job(self):
        with open(self.path(JOB, "job.json"), encoding="utf-8") as f:
            return json.load(f)


class Rules(V14Base):
    def test_calibration_shown_with_4_mm_minimum_and_file_unchanged(self):
        self.assertEqual(server.calibration()["standoff_mm_allowed"], [4.0, 6.0])
        self.assertEqual(server.v10.cal()["standoff_mm_allowed"], [3.0, 6.0])

    def test_design_check_refuses_below_4_mm(self):
        with patch.object(server.v11, "check_design") as check:
            for value in (3.0, 3.5):
                with self.assertRaises(ValueError):
                    server.check_design({"job": JOB, "standoff_mm": value})
            check.assert_not_called()
            server.check_design({"job": JOB, "standoff_mm": 4.0, "svg": "designs/lotus.svg"})
            self.assertEqual(check.call_args[0][0]["standoff_mm"], 4.0)

    def test_program_refuses_a_plan_checked_below_4_mm(self):
        self.write_job(standoff=3.0)
        with patch.object(server.v10, "program") as write:
            with self.assertRaisesRegex(ValueError, "4 mm"):
                server.program({"job": JOB, "confirm_prosthetic": True, "confirm_unmoved": True})
            write.assert_not_called()

    def test_program_is_written_without_offset_and_fingerprinted(self):
        gfile = self.path(PROG, GCODE)
        fake = {"program": PROG, "program_file": gfile, "download": "/d", "preview": "/p", "reread": {"lines": 15},
                "standoff_mm": 4.0, "nozzle_z_range": [-63.6, -56.0], "estimated_run_time_s": 60}
        with patch.object(server.v10, "program", return_value=fake) as write:
            r = server.program({"job": JOB, "confirm_prosthetic": True, "confirm_unmoved": True, "use_offset": True})
        self.assertIs(write.call_args[0][0]["use_offset"], False)
        prog = r["job"]["steps"]["program"]
        self.assertEqual(prog["studio"], "v14")
        with open(gfile, "rb") as f:
            self.assertEqual(prog["sha256"], hashlib.sha256(f.read()).hexdigest())

    def test_paper_steps_are_gone(self):
        for path in ("/api/studio/offset", "/api/studio/skip_offset", "/api/studio/gapcheck"):
            fn, _ = server.Handler.POST_API[path]
            with self.assertRaises(ValueError):
                fn({"job": JOB})


class RunGuard(V14Base):
    def start(self, **params):
        return server.run_start(dict({"job": JOB}, **params))

    def assert_not_started(self):
        self.machine.start_program.assert_not_called()

    def test_all_four_confirmations_required(self):
        for missing in ALL_TICKS:
            with self.subTest(missing=missing):
                with self.assertRaises(ValueError):
                    self.start(**dict(ALL_TICKS, **{missing: False}))
        with self.assertRaises(ValueError):
            self.start(**{k: "true" for k in ALL_TICKS})
        self.assert_not_started()

    def test_person_job_never_runs(self):
        self.write_job(subject="person")
        with self.assertRaisesRegex(ValueError, "prosthetic"):
            self.start(**ALL_TICKS)
        self.assert_not_started()

    def test_older_ugs_program_must_be_rewritten(self):
        self.write_job(studio=None)
        with self.assertRaisesRegex(ValueError, "older Studio"):
            self.start(**ALL_TICKS)
        self.assert_not_started()

    def test_changed_file_refused(self):
        self.write_job(sha="0" * 64)
        with self.assertRaisesRegex(ValueError, "changed"):
            self.start(**ALL_TICKS)
        self.assert_not_started()

    def test_program_below_4_mm_refused(self):
        self.write_job(standoff=3.0)
        with self.assertRaisesRegex(ValueError, "4 mm"):
            self.start(**ALL_TICKS)
        self.assert_not_started()

    def test_non_prosthetic_header_refused(self):
        self.write_job(program_text=PROGRAM.replace("PROSTHETIC HAND ONLY", "ANY HAND"))
        with self.assertRaisesRegex(ValueError, "prosthetic"):
            self.start(**ALL_TICKS)
        self.assert_not_started()

    def test_forbidden_line_refused_even_with_matching_fingerprint(self):
        self.write_job(program_text=PROGRAM.replace("G90\n", "G90\nM3 S1000\n"))
        with self.assertRaises(server.v10.ProgramReadError):
            self.start(**ALL_TICKS)
        self.assert_not_started()

    def test_camera_recording_blocks_run(self):
        with patch.object(server.v10.camera, "status", return_value={"state": "recording"}):
            with self.assertRaises(server.MachineError):
                self.start(**ALL_TICKS)
        self.assert_not_started()

    def test_start_sends_checked_lines_and_records_the_result(self):
        r = self.start(**ALL_TICKS, x=0, command="M3")
        name, lines, end = self.machine.start_program.call_args[0]
        self.assertEqual(name, GCODE)
        self.assertEqual(lines[:2], ["G21", "G90"])
        self.assertFalse([s for s in lines if not s.startswith(("G21", "G90", "G53 G0 ", "G53 G1 "))])
        self.assertEqual(end, (95.415, 75.387, -20.0))
        self.assertEqual(r["job"]["runs"][-1]["result"], "running")
        self.assertEqual(self.job()["runs"][-1]["result"], "running")
        self.assertEqual(server.status_of(self.job()), "run")
        on_done = self.machine.start_program.call_args[1]["on_done"]
        self.machine.status.return_value = {"mpos": list(end), "program": {"state": "finished", "done": len(lines),
                                                                          "elapsed_s": 12.3}}
        on_done(None)
        run = self.job()["runs"][-1]
        self.assertEqual((run["result"], run["lines_done"], run["error"]), ("finished", len(lines), None))
        self.assertEqual(server.status_of(self.job()), "done")
        self.assertEqual([c[0][2] for c in self.log.call_args_list], ["Studio run start", "Studio run result"])

    def test_failed_run_is_recorded(self):
        self.start(**ALL_TICKS)
        on_done = self.machine.start_program.call_args[1]["on_done"]
        self.machine.status.return_value = {"mpos": None, "program": {"state": "stopped", "done": 3, "elapsed_s": 2}}
        on_done("STOP requested; home again before moving or recording")
        run = self.job()["runs"][-1]
        self.assertEqual(run["result"], "stopped")
        self.assertEqual(server.status_of(self.job()), "run")


class JogGuard(V14Base):
    def test_jog_needs_the_no_person_tick(self):
        for params in ({}, {"confirm_no_person": "true"}, {"confirm_no_person": False}):
            with self.assertRaises(ValueError):
                server.jog(dict(params, axis="Z", direction=1, step_mm=1))
        self.machine.start_jog.assert_not_called()

    def test_only_axis_direction_and_step_are_accepted(self):
        bad = [{"axis": "A", "direction": 1, "step_mm": 1}, {"axis": "Z", "direction": 2, "step_mm": 1},
               {"axis": "Z", "direction": True, "step_mm": 1}, {"axis": "Z", "direction": 1.0, "step_mm": 1},
               {"axis": "Z", "direction": -1, "step_mm": 5}, {"axis": "Z", "direction": -1, "step_mm": True},
               {"axis": "Z", "direction": -1, "step_mm": "1"}]
        for params in bad:
            with self.subTest(params=params):
                with self.assertRaises(ValueError):
                    server.jog(dict(params, confirm_no_person=True))
        self.machine.start_jog.assert_not_called()
        server.jog({"axis": "Z", "direction": -1, "step_mm": 0.1, "confirm_no_person": True, "z": -90, "command": "M3"})
        self.machine.start_jog.assert_called_once_with("Z", -1, 0.1)

    def test_recording_blocks_jog(self):
        with patch.object(server.v10.camera, "status", return_value={"state": "recording"}):
            with self.assertRaises(server.MachineError):
                server.jog({"axis": "Z", "direction": 1, "step_mm": 1, "confirm_no_person": True})
        self.machine.start_jog.assert_not_called()


class HttpGuard(V14Base):
    def setUp(self):
        super().setUp()
        self.srv = server.base.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = self.srv.server_address[1]
        self.enterContext(patch.object(server.base, "ALLOWED_HOSTS", {f"127.0.0.1:{port}"}))
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.port = port

    def post(self, path, body):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"{}")

    def test_job_changes_blocked_while_a_program_runs(self):
        self.machine.status.return_value = {"busy": "program"}
        self.machine.pause.return_value = {"busy": "program", "program": {"paused": True}}
        code, body = self.post("/api/studio/check_design", {"job": JOB, "standoff_mm": 4})
        self.assertEqual(code, 409)
        code, body = self.post("/api/machine/pause", {})
        self.assertEqual(code, 200)
        self.machine.pause.assert_called_once_with()

    def test_page_and_job_status(self):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        c.request("GET", "/")
        r = c.getresponse()
        page = r.read().decode("utf-8")
        self.assertEqual(r.status, 200)
        self.assertIn("5 · Run", page)
        self.assertNotIn("Surface check", page)
        c.request("GET", f"/api/studio/job?id={JOB}")
        r = c.getresponse()
        self.assertEqual(json.loads(r.read())["status"], "run")


if __name__ == "__main__":
    unittest.main()
