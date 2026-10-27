"""No air run anywhere (user 2026-10-27: "remove air run from every option"): the Start dialog has no air-run tick,
calibration approval no longer asks for one, the Admin page neither, and Studio refuses an air-run start from an old
page; Resume is the ordinary one."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class NoAirRunTests(unittest.TestCase):
    def test_pages_have_no_air_run(self):
        for name in ("studio_v29.html", "pc_admin.js"):
            with open(os.path.join(ROOT, "webui", name), encoding="utf-8") as f:
                text = f.read().lower()
            for word in ("air run", "air_run", "rairbox", "capair"):
                self.assertNotIn(word, text, f"{word} in {name}")

    def test_studio_refuses_an_air_run_start(self):
        import webui_server_v29 as v29
        job = {"id": "20261027-090000-job", "subject": "realhand", "steps": {"program": {}}}
        with patch.object(v29.v11, "job_load", return_value=job), patch.object(v29, "_z_home_check"), \
                patch.object(v29.v28, "run") as real_run:
            with self.assertRaisesRegex(ValueError, "Air runs were removed"):
                v29.run({"job": job["id"], "air_run": True, "confirm_air_run": True, "confirm_clear": True,
                         "confirm_estop": True})
        real_run.assert_not_called()
        for gone in ("_air_start", "air_check", "_empty_bed"):
            self.assertFalse(hasattr(v29, gone), gone)

    def test_resume_is_the_ordinary_one(self):
        import webui_server_v29 as v29
        with patch.object(v29, "_RESUME", (lambda p: {"resumed": True}, False)):
            self.assertEqual(v29.resume({}), {"resumed": True})

    def test_calibration_approval_needs_no_air_run_tick(self):
        import webui_server_v26 as v26
        with patch.object(v26, "_cal_mode", return_value={"id": "m"}), \
                patch.object(v26, "_session", side_effect=RuntimeError("past the ticks")):
            with self.assertRaisesRegex(RuntimeError, "past the ticks"):
                v26.approve({"approver": "Harsh", "confirm_dots": True, "confirm_touches": True})


if __name__ == "__main__":
    unittest.main()
