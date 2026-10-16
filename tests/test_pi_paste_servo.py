"""Paste pinch-valve servo on the Pi (henna_preview/paste_servo.py + raspberry_pi/servo_angle_set.py): closed at
start and on off(), open on on(), refused without saved angles, the angle tool, and the Pi server's choice."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "raspberry_pi"))

from henna_preview import paste_servo as ps  # noqa: E402
import servo_angle_set as tool  # noqa: E402


class FakeServo:
    made = []

    def __init__(self, pin, angle):
        self.pin, self.angle, self.angles, self.closed = pin, angle, [angle], False
        FakeServo.made.append(self)

    def __setattr__(self, k, v):
        object.__setattr__(self, k, v)
        if k == "angle" and hasattr(self, "angles"):
            self.angles.append(v)

    def close(self):
        self.closed = True


class ServoOutputTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "paste_servo.json")
        self.addCleanup(shutil.rmtree, self.dir)

    def test_closed_at_start_open_on_closed_off(self):
        ps.save_config({"pin": 18, "closed_deg": 20, "open_deg": 70}, self.path)
        out = ps.ServoPasteOutput(self.path, make_servo=FakeServo)
        v = out.start()
        self.assertTrue(v["ready"], v)
        self.assertEqual(out.servo.angle, 20)
        out.on()
        self.assertEqual(out.servo.angle, 70)
        self.assertTrue(out.view()["open"])
        out.off()
        self.assertEqual(out.servo.angle, 20)
        out.on()
        out.close()
        self.assertEqual(out.servo.angle, 20)                    # closed before the signal stops
        self.assertTrue(out.servo.closed)
        self.assertEqual(out.name, "servo GPIO18")

    def test_no_saved_angles_is_not_ready_and_never_opens(self):
        out = ps.ServoPasteOutput(self.path, make_servo=FakeServo)
        v = out.start()
        self.assertFalse(v["ready"])
        self.assertIn("servo_angle_set.py", v["error"])
        with self.assertRaises(RuntimeError):
            out.on()
        out.off()

    def test_config_rules(self):
        for bad in ({"closed_deg": 20, "open_deg": 22}, {"closed_deg": -5, "open_deg": 40},
                    {"closed_deg": 20, "open_deg": 190}, {"pin": 17, "closed_deg": 20, "open_deg": 70},
                    {"pin": 27, "closed_deg": 20, "open_deg": 70}, {"closed_deg": "x", "open_deg": 70}, {}):
            with self.assertRaises(ps.ServoConfigError, msg=bad):
                ps.check_config(bad)
        self.assertFalse(ps.wants_servo(self.path))
        ps.save_config({"closed_deg": 20, "open_deg": 70}, self.path)
        self.assertTrue(ps.wants_servo(self.path))


class StudioAngleSetupTests(unittest.TestCase):
    """Program step -> Servo angles: try any angle, save closed / open (then closed), only while nothing runs."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "paste_servo.json")
        self.addCleanup(shutil.rmtree, self.dir)
        ps.save_config({"closed_deg": 60, "open_deg": 40}, self.path)
        self.out = ps.ServoPasteOutput(self.path, make_servo=FakeServo)
        self.out.start()

    def test_try_and_save(self):
        self.out.try_angle(75)
        self.assertEqual(self.out.servo.angle, 75)
        with self.assertRaises(ps.ServoConfigError):
            self.out.try_angle(200)
        v = self.out.set_angles(90, 60)
        self.assertEqual((v["closed_deg"], v["open_deg"], self.out.servo.angle), (90.0, 60.0, 90.0))
        self.assertEqual(ps.load_config(self.path)["closed_deg"], 90.0)
        with self.assertRaises(ps.ServoConfigError):
            self.out.set_angles(90, 88)                       # too close: refused, old angles kept
        self.assertEqual(ps.load_config(self.path)["closed_deg"], 90.0)

    def test_pi_routes_refuse_while_running_or_without_the_servo(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            os.environ["HENNA_PASTE"] = "servo"
            import webui_server_pi as pi
            pi.PASTE = self.out
            for route in ("/api/pi/servo/try", "/api/pi/servo/save"):
                self.assertIn(route, pi.PiHandler.POST_API)
            busy = {"busy": "program", "program": {"state": "running"}}
            saved_status = pi.studio.machine.status
            pi.studio.machine.status = lambda: busy
            logs = pi.studio._log
            pi.studio._log = lambda *a, **k: None
            try:
                with self.assertRaisesRegex(ValueError, "wait until the machine has finished"):
                    pi.servo_try({"angle": 70})
                pi.studio.machine.status = lambda: {"busy": None, "program": None}
                self.assertEqual(pi.servo_try({"angle": 70})["open"], True)
                self.assertEqual(pi.servo_save({"closed_deg": 85, "open_deg": 55})["closed_deg"], 85.0)
                pi.PASTE = object()
                with self.assertRaisesRegex(ValueError, "without the servo output"):
                    pi.servo_try({"angle": 70})
            finally:
                pi.studio.machine.status = saved_status
                pi.studio._log = logs
            with open(os.path.join(ROOT, "webui", "pi_touch.js"), encoding="utf-8") as f:
                js = f.read()
            self.assertIn("/api/pi/servo/try", js)
            self.assertIn("/api/pi/servo/save", js)
        finally:
            os.environ.pop("HENNA_PASTE", None)
            if "webui_server_pi" in sys.modules:
                sys.modules["webui_server_pi"].studio.machine.paste_output = None
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


class HardwareSignalTests(unittest.TestCase):
    """dtoverlay=pwm-pio,gpio=18: the servo pulses come from a sysfs PWM channel (hardware-timed, no twitching)."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)
        self.chip = os.path.join(self.root, "pwmchip1")
        os.makedirs(os.path.join(self.chip, "pwm0"))
        for name in ("export", "pwm0/period", "pwm0/duty_cycle", "pwm0/enable"):
            open(os.path.join(self.chip, name), "w").close()

    def read(self, name):
        with open(os.path.join(self.chip, "pwm0", name)) as f:
            return f.read()

    def test_pulse_widths_and_enable(self):
        servo = ps.SysfsServo(self.chip, 90)
        self.assertEqual((self.read("period"), self.read("duty_cycle"), self.read("enable")), ("20000000", "1500000", "1"))
        servo.angle = 0
        self.assertEqual(self.read("duty_cycle"), "500000")
        servo.angle = 180
        self.assertEqual(self.read("duty_cycle"), "2500000")
        self.assertEqual(servo.angle, 180.0)
        servo.close()
        self.assertEqual(self.read("enable"), "0")
        self.assertEqual(ps.signal_kind(servo), "hardware (pwm-pio)")
        self.assertEqual(ps.signal_kind(FakeServo(18, 0)), "software (twitches)")

    def test_finds_only_the_pio_chip(self):
        self.assertIsNone(ps.find_pio_chip(self.root))
        target = os.path.join(self.root, "1f00098000.pwm-pio")
        os.makedirs(target)
        try:
            os.symlink(target, os.path.join(self.chip, "device"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this system")
        self.assertEqual(ps.find_pio_chip(self.root), self.chip)


class PiModelChipTests(unittest.TestCase):
    """Pi 4: the BCM PWM chip (dtoverlay=pwm,pin=18,func=2); Pi 5: the pwm-pio chip, never its fan chip."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root)

    def chip(self, name, driver):
        chip = os.path.join(self.root, name)
        os.makedirs(os.path.join(chip, "device"))
        target = os.path.join(self.root, "drivers", driver)
        os.makedirs(target, exist_ok=True)
        try:
            os.symlink(target, os.path.join(chip, "device", "driver"), target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("no symlinks on this system")
        return chip

    def test_pi4_uses_the_bcm_chip_and_pi5_never_its_fan_chip(self):
        bcm = self.chip("pwmchip0", "bcm2835-pwm")
        self.assertEqual(ps.find_servo_chip(self.root, "Raspberry Pi 4 Model B Rev 1.4"), (bcm, "pwm"))
        self.assertEqual(ps.find_servo_chip(self.root, "Raspberry Pi 5 Model B Rev 1.1"), (None, None))
        pio = self.chip("pwmchip1", "pwm-pio-rp1")
        self.assertEqual(ps.find_servo_chip(self.root, "Raspberry Pi 5 Model B Rev 1.1"), (pio, "pwm-pio"))

    def test_kind_is_shown(self):
        chip = os.path.join(self.root, "pwmchip0")
        os.makedirs(os.path.join(chip, "pwm0"))
        for name in ("export", "pwm0/period", "pwm0/duty_cycle", "pwm0/enable"):
            open(os.path.join(chip, name), "w").close()
        self.assertEqual(ps.signal_kind(ps.SysfsServo(chip, 90, "pwm")), "hardware (pwm)")


class AngleToolTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "paste_servo.json")
        self.addCleanup(shutil.rmtree, self.dir)
        self._running = tool.studio_running
        tool.studio_running = lambda: False
        self.addCleanup(setattr, tool, "studio_running", self._running)
        FakeServo.made = []

    def run_tool(self, *argv):
        return tool.main(list(argv), make_servo=FakeServo, sleep=lambda s: None, path=self.path)

    def test_angle_moves_and_releases(self):
        self.assertEqual(self.run_tool("--angle", "90"), 0)
        self.assertEqual((FakeServo.made[-1].pin, FakeServo.made[-1].angle), (18, 90.0))
        self.assertTrue(FakeServo.made[-1].closed)
        self.assertFalse(os.path.exists(self.path))

    def test_save_writes_and_shows_closed_open_closed(self):
        self.assertEqual(self.run_tool("--save", "--closed", "20", "--open", "70"), 0)
        with open(self.path, encoding="utf-8") as f:
            cfg = json.load(f)
        self.assertEqual((cfg["closed_deg"], cfg["open_deg"], cfg["use_for_paste"]), (20.0, 70.0, True))
        self.assertEqual(FakeServo.made[-1].angles[-3:], [20.0, 70.0, 20.0])

    def test_off_and_refusals(self):
        self.run_tool("--save", "--closed", "20", "--open", "70")
        self.assertEqual(self.run_tool("--off"), 0)
        self.assertFalse(ps.wants_servo(self.path))
        self.assertEqual(self.run_tool("--angle", "200"), 1)
        self.assertEqual(self.run_tool("--angle", "90", "--pin", "17"), 1)
        tool.studio_running = lambda: True
        self.assertEqual(self.run_tool("--angle", "90"), 1)


class PiServerChoiceTests(unittest.TestCase):
    def test_servo_mode_hands_the_servo_to_the_link(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            os.environ["HENNA_PASTE"] = "servo"
            import webui_server_pi as pi
            self.assertEqual(pi.PASTE_MODE, "servo")
            self.assertIsInstance(pi.PASTE, ps.ServoPasteOutput)
            self.assertIs(pi.studio.machine.paste_output, pi.PASTE)
            self.assertEqual(pi.paste_view()["mode"], "servo")
        finally:
            os.environ.pop("HENNA_PASTE", None)
            if "webui_server_pi" in sys.modules:
                sys.modules["webui_server_pi"].studio.machine.paste_output = None
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


if __name__ == "__main__":
    unittest.main()
