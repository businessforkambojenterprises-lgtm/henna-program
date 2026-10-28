"""No hand watch during a run (user 2026-10-27: "completely remove this feature" -> "The whole hand watch"), but live
height following back without pauses ("live height correction ... this"): a real-hand run keeps every program rule and
the hand check right before Start, arms the following, starts the pause-free camera loop - never the pausing watch -
and nothing that loop sees holds the machine."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class NoHandWatchTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.job = {"id": "20261027-100000-job", "subject": "realhand",
                    "steps": {"scan": {"name": "s.db3"}, "design": {"standoff_mm": 6},
                              "program": {"draw_feed_mm_min": 300}}}

    def test_a_real_hand_run_follows_without_the_pausing_watch(self):
        v29, started, rules = self.v29, [], []
        with patch.object(v29.v11, "job_load", return_value=self.job), patch.object(v29, "_z_home_check"), \
                patch.object(v29.v28, "machine_job_only"), \
                patch.object(v29.v28, "_real_rules", side_effect=lambda j, st: rules.append(st)), \
                patch.object(v29.v28, "hand_check", return_value={"median_dz_mm": 0.1, "share_off": 0.0,
                                                                  "seen_share": 0.9}) as check, \
                patch.dict(v29.v28._INNER, {"/api/machine/run": lambda p: started.append(p) or {"busy": "program"}}), \
                patch.object(v29, "_arm_follow", return_value=object()) as arm, \
                patch.object(v29.v28, "reference", return_value="ref"), \
                patch.object(v29.v28.WATCH, "start") as pausing_watch, \
                patch.object(v29.FOLLOW_CAMERA, "start") as follow_camera, patch.object(v29, "_log"), \
                patch.object(v29.machine, "status", return_value={"mpos": [0, 0, 0]}):
            v29.run({"job": self.job["id"], "confirm_clear": True, "confirm_estop": True})
        self.assertEqual(rules, [6])                                   # the real-hand program rules
        check.assert_called_once()                                      # the hand check right before Start
        self.assertEqual(len(started), 1)
        arm.assert_called_once()                                        # live height following armed
        follow_camera.assert_called_once()                              # its pause-free camera loop
        pausing_watch.assert_not_called()                               # never the watch that pauses

    def test_the_follow_camera_never_pauses_the_machine(self):
        v29 = self.v29
        with patch.object(v29.machine, "pause") as pause:
            for reason in ("the skin came within 0.7 mm of the nozzle tip", "the hand moved since the scan",
                           "no camera picture for 1.5 s", "hand watch error: boom"):
                v29.FOLLOW_CAMERA._pause(self.job, reason, [0, 0, 0])
        pause.assert_not_called()
        self.assertEqual(v29.FOLLOW_CAMERA.state.get("last_reason"), "hand watch error: boom")

    def test_the_page_polls_no_watch(self):
        with open(os.path.join(ROOT, "webui", "studio_v29.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertNotIn("api('/api/studio/watch')", page)
        self.assertNotIn("keeps watching", page)
        self.assertIn("api('/api/studio/follow')", page)                 # the live view shows the following


if __name__ == "__main__":
    unittest.main()
