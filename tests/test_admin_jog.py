"""(From the Pi branch's tests/test_pi_admin_jog.py with henna_preview/admin_jog.py, 2026-10-24; the PC server's routing is in test_pc_admin.py.)
Admin Z jog (2026-10-18, user: "i want z to move freely under admin panel"): with a valid Admin session the Jog
window's Z goes straight up or down by the slider's distance between Z 0 and the machine's Z floor, and X/Y ("now x and
y should aslo move no restriction in admin panel") at any height down to the floor inside the work area; without a
session the normal jog (travel height, real-hand rule) applies."""
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


class AdminXYTests(unittest.TestCase):
    def test_any_height_inside_the_work_area(self):
        t = admin_jog.admin_xy_target
        self.assertEqual(t((57.6, 34.2, -35.5), "X", 1, 12.4, -40.0, 225, 176, step_ok), (70.0, 34.2, -35.5))
        self.assertEqual(t((57.6, 34.2, -40.0), "Y", -1, 34.2, -40.0, 225, 176, step_ok), (57.6, 0.0, -40.0))
        with self.assertRaisesRegex(MachineError, "outside the work area"):
            t((57.6, 34.2, -5.0), "Y", -1, 34.3, -40.0, 225, 176, step_ok)
        with self.assertRaisesRegex(MachineError, "outside the work area"):
            t((220.0, 34.2, -5.0), "X", 1, 5.1, -40.0, 225, 176, step_ok)
        with self.assertRaisesRegex(MachineError, "below the floor"):
            t((57.6, 34.2, -41.0), "X", 1, 1.0, -40.0, 225, 176, step_ok)
        with self.assertRaises(MachineError):
            t((57.6, 34.2, -5.0), "Z", 1, 1.0, -40.0, 225, 176, step_ok)

    def test_slow_below_travel_height(self):
        link = FakeLink(-35.5)
        admin_jog.start_admin_xy(link, "X", 1, 12.4, -40.0, -5.0, 225, 176, step_ok)
        link.mpos = [65.7, 80.0, -5.0]
        admin_jog.start_admin_xy(link, "Y", -1, 25.0, -40.0, -5.0, 225, 176, step_ok)
        self.assertEqual([c[0] for c in link.sent], ["$J=G21 G90 G53 X78.100 F300", "$J=G21 G90 G53 Y55.000 F1000"])


if __name__ == "__main__":
    unittest.main()
