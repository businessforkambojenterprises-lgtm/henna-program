"""Studio v16: drawing speed per program, drops to the hand capped at 300. Fake writer and machine; no hardware."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server_v16 as server  # noqa: E402
from tests.test_grbl_program_link import PROGRAM  # noqa: E402
from tests.test_studio_v14 import ALL_TICKS, GCODE, JOB, PROG, V14Base  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_pristine  # noqa: E402

studio_pristine.remember()           # the older modules as loaded, before any v24+ test imports a later Studio


def setUpModule():
    global _PRISTINE
    _PRISTINE = studio_pristine.apply()


def tearDownModule():
    studio_pristine.undo(_PRISTINE)


def with_feed(text, feed):
    return text.replace(" F300", f" F{feed}")


class V16Base(V14Base):
    def setUp(self):
        super().setUp()
        self.link = self.enterContext(patch.object(server.v15, "machine"))
        self.link.status.return_value = {"connected": True, "busy": None, "mpos": [1.0, 2.0, -40.0], "program": None}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.writer_out = tmp.name

    def fake_step22(self, config_path, write):
        with open(config_path, encoding="utf-8") as f:
            feed = int(json.load(f)["draw_feed_mm_min"])
        with open(os.path.join(self.writer_out, "DESIGN_HOVER_TEST_PROSTHETIC_20261010-130000.gcode"), "w",
                  encoding="ascii", newline="\n") as f:
            f.write(with_feed(PROGRAM, feed))
        with open(os.path.join(self.writer_out, "report.json"), "w", encoding="utf-8") as f:
            json.dump({"global_floor_in_program": -64.7, "estimated_run_time_s": 60.0,
                       "nozzle_z_range": [-63.664, -56.043], "hand_top": {}}, f)
        with open(os.path.join(self.writer_out, "design_hover_preview.png"), "wb") as f:
            f.write(b"png")
        self.feed_written = feed
        return self.writer_out

    def write_plan(self, standoff=4.0):
        self.save("20261010-120001-plan/plan.json", {"config": {"standoff_mm": standoff, "decided_by": "test"},
                                                      "recording": "20261010_115900_000001.db3"})

    def program(self, **params):
        self.write_plan(params.pop("standoff", 4.0))
        with patch.object(server.v10, "run_step22", side_effect=self.fake_step22):
            return server.program(dict({"job": JOB, "confirm_prosthetic": True, "confirm_unmoved": True}, **params))

    def program_text(self, job):
        with open(job["steps"]["program"]["program_file"], encoding="ascii") as f:
            return f.read()


class SpeedChoice(V16Base):
    def test_rules(self):
        r = server.v16_rules()
        self.assertEqual(r["default_feed_mm_min"], 600)
        self.assertEqual((min(r["feeds_mm_min"]), max(r["feeds_mm_min"])), (300, 1200))
        self.assertEqual(r["descent_max_mm_min"], 300)
        self.assertEqual(server.v10.cal()["draw_feed_mm_min"], 300.0)        # calibration file unchanged

    def test_only_offered_speeds(self):
        for bad in (250, 350, 1300, 0, -300, True, "300", None, 600.5):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    server.draw_feed({"draw_feed_mm_min": bad})
        self.assertEqual(server.draw_feed({}), 600)
        self.assertEqual(server.draw_feed({"draw_feed_mm_min": 1200}), 1200)

    def test_600_draws_fast_but_drops_at_300(self):
        r = self.program(draw_feed_mm_min=600)
        self.assertEqual(self.feed_written, 600)
        prog = r["job"]["steps"]["program"]
        self.assertEqual((prog["studio"], prog["draw_feed_mm_min"], prog["descent_feed_mm_min"]), ("v16", 600, 300))
        text = self.program_text(r["job"])
        self.assertTrue(text.startswith(server.v14.HEADER))
        g1 = [ln for ln in text.splitlines() if ln.startswith("G53 G1")]
        self.assertEqual([ln.rsplit(" F", 1)[1] for ln in g1], ["300", "600", "600", "300", "600"])
        self.assertIn("(drawing speed 600 mm/min - drop to the hand at 300 mm/min or slower)", text)
        with open(prog["program_file"], "rb") as f:
            self.assertEqual(prog["sha256"], hashlib.sha256(f.read()).hexdigest())
        self.assertGreater(prog["estimated_run_time_s"], 60.0)              # slower drops are counted
        with open(os.path.join(os.path.dirname(prog["program_file"]), "config.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual(cfg["draw_feed_mm_min"], 600.0)

    def test_slower_speed_drops_at_that_speed(self):
        text = self.program_text(self.program(draw_feed_mm_min=300)["job"])
        self.assertEqual({ln.rsplit(" F", 1)[1] for ln in text.splitlines() if ln.startswith("G53 G1")}, {"300"})

    def test_1200_draws_at_1200_and_drops_at_300(self):
        text = self.program_text(self.program(draw_feed_mm_min=1200)["job"])
        g1 = [ln.rsplit(" F", 1)[1] for ln in text.splitlines() if ln.startswith("G53 G1")]
        self.assertEqual(g1, ["300", "1200", "1200", "300", "1200"])

    def test_default_is_600(self):
        self.assertEqual(self.program()["job"]["steps"]["program"]["draw_feed_mm_min"], 600)

    def test_refusals_before_writing(self):
        with patch.object(server.v10, "run_step22") as write:
            for params in ({"draw_feed_mm_min": 1300}, {"draw_feed_mm_min": 200}, {"confirm_unmoved": False}):
                with self.subTest(params=params):
                    self.write_plan()
                    with self.assertRaises(ValueError):
                        server.program(dict({"job": JOB, "confirm_prosthetic": True, "confirm_unmoved": True}, **params))
            self.write_plan(3.0)
            with self.assertRaisesRegex(ValueError, "4 mm"):
                server.program({"job": JOB, "confirm_prosthetic": True, "confirm_unmoved": True})
            write.assert_not_called()


class RunChecks(V16Base):
    def tamper(self, job, text):
        """Replace the program text and its fingerprint, as if the file had been written that way."""
        path = job["steps"]["program"]["program_file"]
        with open(path, "w", encoding="ascii", newline="\n") as f:
            f.write(text)
        j = self.job()
        with open(path, "rb") as f:
            j["steps"]["program"]["sha256"] = hashlib.sha256(f.read()).hexdigest()
        self.save(f"{JOB}/job.json", j)
        return j

    def test_600_program_runs_with_its_own_limit(self):
        self.program(draw_feed_mm_min=600)
        r = server.run_start(dict({"job": JOB}, **ALL_TICKS))
        name, lines, end = self.link.start_program.call_args[0]
        self.assertTrue(any(ln.endswith("F600") for ln in lines))
        self.assertEqual(self.job()["runs"][-1]["draw_feed_mm_min"], 600)
        self.assertEqual(r["job"]["runs"][-1]["result"], "running")

    def test_fast_drop_refused_even_with_matching_fingerprint(self):
        job = self.program(draw_feed_mm_min=600)["job"]
        self.tamper(job, self.program_text(job).replace(" F300", " F600"))
        with self.assertRaisesRegex(ValueError, "drop to the hand"):
            server.run_start(dict({"job": JOB}, **ALL_TICKS))
        self.link.start_program.assert_not_called()

    def test_speed_above_the_program_speed_refused(self):
        job = self.program(draw_feed_mm_min=400)["job"]
        self.tamper(job, self.program_text(job).replace(" F400", " F600"))
        with self.assertRaises(server.v10.ProgramReadError):
            server.run_start(dict({"job": JOB}, **ALL_TICKS))
        self.link.start_program.assert_not_called()

    def test_v14_program_still_runs_at_300(self):
        server.run_start(dict({"job": JOB}, **ALL_TICKS))                  # fixture job holds a v14 program
        self.assertEqual(self.link.start_program.call_args[0][0], GCODE)

    def test_v14_program_with_faster_lines_refused(self):
        self.write_job(program_text=with_feed(PROGRAM, 600))
        with self.assertRaises(server.v10.ProgramReadError):
            server.run_start(dict({"job": JOB}, **ALL_TICKS))
        self.link.start_program.assert_not_called()

    def test_routes(self):
        self.assertIs(server.Handler.POST_API["/api/studio/program"][0], server.program)
        self.assertIs(server.Handler.POST_API["/api/machine/run"][0], server.run_start)
        self.assertEqual(server.Handler.server_version, "HennaStudio/0.16")
        self.assertIn("/api/machine/jog", server.Handler.POST_API)


if __name__ == "__main__":
    unittest.main()
