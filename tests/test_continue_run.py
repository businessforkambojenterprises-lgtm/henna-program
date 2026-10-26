"""Scan & start and Re-scan and continue (user 2026-10-26: "whole process is too slow for someone to rest hand, thats
why hand moves" -> "build 1 and 2"): which stroke a stopped run stopped in, the rest of the design keeping its size
and place, and the two Studio actions (prepare on the new scan, then the Start button's own run)."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import mock_open, patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import continue_run as cr  # noqa: E402
from henna_preview.svg_design import SvgDesignError, place, read_svg  # noqa: E402

SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
<line x1="0" y1="0" x2="100" y2="0"/><line x1="0" y1="50" x2="60" y2="50"/><line x1="10" y1="100" x2="90" y2="100"/>
</svg>"""

LINES = ["G21", "G90", "G53 G0 Z-0.500",
         "G53 G0 X10.000 Y10.000", "G53 G1 X10.000 Y10.000 Z-10.000 F300", "M3 S1000", "G4 P0.2",
         "G53 G1 X20.000 Y10.000 Z-10.000 F300", "G53 G1 X30.000 Y10.000 Z-10.000 F300", "M5", "G53 G0 Z-5.000",
         "G53 G0 X10.000 Y20.000", "G53 G1 X10.000 Y20.000 Z-10.000 F300", "G53 G1 X20.000 Y20.000 Z-10.000 F300",
         "G53 G0 Z-5.000",
         "G53 G0 X10.000 Y30.000", "G53 G1 X10.000 Y30.000 Z-10.000 F300", "G53 G1 X25.000 Y30.000 Z-10.000 F300",
         "G53 G0 Z-0.500", "G53 G0 X65.700 Y150.000"]
START = [65.7, 150.0, -0.5]


class StrokeTests(unittest.TestCase):
    def test_strokes_are_the_drawing_runs_paste_lines_do_not_split(self):
        self.assertEqual(cr.strokes_of(LINES), [(4, 8), (12, 13), (16, 17)])

    def test_where_it_stopped(self):
        n = 3
        self.assertEqual(cr.stopped_stroke(LINES, [15.0, 10.0, -10.0], 9, START), (0, n, "redo"))   # half drawn
        self.assertEqual(cr.stopped_stroke(LINES, [20.0, 15.0, -5.0], 13, START), (1, n, "next"))   # on its way
        self.assertEqual(cr.stopped_stroke(LINES, [20.0, 20.0, -10.0], 15, START), (2, n, "next"))  # stroke 2 ended
        self.assertEqual(cr.stopped_stroke(LINES, [15.0, 30.0, -10.0], 18, START), (2, n, "redo"))
        self.assertEqual(cr.stopped_stroke(LINES, [45.35, 90.0, -0.5], 20, START), (3, n, "done"))   # going home
        self.assertEqual(cr.stopped_stroke(LINES, [37.85, 80.0, -0.5], 4, START), (0, n, "next"))    # first move

    def test_no_position_or_far_away_is_refused(self):
        with self.assertRaisesRegex(cr.ContinueError, "did not report"):
            cr.stopped_stroke(LINES, None, 9, START)
        with self.assertRaisesRegex(cr.ContinueError, "could not tell"):
            cr.stopped_stroke(LINES, [100.0, 100.0, -10.0], 9, START)


class RestTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)

    def _read(self, text):
        p = os.path.join(self.dir, f"d{len(os.listdir(self.dir))}.svg")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return read_svg(p)

    def test_the_rest_keeps_size_and_place(self):
        whole, _ = place(self._read(SVG), 50.0, (100.0, 100.0), 30.0)
        text, n, orig = cr.rest_svg(SVG, 1, "designs/x.svg")
        self.assertEqual((n, orig), (1, "designs/x.svg"))
        rest, _ = place(self._read(text), 50.0, (100.0, 100.0), 30.0)
        self.assertEqual(len(rest), 2)
        for a, b in zip(whole[1:], rest):
            np.testing.assert_allclose(a["plan"], b["plan"])

    def test_a_rest_of_a_rest_counts_from_the_original(self):
        t1, _, _ = cr.rest_svg(SVG, 1, "designs/x.svg")
        t2, n, orig = cr.rest_svg(t1, 1, "outputs/continue_designs/j-rest-from-2.svg")
        self.assertEqual((n, orig), (2, "designs/x.svg"))
        self.assertEqual(len(place(self._read(t2), 50.0, (0, 0))[0]), 1)
        self.assertEqual(cr.original_of(t2, "outputs/continue_designs/j-rest-from-3.svg"), "designs/x.svg")
        self.assertEqual(cr.original_of(SVG, "designs/x.svg"), "designs/x.svg")

    def test_nothing_left_is_refused(self):
        t, _, _ = cr.rest_svg(SVG, 3, "designs/x.svg")
        with self.assertRaises(SvgDesignError):
            self._read(t)


class StudioTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.job = {"id": "20261026-130229-job", "subject": "realhand", "steps": {}}
        self.src = {"landmarks": [], "outline_px": [], "program": {"draw_feed_mm_min": 300, "dispense": None},
                    "design": {"svg": "designs/x.svg", "fit_mm": 50, "rotation_deg": 0, "centre_machine_xy": [1, 2],
                               "standoff_mm": 6}}
        self.prep = {"followed": {"shift_mm": 1.2, "turn_deg": 0.1, "fit_mm": 0.4}, "done": ["scan", "program written"]}
        self.ticks = {"job": self.job["id"], "confirm_clear": True, "confirm_estop": True}
        p = patch.object(v29, "_log")
        p.start()
        self.addCleanup(p.stop)

    def _start(self, extra=()):
        v29 = self.v29
        ps = [patch.object(v29.v11, "job_load", return_value=dict(self.job, steps={"scan": {"name": "s"}})),
              patch.object(v29, "_placement_of", return_value=self.src),
              patch.object(v29, "_follow_and_prepare", return_value=self.prep),
              patch.object(v29, "run", return_value={"state": "Run", "job": {"id": self.job["id"]}}),
              patch.object(v29.machine, "status", return_value={"mpos": [0, 0, 0]})] + list(extra)
        mocks = [p.start() for p in ps]
        for p in ps:
            self.addCleanup(p.stop)
        return mocks

    def test_scan_and_start_prepares_then_starts(self):
        mocks = self._start()
        out = self.v29.scan_and_start(self.ticks)
        self.assertEqual(out["prepared"]["done"], self.prep["done"])
        mocks[2].assert_called_once()
        self.assertTrue(mocks[2].call_args.kwargs["write_program"])
        mocks[3].assert_called_once()
        self.assertTrue(all(mocks[3].call_args.args[0][k] for k in ("confirm_clear", "confirm_estop")))

    def test_scan_and_start_needs_the_ticks_and_no_air_run(self):
        with patch.object(self.v29.v11, "job_load", return_value=self.job):
            with self.assertRaisesRegex(ValueError, "emergency stop"):
                self.v29.scan_and_start({"job": self.job["id"]})
            with self.assertRaisesRegex(ValueError, "air run"):
                self.v29.scan_and_start(dict(self.ticks, air_run=True))

    def test_a_refused_start_says_what_was_prepared(self):
        self._start([patch.object(self.v29, "run", side_effect=ValueError("Hand check: the hand moved"))])
        with self.assertRaisesRegex(ValueError, "prepared .*program written.* refused: Hand check"):
            self.v29.scan_and_start(self.ticks)

    def test_continue_writes_the_rest_and_starts(self):
        d = tempfile.mkdtemp(dir=os.path.join(ROOT, "outputs"))
        self.addCleanup(shutil.rmtree, d)
        cp = {"run": "r1", "from": 1, "strokes": 3, "how": "redo", "base": 0, "total": 3, "svg_text": SVG}
        mocks = self._start([patch.object(self.v29, "CONT_DIR", d),
                             patch.object(self.v29, "continue_point", return_value=cp)])
        out = self.v29.rescan_continue(self.ticks)
        self.assertEqual(out["continued"], {"from_stroke": 2, "total": 3, "redo": True})
        src = mocks[2].call_args.args[1]
        path = os.path.join(self.v29.base.ROOT, src["design"]["svg"])
        self.assertTrue(os.path.samefile(os.path.dirname(path), d))
        with open(path, encoding="utf-8") as f:
            self.assertIn('data-henna-from-stroke="1"', f.read())
        mocks[3].assert_called_once()

    def test_continue_point_from_a_recorded_run(self):
        v29 = self.v29
        job = {"id": "j", "runs": [{"id": "r1", "result": "stopped", "program": "p1", "file": "f.gcode",
                                    "lines": len(LINES), "last_mpos": [20.0, 15.0, -5.0], "last_acked": 13}],
               "steps": {"program": {"program": "p1"}, "scan": {"mpos": START},
                         "design": {"svg": "designs/x.svg"}}}
        placed = [((10.0, 10.0), (30.0, 10.0)), ((10.0, 20.0), (20.0, 20.0)), ((10.0, 30.0), (25.0, 30.0))]
        with patch.object(v29, "_program_lines_of_run", return_value=LINES), \
                patch.object(v29, "design_strokes", return_value=placed), \
                patch("builtins.open", mock_open(read_data=SVG)):
            cp = v29.continue_point(job)
            self.assertEqual((cp["from"], cp["strokes"], cp["how"]), (1, 3, "next"))
            with patch.object(v29, "design_strokes", return_value=placed[:2]):
                with self.assertRaisesRegex(cr.ContinueError, "do not match"):
                    v29.continue_point(job)
        for change, why in (({"result": "finished"}, "finished"), ({"program": "p0"}, "written again")):
            bad = dict(job, runs=[dict(job["runs"][0], **change)])
            with self.assertRaisesRegex(cr.ContinueError, why):
                v29.continue_point(bad)


if __name__ == "__main__":
    unittest.main()
