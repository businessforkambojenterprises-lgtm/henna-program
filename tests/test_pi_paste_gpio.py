"""Paste SSR on the Pi pin (henna_preview/paste_gpio.py): starts OFF, on/off, no GPIO -> not ready (paste refused),
and webui_server_pi uses it only with HENNA_PASTE=pi (default: the servo; controller with HENNA_PASTE=controller)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import paste_gpio  # noqa: E402


class FakeDevice:
    def __init__(self):
        self.value, self.closed = 1, False                 # starts "on" to prove start() switches it off

    def on(self):
        self.value = 1

    def off(self):
        self.value = 0

    def close(self):
        self.closed = True


class PastePinTests(unittest.TestCase):
    def test_starts_off_switches_and_closes_off(self):
        dev = FakeDevice()
        p = paste_gpio.GpioPasteOutput(make_device=lambda: dev)
        v = p.start()
        self.assertTrue(v["ready"])
        self.assertFalse(v["on"])
        p.on()
        self.assertTrue(p.view()["on"])
        p.off()
        self.assertFalse(p.view()["on"])
        p.on()
        p.close()
        self.assertEqual(dev.value, 0)
        self.assertTrue(dev.closed)

    def test_no_gpio_is_not_ready_and_never_on(self):
        def broken():
            raise ImportError("No module named 'gpiozero'")
        p = paste_gpio.GpioPasteOutput(make_device=broken)
        self.assertFalse(p.start()["ready"])
        with self.assertRaises(RuntimeError):
            p.on()
        p.off()                                            # harmless

    def test_pi_server_uses_the_pin(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            os.environ.pop("HENNA_PASTE", None)
            import webui_server_pi as pi
            from henna_preview.paste_servo import ServoPasteOutput
            self.assertIsInstance(pi.studio.machine.paste_output, ServoPasteOutput)   # default since 2026-10-16
            sys.modules.pop("webui_server_pi", None)
            pi.studio.machine.paste_output = None
            os.environ["HENNA_PASTE"] = "controller"
            import webui_server_pi as pi
            self.assertIsNone(pi.studio.machine.paste_output)                         # only the DLC32's TTL output
            self.assertEqual(pi.PiHandler.GET_API["/api/pi/paste_output"]()["output"], "controller")
            sys.modules.pop("webui_server_pi", None)
            os.environ["HENNA_PASTE"] = "pi"
            import webui_server_pi as pi
            self.assertIs(pi.studio.machine.paste_output, pi.PASTE)
            self.assertEqual(pi.PASTE.name, "GPIO27")
            with open(pi.__file__, encoding="utf-8") as f:
                src = f.read()
            self.assertIn("PASTE.start()", src)
            self.assertIn("PASTE.close()", src)
        finally:
            os.environ.pop("HENNA_PASTE", None)
            if "webui_server_pi" in sys.modules:
                sys.modules["webui_server_pi"].studio.machine.paste_output = None
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


if __name__ == "__main__":
    unittest.main()
