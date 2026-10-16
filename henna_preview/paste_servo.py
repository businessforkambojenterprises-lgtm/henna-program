"""Paste pinch valve on a 180-degree servo, driven by a Raspberry Pi pin (user 2026-10-16: "gpio pin from rasp",
180 deg standard servo, pinch valve - the paste is pushed by air or a spring, the servo only pinches the tube).

Wiring: servo SIGNAL (orange / yellow) to header pin 12 (GPIO18); servo + (red) and - (brown / black) to their own
5-6 V supply (2 A or more - not the Pi's or the DLC32's 5 V pins: a servo under load can restart the Pi); that
supply's - also to a Pi GND pin (pin 14), so the signal has a common ground.
Two angles, found once with raspberry_pi/servo_angle_set.py and kept per device in outputs/paste_servo.json:
  closed_deg - the tube pinched shut (paste OFF), where the servo starts and returns to
  open_deg   - the tube open (paste ON)
Studio v29's link (grbl_program_link_v29.paste_output) calls on() / off(): open after the drop to the skin, closed
before every lift, closed at once on Pause, STOP, any error, disconnect and when Studio closes. Without a saved
file the output is not ready and Studio refuses paste runs and the dispenser test.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, "outputs", "paste_servo.json")
PIN = 18
NOT_FREE = {17: "the pause button", 27: "the old paste SSR pin"}
MIN_PULSE_S, MAX_PULSE_S = 0.0005, 0.0025      # 0 and 180 degrees on common hobby servos
MIN_SWING_DEG = 5.0


class ServoConfigError(ValueError):
    pass


def check_config(cfg):
    """{'pin', 'closed_deg', 'open_deg'} checked; raises ServoConfigError."""
    try:
        pin, closed, opened = int(cfg.get("pin", PIN)), float(cfg["closed_deg"]), float(cfg["open_deg"])
    except (KeyError, TypeError, ValueError):
        raise ServoConfigError("paste_servo.json needs closed_deg and open_deg (numbers)") from None
    if pin in NOT_FREE:
        raise ServoConfigError(f"GPIO{pin} is used by {NOT_FREE[pin]}")
    for name, v in (("closed_deg", closed), ("open_deg", opened)):
        if not 0.0 <= v <= 180.0:
            raise ServoConfigError(f"{name} must be 0 to 180 degrees")
    if abs(opened - closed) < MIN_SWING_DEG:
        raise ServoConfigError(f"open and closed must be at least {MIN_SWING_DEG:g} degrees apart")
    return {"pin": pin, "closed_deg": closed, "open_deg": opened}


def load_config(path=CONFIG):
    if not os.path.isfile(path):
        raise ServoConfigError("no saved servo angles - find them first: raspberry_pi/servo_angle_set.py")
    with open(path, encoding="utf-8") as f:
        return check_config(json.load(f))


def save_config(cfg, path=CONFIG):
    cfg = check_config(cfg)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dict(cfg, use_for_paste=True), f, indent=2)
    return cfg


def wants_servo(path=CONFIG):
    """True when a saved servo file says it is the paste output."""
    try:
        with open(path, encoding="utf-8") as f:
            return bool(json.load(f).get("use_for_paste"))
    except (OSError, ValueError):
        return False


def gpio_servo(pin, angle):
    """gpiozero.AngularServo on pin, starting at angle (same import route as pause_button.py)."""
    try:
        from gpiozero import AngularServo
    except ImportError:
        import sys
        sys.path.append("/usr/lib/python3/dist-packages")
        from gpiozero import AngularServo
    return AngularServo(pin, initial_angle=angle, min_angle=0, max_angle=180,
                        min_pulse_width=MIN_PULSE_S, max_pulse_width=MAX_PULSE_S)


class ServoPasteOutput:
    def __init__(self, path=CONFIG, make_servo=gpio_servo):
        self._path, self._make, self.servo, self.cfg = path, make_servo, None, None
        self.ready, self.error, self.is_open = False, None, False
        self.name = f"servo GPIO{PIN}"

    def start(self):
        try:
            self.cfg = load_config(self._path)
            self.name = f"servo GPIO{self.cfg['pin']}"
            self.servo = self._make(self.cfg["pin"], self.cfg["closed_deg"])
            self.off()
            self.ready, self.error = True, None
        except Exception as e:                             # not ready: paste runs are refused, Studio still runs
            self.ready, self.error = False, f"{type(e).__name__}: {e}"
        return self.view()

    def view(self, _=None):
        c = self.cfg or {}
        return {"ready": self.ready, "error": self.error, "pin": c.get("pin", PIN), "closed_deg": c.get("closed_deg"),
                "open_deg": c.get("open_deg"), "open": self.is_open}

    def on(self):
        if self.servo is None:
            raise RuntimeError(f"paste servo is not working: {self.error}")
        self.servo.angle = self.cfg["open_deg"]
        self.is_open = True

    def off(self):
        if self.servo is not None:
            self.servo.angle = self.cfg["closed_deg"]
        self.is_open = False

    def close(self):
        if self.servo is not None:
            try:
                self.off()
                import time
                time.sleep(0.4)                            # reach closed before the signal stops
                self.servo.close()
            except Exception:
                pass
