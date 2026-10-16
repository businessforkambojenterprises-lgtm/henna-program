"""Find and save the paste servo's two angles (henna_preview/paste_servo.py) - the CNC is not used.

Studio must be CLOSED (it holds the servo pin while it runs). Steps:
  1. ~/henna-venv/bin/python raspberry_pi/servo_angle_set.py --angle 90
     moves the servo to 90 degrees and holds it 5 s. Try angles until you know
       CLOSED: the tube is pinched shut (no paste)      OPEN: the tube is open (paste flows)
     Start near the middle and change in small steps - a pinch that is too hard can stall the servo.
  2. ~/henna-venv/bin/python raspberry_pi/servo_angle_set.py --save --closed 20 --open 70
     saves them to outputs/paste_servo.json (Studio then uses the servo for paste) and shows
     closed -> open -> closed once.
  --off  stops using the servo for paste (the file is kept, use_for_paste false).
"""
import json
import os
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from henna_preview import paste_servo as ps  # noqa: E402

HOLD_S = 5.0


def studio_running():
    try:
        urllib.request.urlopen("http://127.0.0.1:8765/", timeout=1)
        return True
    except Exception:
        return False


def arg(argv, name, cast=float):
    return cast(argv[argv.index(name) + 1]) if name in argv else None


def main(argv=None, make_servo=ps.gpio_servo, sleep=time.sleep, path=ps.CONFIG):
    argv = sys.argv[1:] if argv is None else argv
    if "--off" in argv:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                cfg = json.load(f)
            cfg["use_for_paste"] = False
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2)
        print("Paste servo switched off for Studio (paste goes back to the DLC32 output). Restart Studio.")
        return 0
    if studio_running():
        print("STOP: Studio is running - close its window first (it holds the servo pin).")
        return 1
    pin = int(arg(argv, "--pin", int) or ps.PIN)
    if "--save" in argv:
        cfg = ps.save_config({"pin": pin, "closed_deg": arg(argv, "--closed"), "open_deg": arg(argv, "--open")}, path)
        servo = make_servo(cfg["pin"], cfg["closed_deg"])
        try:
            for name in ("closed_deg", "open_deg", "closed_deg"):
                servo.angle = cfg[name]
                print(f"  {name.split('_')[0]:6s} {cfg[name]:g} deg")
                sleep(1.5)
        finally:
            servo.close()
        print(f"SAVED {path}: closed {cfg['closed_deg']:g}, open {cfg['open_deg']:g} on GPIO{cfg['pin']}. "
              "Start Studio; the paste output is now this servo.")
        return 0
    angle = arg(argv, "--angle")
    if angle is None or not 0 <= angle <= 180:
        print(__doc__)
        return 1
    if pin in ps.NOT_FREE:
        print(f"STOP: GPIO{pin} is used by {ps.NOT_FREE[pin]}")
        return 1
    servo = make_servo(pin, angle)
    try:
        print(f"Servo on GPIO{pin} at {angle:g} deg for {HOLD_S:g} s - is the tube pinched shut or open?")
        sleep(HOLD_S)
    finally:
        servo.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
