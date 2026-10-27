"""No hand watch during a run (user 2026-10-27: "completely remove this feature" -> "The whole hand watch"): a real-hand
run keeps every program rule and the hand check right before Start, but starts no watch and no live height following;
the page polls no watch."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class NoHandWatchTests(unittest.TestCase):
    def test_a_real_hand_run_starts_without_watch_or_following(self):
        import webui_server_v29 as v29
        job = {"id": "20261027-100000-job", "subject": "realhand",
               "steps": {"scan": {"name": "s.db3"}, "design": {"standoff_mm": 6}, "program": {"draw_feed_mm_min": 300}}}
        started, rules = [], []
        with patch.object(v29.v11, "job_load", return_value=job), patch.object(v29, "_z_home_check"), \
                patch.object(v29.v28, "machine_job_only"), \
                patch.object(v29.v28, "_real_rules", side_effect=lambda j, st: rules.append(st)), \
                patch.object(v29.v28, "hand_check", return_value={"median_dz_mm": 0.1, "share_off": 0.0,
                                                                  "seen_share": 0.9}) as check, \
                patch.dict(v29.v28._INNER, {"/api/machine/run": lambda p: started.append(p) or {"busy": "program"}}), \
                patch.object(v29.v28.WATCH, "start") as watch_start, patch.object(v29, "_log"), \
                patch.object(v29.machine, "status", return_value={"mpos": [0, 0, 0]}):
            v29.run({"job": job["id"], "confirm_clear": True, "confirm_estop": True})
        self.assertEqual(rules, [6])                                   # the real-hand program rules
        check.assert_called_once()                                      # the hand check right before Start
        self.assertEqual(len(started), 1)
        watch_start.assert_not_called()                                 # no watch while it runs
        self.assertEqual(v29.follow_view(), {"active": False, "dz_mm": None, "stats": None})
        self.assertFalse(hasattr(v29, "_arm_follow"))

    def test_the_page_polls_no_watch(self):
        with open(os.path.join(ROOT, "webui", "studio_v29.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertNotIn("api('/api/studio/watch')", page)
        self.assertNotIn("keeps watching", page)


if __name__ == "__main__":
    unittest.main()
