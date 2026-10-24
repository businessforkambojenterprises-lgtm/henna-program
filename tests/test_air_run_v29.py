"""Air run for a real-hand job (Studio v29, user 2026-10-22: "yes" to an "Air run (no hand)" option instead of removing
the hand check): the camera check is reversed - the design area must be EMPTY before Start, while running and before
Resume; a hand (or anything) there refuses / pauses; prosthetic jobs cannot use it; the normal check is unchanged."""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import hand_watch as hw  # noqa: E402


def skin(z=-30.0):
    xs, ys = np.meshgrid(np.arange(100, 130, 0.5), np.arange(90, 120, 0.5))
    return np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, z)])


TOOL = (65.7, 80.0, -5.0)                      # the tip far away at travel height


class ClearAreaTests(unittest.TestCase):
    def setUp(self):
        self.ref = hw.Reference(skin(-30.0))
        self.air = hw.air_reference(self.ref)

    def test_empty_area_passes_a_hand_refuses(self):
        r = hw.evaluate(self.air, skin(-60.0), TOOL)                  # bed 30 mm below the scanned skin
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(hw.message(r), "design area empty")
        r = hw.evaluate(self.air, skin(-30.0), TOOL)                  # the hand is still there
        self.assertEqual(r["verdict"], "moved")
        self.assertIn("something is in the design area", hw.message(r))
        r = hw.evaluate(self.air, skin(-35.0), TOOL)                  # only 5 mm lower: not clear (needs 10)
        self.assertEqual(r["verdict"], "moved")

    def test_a_small_object_in_the_area_refuses(self):
        live = skin(-60.0)
        m = (live[:, 0] < 104) & (live[:, 1] < 100)
        live[m, 2] = -28.0                                           # a finger tip: about 5 % of the area
        self.assertGreater(m.mean(), 1 - hw.CLEAR_SHARE)
        self.assertEqual(hw.evaluate(self.air, live, TOOL)["verdict"], "moved")

    def test_unseen_and_the_normal_check_unchanged(self):
        r = hw.evaluate(self.air, skin(-60.0)[:10], TOOL)
        self.assertEqual(r["verdict"], "unseen")
        self.assertIn("design area", hw.message(r))
        self.assertEqual(hw.evaluate(self.ref, skin(-30.0), TOOL)["verdict"], "ok")      # a hand run: hand there
        self.assertEqual(hw.evaluate(self.ref, skin(-60.0), TOOL)["verdict"], "moved")   # ... and hand gone
        self.assertFalse(getattr(self.ref, "expect_clear", False))


class StudioAirRunTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.saved = (v29.v11.job_load, v29.v28._real_rules, v29.air_check, v29.v28._INNER["/api/machine/run"],
                      v29.v28._INNER["/api/machine/resume"], v29.v28.WATCH.start, v29.v28.reference, v29._RESUME,
                      v29._log)
        self.calls = []
        # the jobs here are made up; the Z-home guard has its own tests (test_machine_z_home_moved.py)
        self.saved_zhome = v29._z_home_check
        v29._z_home_check = lambda job: None
        self.job = {"id": "20261022-120000-job", "subject": "realhand",
                    "steps": {"scan": {"name": "s.db3", "mpos": [65.7, 80, -5]}, "design": {"standoff_mm": 6},
                              "program": {"draw_feed_mm_min": 300}}}
        v29.v11.job_load = lambda jid: self.job
        v29.v28._real_rules = lambda job, st: self.calls.append(("rules", st))
        v29.air_check = lambda job, samples=3: self.calls.append(("air_check", samples)) or {
            "median_dz_mm": -31.0, "share_off": 0.0, "seen_share": 0.9}
        v29.v28._INNER["/api/machine/run"] = lambda p: self.calls.append(("run", p.get("air_run"))) or {"busy": "program"}
        v29.v28._INNER["/api/machine/resume"] = lambda p: self.calls.append(("resume",)) or {}
        v29.v28.WATCH.start = lambda job, ref: self.calls.append(("watch", getattr(ref, "expect_clear", False)))
        v29.v28.reference = lambda job: hw.Reference(skin(-30.0))
        v29._RESUME = (lambda p: self.calls.append(("v28 resume",)) or {}, False)
        v29._log = lambda *a, **k: None

    def tearDown(self):
        v29 = self.v29
        (v29.v11.job_load, v29.v28._real_rules, v29.air_check, v29.v28._INNER["/api/machine/run"],
         v29.v28._INNER["/api/machine/resume"], v29.v28.WATCH.start, v29.v28.reference, v29._RESUME,
         v29._log) = self.saved
        v29._z_home_check = self.saved_zhome
        with v29.v28.WATCH.lock:
            v29.v28.WATCH.state = {"active": False}

    def params(self, **kw):
        return dict({"job": self.job["id"], "confirm_clear": True, "confirm_estop": True, "air_run": True,
                     "confirm_air_run": True}, **kw)

    def test_air_start_checks_empty_and_watches_empty(self):
        out = self.v29.run(self.params())
        self.assertTrue(out["air_run"])
        self.assertEqual(self.calls, [("rules", 6), ("air_check", 3), ("run", True), ("watch", True)])
        self.assertTrue(self.v29.v28.WATCH.view()["air_run"])

    def test_refusals(self):
        with self.assertRaisesRegex(ValueError, "Confirm the air run"):
            self.v29.run(self.params(confirm_air_run=False))
        self.job["subject"] = "prosthetic"
        with self.assertRaisesRegex(ValueError, "for real-hand jobs"):
            self.v29.run(self.params())
        self.assertEqual(self.calls, [])

    def test_resume_after_a_watch_pause_rechecks_empty(self):
        v28 = self.v29.v28
        with v28.WATCH.lock:
            v28.WATCH.state = {"active": True, "job": self.job["id"], "paused_by_watch": "something is in the area",
                               "air_run": True}
        self.v29.resume({})
        self.assertEqual(self.calls, [("air_check", 2), ("resume",)])
        self.assertIsNone(v28.WATCH.view()["paused_by_watch"])
        with v28.WATCH.lock:
            v28.WATCH.state = {"active": True, "job": self.job["id"], "paused_by_watch": "hand moved"}
        self.v29.resume({})                                        # a hand run: v28's resume (looks for the hand)
        self.assertEqual(self.calls[-1], ("v28 resume",))

    def test_routes_and_page(self):
        v29 = self.v29
        self.assertIs(v29.Handler.POST_API["/api/machine/run"][0], v29.run)
        self.assertIs(v29.Handler.POST_API["/api/machine/resume"][0], v29.resume)
        with open(v29.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        self.assertIn('id="rair"', page)
        self.assertIn("air_run:true,confirm_air_run:true", page)


if __name__ == "__main__":
    unittest.main()
