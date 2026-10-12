"""Raspberry Pi stage 3b check: only '?' can reach the CNC, and the status-age helpers (no hardware)."""
import importlib.util
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("stage3b_cnc_link_check",
                                               os.path.join(ROOT, "raspberry_pi", "stage3b_cnc_link_check.py"))
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

from henna_preview.grbl_program_link_v22 import GrblProgramLinkV22  # noqa: E402
from henna_preview.grbl_scan_link import MachineError  # noqa: E402
from test_grbl_scan_link import FakeSerial  # noqa: E402


def calibration():
    with open(os.path.join(ROOT, "current_calibration.json"), encoding="utf-8") as f:
        return json.load(f)


class TestStatusQueryOnly(unittest.TestCase):
    def test_only_question_mark_reaches_the_port(self):
        fake = FakeSerial()
        w = check.StatusQueryOnly(fake)
        w.write(b"?")
        for data in (b"$H\n", b"$HZ\n", b"!", b"\x18", b"$J=G21 G90 G53 Z-20.000 F300\n", b"??"):
            with self.assertRaises(RuntimeError):
                w.write(data)
        self.assertEqual(fake.sent, [b"?"])
        self.assertEqual(w.sent, 1)
        w.close()
        self.assertTrue(fake.closed)

    def test_studio_link_polls_but_cannot_home(self):
        fake = FakeSerial(state="Alarm")
        link = GrblProgramLinkV22(calibration, opener=lambda p: check.StatusQueryOnly(fake), poll_s=0.01)
        self.addCleanup(lambda: link.disconnect() if link.status()["connected"] and not link.status()["busy"] else None)
        st = link.connect("FAKE", wait_s=0.5)
        self.assertEqual(st["state"], "Alarm")
        try:
            link.start_homing()
        except MachineError:
            pass
        deadline = __import__("time").monotonic() + 2.0
        while link.status()["busy"] and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.02)
        self.assertEqual(set(fake.sent), {b"?"}, "nothing but '?' may reach the controller")
        self.assertFalse(link.status()["homed"])


class TestHelpers(unittest.TestCase):
    def test_find_ch340(self):
        self.assertEqual(check.find_ch340([("/dev/ttyAMA10", None, None), ("/dev/ttyUSB0", 0x1A86, 0x7523)]),
                         "/dev/ttyUSB0")
        self.assertIsNone(check.find_ch340([("/dev/ttyUSB0", 0x1A86, 0x7523), ("/dev/ttyUSB1", 0x1A86, 0x7523)]))
        self.assertIsNone(check.find_ch340([]))

    def test_age_stats(self):
        samples = [(10.0, 0.1), (10.05, None), (10.1, 1.7), (10.15, 1.75), (10.2, 0.05), (12.0, 3.0)]
        s = check.age_stats(samples, 10.0, 11.0, 9.9)
        self.assertEqual((s["samples"], s["no_status_samples"], s["max_age_s"], s["worst_at_s"], s["time_over_limit_s"]),
                         (5, 1, 1.75, 0.25, 0.1))
        self.assertIsNone(check.age_stats([], 0, 1, 0)["max_age_s"])

    def test_grade(self):
        self.assertEqual(check.grade(0.4)[0], "OK ")
        self.assertIn("close", check.grade(1.2)[1])
        self.assertIn("reproduced", check.grade(1.9)[1])
        self.assertEqual(check.grade(None)[0], "NO ")


if __name__ == "__main__":
    unittest.main()
