"""Studio v29 (owner 2026-10-16: "yes do 2"): for a PROSTHETIC job the hand check before Start is a warning only -
the run starts, the numbers are logged and shown. Real-hand jobs keep v28's refusal; not connected still refuses."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

v29 = None                      # imported in setUp: importing v26+ at collection time changes v10.cal for older tests

TICKS = {"confirm_clear": True, "confirm_estop": True}


class ProstheticStartTests(unittest.TestCase):
    def setUp(self):
        global v29
        import webui_server_v29
        v29 = webui_server_v29
        self.saved = (v29.v28.hand_check, v29.v28._INNER["/api/machine/run"], v29.v28.machine_job_only, v29._log,
                      v29.v28.run)
        self.started, self.logs = [], []
        # the jobs here are made up; the Z-home guard has its own tests (test_machine_z_home_moved.py)
        self.saved_zhome = v29._z_home_check
        v29._z_home_check = lambda job: None
        v29.v28._INNER["/api/machine/run"] = lambda p: self.started.append(p) or {"busy": "program"}
        v29.v28.machine_job_only = lambda job: None
        v29._log = lambda *a: self.logs.append(a)

    def tearDown(self):
        (v29.v28.hand_check, v29.v28._INNER["/api/machine/run"], v29.v28.machine_job_only, v29._log,
         v29.v28.run) = self.saved
        v29._z_home_check = self.saved_zhome

    def job(self, subject="prosthetic"):
        return {"id": "20261016-120000-job", "subject": subject, "steps": {"scan": {"name": "rec.db3"}}}

    def moved(self, job, samples=3):
        raise ValueError("Hand check: the hand moved since the scan (median +1.5 mm, 25 % of the area more than "
                         "2 mm off). Scan again.")

    def test_moved_prosthetic_starts_with_a_warning(self):
        v29.v28.hand_check = self.moved
        out = v29._prosthetic_start(self.job(), dict(TICKS, job="j"))
        self.assertEqual(len(self.started), 1)
        self.assertIn("warning only, prosthetic", out["hand_check_warning"])
        self.assertIn("+1.5 mm", out["hand_check_warning"])
        self.assertIn("WARNING - started anyway", self.logs[-1][5])

    def test_good_check_has_no_warning(self):
        v29.v28.hand_check = lambda job, samples=3: {"median_dz_mm": 0.2, "share_off": 0.01, "seen_share": 0.9}
        out = v29._prosthetic_start(self.job(), dict(TICKS, job="j"))
        self.assertNotIn("hand_check_warning", out)
        self.assertEqual(self.logs[-1][5], "ok")

    def test_still_refused_without_ticks_or_connection(self):
        with self.assertRaisesRegex(ValueError, "emergency stop"):
            v29._prosthetic_start(self.job(), {"job": "j"})

        def offline(job, samples=3):
            raise v29.MachineError("Connect the machine first")
        v29.v28.hand_check = offline
        with self.assertRaises(v29.MachineError):
            v29._prosthetic_start(self.job(), dict(TICKS, job="j"))
        self.assertEqual(self.started, [])

    def test_real_hand_keeps_the_v28_refusal(self):
        calls = []
        v29.v28.run = lambda p: calls.append(p) or (_ for _ in ()).throw(ValueError("Hand check: moved"))
        saved_load = v29.v11.job_load
        v29.v11.job_load = lambda job_id: self.job("realhand")
        try:
            with self.assertRaisesRegex(ValueError, "Hand check: moved"):
                v29.run({"job": "j"})
        finally:
            v29.v11.job_load = saved_load
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.started, [])

    def test_page_shows_the_warning(self):
        with open(v29.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("hand_check_warning?'warn'", page)


if __name__ == "__main__":
    unittest.main()
