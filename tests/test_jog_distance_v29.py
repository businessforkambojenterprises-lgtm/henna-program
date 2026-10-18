"""Studio v29 (user 2026-10-18: "jog should be like mks web ui fo distance there has to be a slider rather than fixed
numbers"): from start, the jog takes any distance from 0.1 to 100 mm in 0.1 mm steps (the route and the link); the
link's target checks are unchanged; v14 to v28 keep 0.1 / 1 / 10 until v29 starts. The Jog window has the slider and
the active machine's own travel height and work area."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webui_server_v14 as v14  # noqa: E402
from henna_preview import grbl_program_link as gl  # noqa: E402
from henna_preview.grbl_program_link import GrblProgramLink, MachineError  # noqa: E402


class JogDistanceTests(unittest.TestCase):
    def setUp(self):
        self.saved = (v14.jog_step_ok, v14.JOG_STEP_TEXT, gl.jog_step_ok, gl.JOG_STEP_TEXT)

    def tearDown(self):
        v14.jog_step_ok, v14.JOG_STEP_TEXT, gl.jog_step_ok, gl.JOG_STEP_TEXT = self.saved

    def test_before_v29_starts_only_the_three_steps(self):
        self.assertTrue(gl.jog_step_ok(10.0))
        self.assertFalse(gl.jog_step_ok(2.5))
        with self.assertRaisesRegex(MachineError, "step 0.1, 1 or 10 mm"):
            GrblProgramLink.jog_target((50.0, 50.0, -5.0), "X", 1, 2.5)

    def test_free_steps_after_install(self):
        import webui_server_v29 as v29
        for ok in (0.1, 0.3, 2.5, 37, 100, 100.0):
            self.assertTrue(v29.free_jog_step(ok), ok)
        for bad in (0, 0.05, 0.25, 100.1, -1, "x", None):
            self.assertFalse(v29.free_jog_step(bad), bad)
        v29.install_free_jog()
        self.assertEqual(GrblProgramLink.jog_target((50.0, 50.0, -5.0), "X", 1, 2.5), (52.5, 50.0, -5.0))
        self.assertEqual(GrblProgramLink.jog_target((50.0, 50.0, -5.0), "Y", -1, 37.0), (50.0, 13.0, -5.0))
        with self.assertRaisesRegex(MachineError, "0.1 to 100 mm"):
            GrblProgramLink.jog_target((50.0, 50.0, -5.0), "X", 1, 0.25)
        with self.assertRaisesRegex(MachineError, "outside the machine"):          # the box check is the same
            GrblProgramLink.jog_target((50.0, 50.0, -5.0), "X", -1, 60.0)
        with self.assertRaisesRegex(MachineError, "above the top"):
            GrblProgramLink.jog_target((50.0, 50.0, -5.0), "Z", 1, 5.5)

    def test_route_takes_the_slider_distance(self):
        import webui_server_v29 as v29
        sent = []
        cam, start = v14.v10.camera.status, v14.machine.start_jog
        v14.v10.camera.status = lambda: {"state": "off"}
        v14.machine.start_jog = lambda a, d, s: sent.append((a, d, s)) or {"busy": "jog"}
        try:
            p = {"axis": "X", "direction": 1, "confirm_no_person": True}
            with self.assertRaisesRegex(ValueError, "step 0.1, 1 or 10 mm"):
                v14.jog(dict(p, step_mm=2.5))
            v29.install_free_jog()
            v14.jog(dict(p, step_mm=2.5))
            with self.assertRaisesRegex(ValueError, "0.1 to 100 mm"):
                v14.jog(dict(p, step_mm=150))
            with self.assertRaises(ValueError):
                v14.jog(dict(p, step_mm=True))
            self.assertEqual(sent, [("X", 1, 2.5)])
        finally:
            v14.v10.camera.status, v14.machine.start_jog = cam, start

    def test_page_has_the_slider_and_the_machines_own_limits(self):
        import webui_server_v29 as v29
        with open(v29.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        self.assertIn('id="jdist"', page)
        self.assertIn("function jogFromSlider(p)", page)
        self.assertIn("MACHINES.motion_in_use", page)
        self.assertNotIn("Moves stay at travel height (Z -20)", page)
        self.assertNotIn('data-step="10"', page)


if __name__ == "__main__":
    unittest.main()
