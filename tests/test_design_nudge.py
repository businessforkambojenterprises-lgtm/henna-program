"""Scan & start moves the design away from tall skin under the nozzle's path (user 2026-10-27: "build 3"): the same
design check at places up to NUDGE_STEPS_MM away from the reported tall point; the first that passes is used; other
refusals are not retried; never for the rest of a stopped run."""
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DESIGN = {"svg": "designs/x.svg", "fit_mm": 40, "standoff_mm": 4}
TALL = ("Under the nozzle's path (the nozzle's travel moves - to the first stroke and between strokes, 8 mm margin) "
        "the highest point is at Z -4.7 (about 77.8 mm above the bed), near machine X129 Y164; travel height ...")


class NudgeTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.tried = []

    def route(self, clear_beyond_mm):
        def check(p):
            c = np.array(p["centre_machine_xy"])
            self.tried.append(c)
            if np.hypot(*(c - np.array([129.0, 164.0]))) < clear_beyond_mm:
                raise self.v29.v27.tc.ClearanceError(TALL) if hasattr(self.v29.v27, "tc") else ValueError(TALL)
            return {"result": {"verdict": "PASS"}}
        return lambda path: check

    def test_moved_away_from_the_tall_point_until_it_passes(self):
        start = np.array([123.0, 146.0])                                  # 19 mm from the tall point
        with patch.object(self.v29, "_route", self.route(23.5)):              # clear from 23.5 mm: 5 mm needed
            r, n = self.v29.check_with_nudge("j", DESIGN, start, 0.0, True)
        self.assertEqual(r["result"]["verdict"], "PASS")
        self.assertEqual(n["mm"], 5.0)
        away = (np.array(n["centre"]) - start) / np.hypot(*(np.array(n["centre"]) - start))
        np.testing.assert_allclose(away, (start - [129, 164]) / np.hypot(*(start - [129, 164])), atol=0.01)
        self.assertEqual(n["away_from"], ["129", "164"])

    def test_no_nudge_when_it_passes_or_when_not_allowed(self):
        with patch.object(self.v29, "_route", self.route(0.0)):
            self.assertEqual(self.v29.check_with_nudge("j", DESIGN, [123.0, 146.0], 0.0, True)[1], None)
        self.tried.clear()
        with patch.object(self.v29, "_route", self.route(100.0)):
            with self.assertRaisesRegex(Exception, "Under the nozzle's path"):
                self.v29.check_with_nudge("j", DESIGN, [123.0, 146.0], 0.0, False)    # the rest of a stopped run
        self.assertEqual(len(self.tried), 1)

    def test_gives_up_after_the_last_step_and_other_refusals_are_not_retried(self):
        with patch.object(self.v29, "_route", self.route(100.0)):
            with self.assertRaisesRegex(Exception, "Under the nozzle's path"):
                self.v29.check_with_nudge("j", DESIGN, [123.0, 146.0], 0.0, True)
        self.assertEqual(len(self.tried), 1 + len(self.v29.NUDGE_STEPS_MM))
        self.tried.clear()

        def other(path):
            def f(p):
                self.tried.append(p)
                raise ValueError("A real hand needs the nozzle 1 mm or more above the skin")
            return f
        with patch.object(self.v29, "_route", other):
            with self.assertRaisesRegex(ValueError, "1 mm or more"):
                self.v29.check_with_nudge("j", DESIGN, [123.0, 146.0], 0.0, True)
        self.assertEqual(len(self.tried), 1)


if __name__ == "__main__":
    unittest.main()
