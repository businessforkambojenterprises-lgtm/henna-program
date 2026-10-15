"""Paste output on a Raspberry Pi pin (user 2026-10-15: "B" - the SSR on the Pi).

Wiring: SSR input + (terminal 3) to header pin 13 (GPIO27), SSR input - (terminal 4) to pin 14 (GND). GPIO27 is low
(OFF) from power-on until Studio switches it; the SSR's input LED shows the state.
Studio v29's link (grbl_program_link_v29.paste_output) switches it: ON after the drop to the skin, OFF before every
lift, OFF at once on Pause, STOP, any error, disconnect and when Studio closes.
"""
PIN = 27


def _gpio_device():
    """gpiozero.DigitalOutputDevice on PIN, starting OFF (same import route as pause_button.py)."""
    try:
        from gpiozero import DigitalOutputDevice
    except ImportError:
        import sys
        sys.path.append("/usr/lib/python3/dist-packages")
        from gpiozero import DigitalOutputDevice
    return DigitalOutputDevice(PIN, active_high=True, initial_value=False)


class GpioPasteOutput:
    name = f"GPIO{PIN}"

    def __init__(self, make_device=_gpio_device):
        self._make, self.device = make_device, None
        self.ready, self.error = False, None

    def start(self):
        try:
            self.device = self._make()
            self.device.off()
            self.ready, self.error = True, None
        except Exception as e:                             # no GPIO: paste runs are refused, Studio still runs
            self.ready, self.error = False, f"{type(e).__name__}: {e}"
        return self.view()

    def view(self, _=None):
        return {"ready": self.ready, "error": self.error, "pin": PIN,
                "on": bool(self.device.value) if self.device is not None else False}

    def on(self):
        if self.device is None:
            raise RuntimeError(f"paste pin GPIO{PIN} is not working: {self.error}")
        self.device.on()

    def off(self):
        if self.device is not None:
            self.device.off()

    def close(self):
        if self.device is not None:
            try:
                self.device.off()
                self.device.close()
            except Exception:
                pass
