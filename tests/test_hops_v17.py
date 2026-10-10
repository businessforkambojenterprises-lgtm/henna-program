"""Low hops (v17): hop planning, independent hop check, re-read rules and the Studio v17 server. No hardware."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import hop_path as hp  # noqa: E402
from henna_preview.program_reread_v2 import ProgramReadError, program_lines_v2, reread_v2  # noqa: E402
from tests.test_studio_v14 import ALL_TICKS, JOB, V14Base  # noqa: E402

HOP_PROGRAM = "\n".join([
    "(DESIGN HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING)",
    "(between strokes: 1 of 1 low hops)",
    "G21", "G90",
    "G53 G0 Z-20.000",
    "G53 G0 X71.089 Y57.046",
    "G53 G1 X71.089 Y57.046 Z-56.052 F300",
    "G53 G1 X72.044 Y56.731 Z-56.043 F300",
    "G53 G1 X73.006 Y56.436 Z-56.048 F300",
    "G53 G0 Z-50.000",
    "G53 G0 X96.124 Y75.617",
    "G53 G1 X96.124 Y75.617 Z-63.547 F300",
    "G53 G1 X95.415 Y75.387 Z-63.664 F300",
    "G53 G0 Z-20.000", ""])
FLOOR = -64.7


def stroke(xy_z):
    return np.array([[x, y, z - 4.0, z, z - 3.0] for x, y, z in xy_z])


class HopPlanning(unittest.TestCase):
    def test_crossing_covers_the_band_and_both_ends(self):
        pts = hp.crossing_points((0.0, 0.0), (10.0, 0.0))
        self.assertAlmostEqual(pts[:, 1].min(), -1.5)
        self.assertAlmostEqual(pts[:, 1].max(), 1.5)
        self.assertAlmostEqual(pts[:, 0].min(), -1.5)          # band around the start
        self.assertAlmostEqual(pts[:, 0].max(), 11.5)          # and around the end

    def test_hop_is_highest_skin_plus_5(self):
        paths = [stroke([(0, 0, -36.0), (5, 0, -36.0)]), stroke([(10, 0, -37.0), (15, 0, -37.0)])]
        hops = hp.plan_hops(paths, lambda pts: -40.0, -20.0)
        self.assertEqual(hops[0]["z"], -35.0)
        self.assertTrue(hops[0]["low"])

    def test_hop_never_below_the_nozzle_at_either_end(self):
        paths = [stroke([(0, 0, -30.0)] * 2), stroke([(10, 0, -37.0)] * 2)]
        self.assertEqual(hp.plan_hops(paths, lambda pts: -40.0, -20.0)[0]["z"], -29.5)

    def test_unmeasured_crossing_or_high_hop_uses_travel_z(self):
        paths = [stroke([(0, 0, -36.0)] * 2), stroke([(10, 0, -37.0)] * 2)]
        self.assertEqual(hp.plan_hops(paths, lambda pts: None, -20.0)[0], {"z": -20.0, "low": False,
                                                                             "reason": "no measured skin along the crossing"})
        self.assertFalse(hp.plan_hops(paths, lambda pts: -25.5, -20.0)[0]["low"])   # -20.5 > travel - 1

    def test_verify_reads_hops_from_text(self):
        moves = hp.hop_moves_from_text(HOP_PROGRAM, -20.0)
        self.assertEqual(moves, [((73.006, 56.436), (96.124, 75.617), -50.0)])
        self.assertEqual(hp.verify_hops(HOP_PROGRAM, lambda pts: -55.0, -20.0)[0]["clearance_mm"], 5.0)
        with self.assertRaises(hp.HopError):
            hp.verify_hops(HOP_PROGRAM, lambda pts: -54.0, -20.0)        # only 4 mm above the skin
        with self.assertRaises(hp.HopError):
            hp.verify_hops(HOP_PROGRAM, lambda pts: None, -20.0)


class RereadV2(unittest.TestCase):
    def test_hop_program_accepted(self):
        r = reread_v2(HOP_PROGRAM, -20.0, FLOOR, 300.0)
        self.assertEqual((r["strokes"], r["low_hops"], r["hop_z_range"]), (2, 1, [-50.0, -50.0]))
        send, end = program_lines_v2(HOP_PROGRAM, -20.0, FLOOR, 300.0, -97.0)
        self.assertIn("G53 G0 Z-50.000", send)
        self.assertFalse([s for s in send if s.startswith("(")])
        self.assertEqual(end, (95.415, 75.387, -20.0))

    def test_old_style_program_still_accepted(self):
        old = HOP_PROGRAM.replace("G53 G0 Z-50.000", "G53 G0 Z-20.000")
        self.assertEqual(reread_v2(old, -20.0, FLOOR, 300.0)["low_hops"], 0)

    def test_refusals(self):
        cases = {
            "hop below the nozzle": HOP_PROGRAM.replace("G53 G0 Z-50.000", "G53 G0 Z-57.000"),
            "hop below the floor": HOP_PROGRAM.replace("G53 G0 Z-50.000", "G53 G0 Z-70.000"),
            "sideways without rising": HOP_PROGRAM.replace("G53 G0 Z-50.000\n", ""),
            "first sideways at a hop": HOP_PROGRAM.replace("G90\nG53 G0 Z-20.000", "G90\nG53 G0 Z-20.000\nG53 G0 Z-30.000"),
            "first move not travel": HOP_PROGRAM.replace("G90\nG53 G0 Z-20.000", "G90\nG53 G0 Z-30.000"),
            "end not at travel": HOP_PROGRAM.rsplit("G53 G0 Z-20.000", 1)[0],
            "forbidden line": HOP_PROGRAM.replace("G90\n", "G90\nM3 S1000\n"),
            "too fast": HOP_PROGRAM.replace("Z-63.547 F300", "Z-63.547 F900"),
        }
        for why, text in cases.items():
            with self.subTest(why):
                with self.assertRaises(ProgramReadError):
                    reread_v2(text, -20.0, FLOOR, 300.0)


import webui_server_v17 as server  # noqa: E402


class StudioV17(V14Base):
    def setUp(self):
        super().setUp()
        self.link = self.enterContext(patch.object(server.v16.v15, "machine"))
        self.link.status.return_value = {"connected": True, "busy": None, "mpos": [1.0, 2.0, -40.0], "program": None}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.writer_out = tmp.name

    def fake_step23(self, config_path):
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.cfg_written = cfg
        feed = int(cfg["draw_feed_mm_min"])
        with open(os.path.join(self.writer_out, "DESIGN_HOVER_TEST_PROSTHETIC_20261010-150000.gcode"), "w",
                  encoding="ascii", newline="\n") as f:
            f.write(HOP_PROGRAM.replace(" F300", f" F{feed}"))
        with open(os.path.join(self.writer_out, "report.json"), "w", encoding="utf-8") as f:
            json.dump({"global_floor_in_program": FLOOR, "estimated_run_time_s": 30.0, "hand_top": {},
                       "nozzle_z_range": [-63.664, -56.043],
                       "hops": {"clearance_mm": 5.0, "low": 1, "total": 1, "z_range": [-50.0, -50.0]}}, f)
        with open(os.path.join(self.writer_out, "design_hover_preview.png"), "wb") as f:
            f.write(b"png")
        return self.writer_out

    def program(self, **params):
        self.save("20261010-120001-plan/plan.json", {"config": {"standoff_mm": 4.0, "decided_by": "test"},
                                                      "recording": "20261010_115900_000001.db3"})
        with patch.object(server, "run_step23", side_effect=self.fake_step23):
            return server.program(dict({"job": JOB, "confirm_prosthetic": True, "confirm_unmoved": True}, **params))

    def test_program_with_hops_fast_drawing_slow_drops(self):
        prog = self.program(draw_feed_mm_min=1200)["job"]["steps"]["program"]
        self.assertEqual(self.cfg_written["hop_clearance_mm"], 5.0)
        self.assertEqual((prog["studio"], prog["draw_feed_mm_min"], prog["hops"]["low"]), ("v17", 1200, 1))
        with open(prog["program_file"], encoding="ascii") as f:
            text = f.read()
        feeds = [ln.rsplit(" F", 1)[1] for ln in text.splitlines() if ln.startswith("G53 G1")]
        self.assertEqual(feeds, ["300", "1200", "1200", "300", "1200"])
        self.assertIn("G53 G0 Z-50.000", text)
        with open(prog["program_file"], "rb") as f:
            self.assertEqual(prog["sha256"], hashlib.sha256(f.read()).hexdigest())

    def test_run_sends_the_hop_lines(self):
        self.program(draw_feed_mm_min=600)
        server.run_start(dict({"job": JOB}, **ALL_TICKS))
        name, lines, end = self.link.start_program.call_args[0]
        self.assertIn("G53 G0 Z-50.000", lines)
        self.assertEqual(end, (95.415, 75.387, -20.0))
        self.assertEqual(self.job()["runs"][-1]["low_hops"], 1)

    def test_older_programs_still_run(self):
        server.run_start(dict({"job": JOB}, **ALL_TICKS))                  # fixture: a v14 program
        self.link.start_program.assert_called_once()

    def test_hop_lowered_below_the_nozzle_refused_at_start(self):
        prog = self.program()["job"]["steps"]["program"]
        with open(prog["program_file"], encoding="ascii") as f:
            text = f.read().replace("G53 G0 Z-50.000", "G53 G0 Z-58.000")
        with open(prog["program_file"], "w", encoding="ascii", newline="\n") as f:
            f.write(text)
        j = self.job()
        with open(prog["program_file"], "rb") as f:
            j["steps"]["program"]["sha256"] = hashlib.sha256(f.read()).hexdigest()
        self.save(f"{JOB}/job.json", j)
        with self.assertRaises(ProgramReadError):
            server.run_start(dict({"job": JOB}, **ALL_TICKS))
        self.link.start_program.assert_not_called()

    def test_routes(self):
        self.assertIs(server.Handler.POST_API["/api/studio/program"][0], server.program)
        self.assertIs(server.Handler.POST_API["/api/machine/run"][0], server.run_start)
        self.assertEqual(server.Handler.server_version, "HennaStudio/0.17")


if __name__ == "__main__":
    unittest.main()
