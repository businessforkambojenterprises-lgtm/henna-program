"""Studio v29: an existing recording uses the controller status Studio saved when it recorded it (user 2026-10-16:
"remove this status line") - from the recording's note, the job named in it, or any job whose scan it is."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webui_server as base  # noqa: E402

v29 = None                      # imported in setUp: importing v26+ at collection time changes v10.cal for older tests

LINE = "<Idle|MPos:65.700,80.000,-5.000|Bf:63,0|FS:0,0|Pn:P>"


class SavedStatusTests(unittest.TestCase):
    def setUp(self):
        global v29
        import webui_server_v29
        v29 = webui_server_v29
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self._out = base.OUT_ROOT
        base.OUT_ROOT = os.path.join(self.dir, "outputs")
        self.addCleanup(setattr, base, "OUT_ROOT", self._out)
        self.rec = os.path.join(self.dir, "20261016_110427_211012.db3")
        open(self.rec, "w").close()

    def job(self, job_id, recording, line=LINE):
        d = os.path.join(base.OUT_ROOT, job_id)
        os.makedirs(d)
        with open(os.path.join(d, "job.json"), "w", encoding="utf-8") as f:
            json.dump({"id": job_id, "subject": "prosthetic",
                       "steps": {"scan": {"recording": recording, "status_line": line}}}, f)

    def note(self, **kw):
        with open(self.rec + ".machine.json", "w", encoding="utf-8") as f:
            json.dump(dict({"machine": "ball-screw"}, **kw), f)

    def test_from_the_note(self):
        self.note(job="20261016-110000-job", status_line=LINE)
        self.assertEqual(v29.saved_status_line(self.rec), LINE)

    def test_from_the_job_the_note_names(self):
        self.job("20261016-110000-job", self.rec)
        self.note(job="20261016-110000-job")
        self.assertEqual(v29.saved_status_line(self.rec), LINE)

    def test_from_any_job_with_this_recording(self):
        self.job("20261016-100000-job", os.path.join(self.dir, "other.db3"), line="<Idle|MPos:1.000,2.000,3.000|>")
        self.job("20261016-110000-job", self.rec)
        self.assertEqual(v29.saved_status_line(self.rec), LINE)

    def test_none_when_studio_never_saved_one(self):
        self.job("20261016-100000-job", os.path.join(self.dir, "other.db3"))
        self.assertIsNone(v29.saved_status_line(self.rec))

    def test_use_recording_fills_the_status(self):
        self.note(status_line=LINE)
        seen = {}
        saved = v29._USE_REC_ROUTE
        v29._USE_REC_ROUTE = (lambda p: seen.update(p) or {"job": "ok"}, True)
        saved_resolve = __import__("webui_server_v2").resolve_recording
        import webui_server_v2
        webui_server_v2.resolve_recording = lambda p: p
        try:
            self.assertEqual(v29.use_recording({"job": "j", "path": self.rec}), {"job": "ok"})
            self.assertEqual(seen["status_line"], LINE)
            os.remove(self.rec + ".machine.json")
            with self.assertRaisesRegex(ValueError, "record a new scan"):
                v29.use_recording({"job": "j", "path": self.rec})
        finally:
            v29._USE_REC_ROUTE = saved
            webui_server_v2.resolve_recording = saved_resolve

    def test_page_has_no_status_box(self):
        with open(v29.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        self.assertNotIn("exline", page)
        self.assertNotIn("Status line sent before that recording", page)
        self.assertIs(v29.Handler.POST_API["/api/studio/use_recording"][0], v29.use_recording)
        self.assertIs(v29.Handler.POST_API["/api/studio/scan"][0], v29.scan)


if __name__ == "__main__":
    unittest.main()
