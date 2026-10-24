"""Setups (Studio v29, user 2026-10-22: "this process takes long time, we need to make it faster" -> "yes"): a job whose
design passed is saved as a named setup; a new job from it needs only Move to scan position, the hand, Scan & prepare
(scan, hand, outline + design moved onto this hand, design check, program - Quick re-scan's steps), Start."""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from henna_preview import follow_hand as fh  # noqa: E402
from test_quick_rescan_v29 import PTS, lm, moved  # noqa: E402


class SetupTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.tmp = tempfile.mkdtemp(prefix="setups_")
        self.calls = []
        self.jobs = {"j1": {"id": "j1", "subject": "realhand", "machine": "ball-screw", "steps": {
            "scan": {"name": "a.db3", "mpos": [65.7, 80.0, -5.0]}, "hand": {"run": "web:h1"},
            "region": {"polygon_px": [[100, 100], [200, 100], [200, 200], [100, 200]]},
            "design": {"svg": "designs/arabic_finger_bel.svg", "fit_mm": 25.0, "rotation_deg": 10.0,
                       "centre_machine_xy": [110.0, 140.0], "standoff_mm": 6.0, "verdict": "PASS"},
            "program": {"program": "p1", "draw_feed_mm_min": 250.0,
                        "dispense": {"dispenser": "servo", "start_s": 0.4, "stop_s": 0.2, "label": "Servo",
                                     "file": "x.gcode", "check": {"strokes": 9}}}}}}
        reports = {"h1": PTS, "h2": moved(PTS, -4.0, 5.0, 2.0)}
        self.saved = (v29.SETUPS_DIR, v29.v11.job_load, v29.v11.job_save, v29._route, v29._hand_report,
                      v29.machine.status, v29._log, v29.v28._parts, fh.landmarks, v29.v14.job_view)
        v29.SETUPS_DIR = self.tmp
        v29.v11.job_load = lambda jid: self.jobs[jid]
        v29.v11.job_save = lambda job: self.jobs.__setitem__(job["id"], job) or job
        v29._hand_report = lambda job: {"run": job["steps"]["hand"]["run"].split(":")[1],
                                        "colour_intrinsics": {"width": 848, "height": 480}}
        fh.landmarks = lambda rep, head, R, t: lm(reports[rep["run"]], reports[rep["run"]])
        self.ready = True
        v29.machine.status = lambda: {"capture_ready": self.ready, "busy": "", "mpos": [65.7, 80.0, -5.0]}
        v29._log = lambda *a, **k: None
        v29.v28._parts = lambda: {"R": np.eye(2), "t": np.zeros(2)}
        v29.v14.job_view = lambda job: job

        def route(path):
            def fn(p):
                self.calls.append((path, p))
                if path.endswith("/job_new"):
                    self.jobs["j2"] = {"id": "j2", "subject": p["subject"], "steps": {}}
                    return self.jobs["j2"]
                if path.endswith("/scan"):
                    self.jobs[p["job"]]["steps"]["scan"] = {"name": "b.db3", "mpos": [65.7, 80.0, -5.0]}
                if path.endswith("/detect"):
                    self.jobs[p["job"]]["steps"]["hand"] = {"run": "web:h2"}
                if path.endswith("/check_design"):
                    return {"result": {"verdict": "PASS"}}
                return {}
            return fn
        v29._route = route

    def tearDown(self):
        v29 = self.v29
        (v29.SETUPS_DIR, v29.v11.job_load, v29.v11.job_save, v29._route, v29._hand_report, v29.machine.status,
         v29._log, v29.v28._parts, fh.landmarks, v29.v14.job_view) = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def save(self, name="Arabic bel - left hand"):
        return self.v29.setup_save({"job": "j1", "name": name})

    def test_save_keeps_the_placement_and_lists_it(self):
        out = self.save()
        sid = out["saved"]
        self.assertTrue(sid.startswith("arabic-bel-left-hand-"))
        with open(os.path.join(self.tmp, sid + ".json"), encoding="utf-8") as f:
            rec = json.load(f)
        self.assertEqual(rec["design"]["fit_mm"], 25.0)
        self.assertEqual(rec["outline_px"][0], [100, 100])
        self.assertEqual(rec["program"]["draw_feed_mm_min"], 250.0)
        self.assertEqual(rec["program"]["dispense"], {"dispenser": "servo", "start_s": 0.4, "stop_s": 0.2,
                                                      "label": "Servo"})       # settings only, not the old file
        self.assertEqual((rec["machine"], rec["subject"]), ("ball-screw", "realhand"))
        self.assertEqual(len(rec["landmarks"]), 8)
        self.assertEqual([s["id"] for s in self.v29.setups_list()["setups"]], [sid])
        self.assertEqual(out["setups"][0]["paste"], "Servo")

    def test_save_needs_a_passed_design_and_a_name(self):
        with self.assertRaisesRegex(ValueError, "name"):
            self.save(" ")
        self.jobs["j1"]["steps"]["design"]["verdict"] = "REFUSED"
        with self.assertRaisesRegex(ValueError, "not passed"):
            self.save()
        self.jobs["j1"]["subject"] = "person"
        with self.assertRaisesRegex(ValueError, "prosthetic and real-hand"):
            self.save()

    def test_new_job_then_scan_and_prepare_moves_the_design_with_the_hand(self):
        sid = self.save()["saved"]
        saved_machine = self.v29.v28.v26.profiles.active_id
        self.v29.v28.v26.profiles.active_id = lambda: "ball-screw"
        try:
            job = self.v29.setup_job({"setup": sid})["job"]
        finally:
            self.v29.v28.v26.profiles.active_id = saved_machine
        self.assertEqual(job["setup"], {"id": sid, "name": "Arabic bel - left hand"})
        self.assertEqual(self.calls[0], ("/api/studio/job_new", {"subject": "realhand"}))
        self.calls.clear()
        out = self.v29.setup_prepare({"job": "j2", "confirm_clear": True, "confirm_estop": True})
        self.assertEqual([c[0] for c in self.calls], ["/api/studio/scan", "/api/studio/detect",
                                                      "/api/studio/region_accept", "/api/studio/check_design",
                                                      "/api/studio/program"])
        np.testing.assert_allclose(self.calls[2][1]["polygon"], moved([[100, 100], [200, 100], [200, 200], [100, 200]],
                                                                      -4.0, 5.0, 2.0), atol=0.1)
        d = self.calls[3][1]
        self.assertAlmostEqual(d["rotation_deg"], 6.0, places=1)
        np.testing.assert_allclose(d["centre_machine_xy"], moved([[110.0, 140.0]], -4.0, 5.0, 2.0)[0], atol=0.01)
        pp = self.calls[4][1]
        self.assertEqual((pp["draw_feed_mm_min"], pp["dispenser"], pp["start_delay_s"], pp["stop_delay_s"]),
                         (250.0, "servo", 0.4, 0.2))
        self.assertIn("program written", out["done"])

    def test_prepare_never_moves_the_head_and_needs_the_ticks(self):
        sid = self.save()["saved"]
        self.jobs["j2"] = {"id": "j2", "subject": "realhand", "steps": {}, "setup": {"id": sid, "name": "x"}}
        with self.assertRaisesRegex(ValueError, "emergency stop"):
            self.v29.setup_prepare({"job": "j2"})
        self.ready = False                                        # the hand is in: the way there is not known
        with self.assertRaisesRegex(ValueError, "not at the scan position"):
            self.v29.setup_prepare({"job": "j2", "confirm_clear": True, "confirm_estop": True})
        self.assertEqual(self.calls, [])
        with self.assertRaisesRegex(ValueError, "not made from a setup"):
            self.v29.setup_prepare({"job": "j1", "confirm_clear": True, "confirm_estop": True})

    def test_other_machine_bad_ids_and_delete(self):
        sid = self.save()["saved"]
        saved_machine = self.v29.v28.v26.profiles.active_id
        self.v29.v28.v26.profiles.active_id = lambda: "belt"
        try:
            with self.assertRaisesRegex(ValueError, "choose that machine first"):
                self.v29.setup_job({"setup": sid})
        finally:
            self.v29.v28.v26.profiles.active_id = saved_machine
        for bad in ("../registry", "", None, "A b"):
            with self.assertRaisesRegex(ValueError, "unknown setup"):
                self.v29.setup_job({"setup": bad})
        with self.assertRaisesRegex(ValueError, "confirm"):
            self.v29.setup_delete({"id": sid})
        self.assertEqual(self.v29.setup_delete({"id": sid, "confirm": True})["setups"], [])

    def test_routes_and_page(self):
        H = self.v29.Handler
        self.assertIs(H.GET_API["/api/studio/setups"], self.v29.setups_list)
        for path, fn, lock in (("setup_save", self.v29.setup_save, True), ("setup_delete", self.v29.setup_delete, False),
                               ("setup_job", self.v29.setup_job, False),
                               ("setup_prepare", self.v29.setup_prepare, True)):
            self.assertEqual(H.POST_API["/api/studio/" + path], (fn, lock))
        with open(os.path.join(ROOT, "webui", "studio_v29.html"), encoding="utf-8") as f:
            page = f.read()
        for bit in ('id="setups"', 'id="scanprep"', 'id="savesetcard"', "/api/studio/setup_prepare",
                    "/api/studio/setup_job", "/api/studio/setup_save", "/api/studio/setup_delete"):
            self.assertIn(bit, page)
        ids = re.findall(r'\bid="([^"]+)"', page)
        self.assertEqual([i for i in set(ids) if ids.count(i) > 1], [])   # "setupcard" was the machine setup's


if __name__ == "__main__":
    unittest.main()
