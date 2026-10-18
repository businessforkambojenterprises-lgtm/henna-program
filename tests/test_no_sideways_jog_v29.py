"""Studio v29 (user 2026-10-18): while a real-hand job (hover run or camera only) is open, sideways (X/Y) jogs are
refused by the server and hidden in the Jog window; Z jogs still go through; a prosthetic job allows all axes."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class NoSidewaysJogTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29
        self.v29 = v29 = webui_server_v29
        self.saved = (dict(v29._ACTIVE), v29._JOG, v29._GET_JOB, v29._JOB_NEW)
        self.sent = []
        v29._JOG = (lambda p: self.sent.append(p) or {"busy": "jog"}, False)

    def tearDown(self):
        v29 = self.v29
        v29._ACTIVE.clear()
        v29._ACTIVE.update(self.saved[0])
        v29._JOG, v29._GET_JOB, v29._JOB_NEW = self.saved[1:]

    def jog(self, axis):
        return self.v29.jog({"axis": axis, "direction": 1, "step_mm": 1.0, "confirm_no_person": True})

    def test_real_hand_job_blocks_x_and_y_only(self):
        v29 = self.v29
        v29._GET_JOB = lambda q: {"id": "20261018-084336-job", "subject": "realhand"}
        v29.get_job({"id": ["20261018-084336-job"]})
        for axis in ("X", "Y"):
            with self.assertRaisesRegex(ValueError, "Sideways jog is off"):
                self.jog(axis)
        self.jog("Z")
        self.assertEqual([p["axis"] for p in self.sent], ["Z"])

    def test_camera_only_job_blocks_too_and_prosthetic_allows(self):
        v29 = self.v29
        v29._JOB_NEW = (lambda p: {"job": {"id": "20261018-090000-job", "subject": "person"}}, False)
        v29.job_new({"subject": "person"})
        with self.assertRaises(ValueError):
            self.jog("X")
        v29._GET_JOB = lambda q: {"id": "20261018-091000-job", "subject": "prosthetic"}
        v29.get_job({"id": ["20261018-091000-job"]})
        self.jog("X")
        self.jog("Y")
        self.assertEqual([p["axis"] for p in self.sent], ["X", "Y"])

    def test_routes_and_page(self):
        v29 = self.v29
        self.assertIs(v29.Handler.POST_API["/api/machine/jog"][0], v29.jog)
        self.assertIs(v29.Handler.POST_API["/api/studio/job_new"][0], v29.job_new)
        self.assertIs(v29.Handler.GET_API["/api/studio/job"], v29.get_job)
        with open(v29.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("""#dlgjog [data-jog^="X"],#dlgjog [data-jog^="Y"]""", page)


if __name__ == "__main__":
    unittest.main()
