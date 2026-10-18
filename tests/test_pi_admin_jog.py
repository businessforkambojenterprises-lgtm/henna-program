"""Admin Z jog (2026-10-18, user: "i want z to move freely under admin panel"): with a valid Admin session the Jog
window's Z goes straight up or down by the slider's distance between Z 0 and the machine's Z floor; X/Y keep their
rules; without a session the normal jog (down to travel height only) applies."""
import os
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import admin_jog  # noqa: E402
from henna_preview.grbl_program_link import MachineError  # noqa: E402


def step_ok(s):
    return isinstance(s, (int, float)) and 0.1 - 1e-9 <= s <= 100 + 1e-9 and abs(s * 10 - round(s * 10)) < 1e-6


class FakeLink:
    def __init__(self, z):
        self._lock = threading.Lock()
        self.busy, self._capture_owner, self.homed = "", None, True
        self.mpos = [65.7, 80.0, z]
        self.cal_floor = None
        self.sent = []

    def _config(self):
        pass

    def _fresh_locked(self):
        pass

    def _cal_execute(self, start, target, command, floor, detail, on_done):
        self.sent.append((command, floor, self.cal_floor))
        return {"busy": "jog"}


class AdminZTargetTests(unittest.TestCase):
    def test_free_between_top_and_floor(self):
        t = admin_jog.admin_z_target
        self.assertEqual(t((65.7, 80.0, -5.0), -1, 23.4, -40.0, step_ok), (65.7, 80.0, -28.4))
        self.assertEqual(t((65.7, 80.0, -5.0), -1, 35.0, -40.0, step_ok), (65.7, 80.0, -40.0))     # exactly the floor
        self.assertEqual(t((65.7, 80.0, -42.0), 1, 1.0, -40.0, step_ok), (65.7, 80.0, -41.0))      # up from below it
        with self.assertRaisesRegex(MachineError, "below the floor Z -40"):
            t((65.7, 80.0, -5.0), -1, 35.1, -40.0, step_ok)
        with self.assertRaisesRegex(MachineError, "above the top"):
            t((65.7, 80.0, -5.0), 1, 5.1, -40.0, step_ok)
        for bad in ((1.0, 1), (-1, 0.05), (-1, True), (True, 1.0)):
            with self.assertRaises(MachineError):
                t((65.7, 80.0, -5.0), bad[0], bad[1], -40.0, step_ok)
        with self.assertRaisesRegex(MachineError, "no Z floor"):
            t((65.7, 80.0, -5.0), -1, 1.0, None, step_ok)

    def test_start_sends_one_z_line_of_the_calibration_form(self):
        link = FakeLink(-5.0)
        admin_jog.start_admin_z(link, -1, 23.4, -40.0, step_ok)
        admin_jog.start_admin_z(link, -1, 1.6, -40.0, step_ok)
        self.assertEqual(link.sent, [("$J=G21 G90 G53 Z-28.400 F300", -40.0, -40.0),
                                     ("$J=G21 G90 G53 Z-6.600 F100", -40.0, -40.0)])
        link.busy = "program"
        with self.assertRaisesRegex(MachineError, "busy"):
            admin_jog.start_admin_z(link, -1, 1.0, -40.0, step_ok)
        link.busy, link.homed = "", False
        with self.assertRaisesRegex(MachineError, "Home"):
            admin_jog.start_admin_z(link, -1, 1.0, -40.0, step_ok)


class PiJogRoutingTests(unittest.TestCase):
    def test_admin_z_goes_to_the_free_z_jog(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            import webui_server_pi as pi
            calls = []
            saved = (pi.admin_jog.start_admin_z, pi.z_floor, pi.studio._JOG, pi._JOG)
            pi.admin_jog.start_admin_z = lambda link, d, s, floor, ok: calls.append(("adminZ", d, s, floor)) or {}
            pi.z_floor = lambda: -40.0
            pi.studio._JOG = (lambda p: calls.append(("v28", p["axis"])) or {}, False)
            pi._JOG = (lambda p: calls.append(("v29", p["axis"])) or {}, False)
            v10.camera.status = lambda: {"state": "off"}
            try:
                z = {"axis": "Z", "direction": -1, "step_mm": 23.4, "confirm_no_person": True}
                pi._REQ.admin = True
                pi.pi_jog(z)
                pi.pi_jog(dict(z, axis="X"))
                with self.assertRaisesRegex(ValueError, "no person"):
                    pi.pi_jog(dict(z, confirm_no_person=False))
                pi._REQ.admin = False
                pi.pi_jog(z)
                self.assertEqual(calls, [("adminZ", -1, 23.4, -40.0), ("v28", "X"), ("v29", "Z")])
                self.assertIn("/api/pi/jog_limits", pi.PiHandler.GET_API)
            finally:
                pi.admin_jog.start_admin_z, pi.z_floor, pi.studio._JOG, pi._JOG = saved
                pi._REQ.admin = False
        finally:
            if "webui_server_pi" in sys.modules:
                sys.modules["webui_server_pi"].studio.machine.paste_output = None
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


if __name__ == "__main__":
    unittest.main()
