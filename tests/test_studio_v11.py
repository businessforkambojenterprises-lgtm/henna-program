"""Henna Studio (v11): job status and invalidation, prosthetic-only guard, SVG upload checks."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server as base  # noqa: E402
import webui_server_v11 as s  # noqa: E402

LINE_ART = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4" fill="none" stroke="#000"/></svg>'
FILLED = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect x="1" y="1" width="8" height="8"/></svg>'


class Jobs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(base, "OUT_ROOT", self.tmp.name)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_status_follows_steps_and_redo_invalidates_later_steps(self):
        j = s.job_new({"subject": "prosthetic"})
        self.assertEqual(j["status"], "scan")
        for k in ("scan", "hand", "region", "design", "plan", "offset", "program"):
            j = s.set_step(j, k, {"verdict": "ACCEPTED"} if k == "offset" else {"x": 1})
        self.assertEqual(j["status"], "run")
        j = s.set_step(j, "gap", {"verdict": "PASS"})
        self.assertEqual(j["status"], "done")
        j = s.set_step(j, "region", {"x": 2})          # redo the region
        self.assertEqual(sorted(j["steps"]), ["hand", "region", "scan"])
        self.assertEqual(j["status"], "design")

    def test_person_job_is_camera_only(self):
        j = s.job_new({"subject": "person"})
        for k in ("scan", "hand", "region", "design"):
            j = s.set_step(j, k, {"x": 1})
        self.assertEqual(j["status"], "done (camera only)")
        for fn in (s.offset, s.skip_offset, s.program, s.gapcheck):
            with self.assertRaises(ValueError):
                fn({"job": j["id"], "lines": [], "paper_mm": 0.1})

    def test_unknown_subject_and_bad_job_ids(self):
        with self.assertRaises(ValueError):
            s.job_new({"subject": "robot"})
        for bad in ("../x-job", "x", "20261009-plan", None):
            with self.assertRaises(ValueError):
                s.job_path(bad)


class Upload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(s, "UPLOAD_DIR", self.tmp.name)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def test_line_art_is_saved_with_a_safe_name(self):
        r = s.design_upload({"filename": "../My Design!.svg", "svg": LINE_ART})
        self.assertTrue(r["design"]["usable"])
        self.assertEqual(os.listdir(self.tmp.name), ["My_Design.svg"])

    def test_filled_shapes_doctype_and_non_svg_are_refused_and_nothing_is_kept(self):
        for text in (FILLED, "<!DOCTYPE svg [<!ENTITY a 'b'>]>" + LINE_ART, "hello", "<svg" + "x" * 400_000):
            with self.assertRaises(ValueError):
                s.design_upload({"filename": "d.svg", "svg": text})
        self.assertEqual(os.listdir(self.tmp.name), [])


if __name__ == "__main__":
    unittest.main()
