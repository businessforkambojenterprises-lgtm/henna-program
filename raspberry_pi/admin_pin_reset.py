"""Reset the forgotten Admin PIN of Henna Studio on this Pi (henna_preview/admin_lock.py).

Run on the Pi itself - this needs the Pi's own login, which is the authorization:
    ~/henna-venv/bin/python ~/henna-program/raspberry_pi/admin_pin_reset.py
Type RESET to confirm. Removes outputs/admin_pin.json only (calibration, machine choice, paste setup and servo
angles stay). Restart Studio; the next Admin open asks for a new PIN. One log row.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from henna_preview.admin_lock import AdminLock  # noqa: E402


def main(argv=None, ask=input, lock=None):
    lock = lock or AdminLock()
    if not lock.pin_set():
        print("No admin PIN is set on this Pi - nothing to reset. Open Admin in Studio to set one.")
        return 0
    if ask("Type RESET to remove the admin PIN: ").strip() != "RESET":
        print("Not reset.")
        return 1
    lock.reset()
    print("Admin PIN removed. Restart Henna Studio; the next Admin open asks for a new PIN.")
    try:
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Admin PIN reset (on the Pi)", None,
               "admin_pin_reset.py", "admin PIN removed", "removed", "OK", "", "forgotten PIN; reset at the Pi")
    except Exception as e:
        print(f"(log row not written: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
