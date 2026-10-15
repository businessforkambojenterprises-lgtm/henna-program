"""Paste SSR on the Pi pin (henna_preview/paste_gpio.py): starts OFF, on/off, no GPIO -> not ready (paste refused),
and webui_server_pi hands it to Studio v29's link."""
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
            import webui_server_pi as pi
            self.assertIs(pi.studio.machine.paste_output, pi.PASTE)
            self.assertIs(pi.PiHandler.GET_API["/api/pi/paste_output"].__self__, pi.PASTE)
            self.assertEqual(pi.PASTE.name, "GPIO27")
            with open(pi.__file__, encoding="utf-8") as f:
                src = f.read()
            self.assertIn("PASTE.start()", src)
            self.assertIn("PASTE.close()", src)
        finally:
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


if __name__ == "__main__":
    unittest.main()
