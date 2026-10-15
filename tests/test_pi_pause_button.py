"""Hardware PAUSE button on the Pi (henna_preview/pause_button.py): Pause a running program, STOP any other motion,
nothing when idle; a failed Pause falls back to STOP; no GPIO -> reported, Studio still starts."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import pause_button as pb  # noqa: E402


class FakeMachine:
    def __init__(self, status, pause_error=None):
        self._status, self.calls, self.pause_error = status, [], pause_error

    def status(self):
        return dict(self._status)

    def pause(self):
        self.calls.append("pause")
        if self.pause_error:
            raise RuntimeError(self.pause_error)

    def stop(self):
        self.calls.append("stop")


class FakeButton:
    is_pressed = True
    when_pressed = None

    def close(self):
        pass


def running(paused=False):
    return {"busy": "program", "state": "Hold" if paused else "Run", "mpos": [60.0, 80.0, -20.0],
            "program": {"state": "running", "paused": paused}}


class PressActionTests(unittest.TestCase):
    def test_running_program_is_paused(self):
        self.assertEqual(pb.press_action(running()), "pause")

    def test_paused_program_is_left_alone(self):
        self.assertIsNone(pb.press_action(running(paused=True)))

    def test_other_motion_is_stopped(self):
        for st in ({"busy": "jog", "state": "Jog"}, {"busy": "homing", "state": "Home"},
                   {"busy": None, "state": "Jog"}, {"busy": "scan_position", "state": "Idle"},
                   {"busy": None, "state": "Run"}):
            self.assertEqual(pb.press_action(st), "stop", st)

    def test_idle_or_recording_does_nothing(self):
        for st in ({"busy": None, "state": "Idle"}, {"busy": "recording", "state": "Idle"},
                   {"busy": None, "state": "Alarm"}, {"busy": None, "state": None}):
            self.assertIsNone(pb.press_action(st), st)


class PressTests(unittest.TestCase):
    def press(self, machine):
        rows = []
        b = pb.PauseButton(machine, lambda *a: rows.append(a), make_button=FakeButton)
        self.assertTrue(b.start()["ready"])
        b.button.when_pressed()
        return b, rows

    def test_press_pauses_and_logs(self):
        m = FakeMachine(running())
        b, rows = self.press(m)
        self.assertEqual(m.calls, ["pause"])
        self.assertEqual(len(rows), 1)
        self.assertEqual(b.view()["presses"], 1)
        self.assertEqual(b.view()["last"]["action"], "pause")

    def test_failed_pause_sends_stop(self):
        m = FakeMachine(running(), pause_error="No program is running")
        b, _ = self.press(m)
        self.assertEqual(m.calls, ["pause", "stop"])
        self.assertIn("STOP sent instead", b.view()["last"]["result"])

    def test_a_spike_is_not_a_press(self):
        m = FakeMachine(running())
        b = pb.PauseButton(m, lambda *a: None, make_button=FakeButton)
        b.start()
        b.button.is_pressed = False
        b.pressed()
        self.assertEqual(m.calls, [])

    def test_no_gpio_is_reported_not_fatal(self):
        def broken():
            raise ImportError("No module named 'gpiozero'")
        b = pb.PauseButton(FakeMachine({}), lambda *a: None, make_button=broken)
        v = b.start()
        self.assertFalse(v["ready"])
        self.assertIn("gpiozero", v["error"])


class PiServerTests(unittest.TestCase):
    def test_route_and_page_chip(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            import webui_server_pi as pi
            self.assertIs(pi.PiHandler.GET_API["/api/pi/pause_button"].__self__, pi.PAUSE_BUTTON)
            self.assertIs(pi.PAUSE_BUTTON.machine, pi.studio.machine)
            self.assertIn("/api/pi/pause_button", pi.pi_page())
            with open(pi.__file__, encoding="utf-8") as f:
                src = f.read()
            self.assertIn("PAUSE_BUTTON.start()", src)
            self.assertIn("PAUSE_BUTTON.close()", src)
        finally:
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


if __name__ == "__main__":
    unittest.main()
