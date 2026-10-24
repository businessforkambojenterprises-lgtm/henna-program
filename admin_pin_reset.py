"""Reset the forgotten Admin PIN of Henna Studio on this PC (henna_preview/admin_lock.py; the Pi has its own copy in
raspberry_pi/admin_pin_reset.py on its branch).

Run on this PC - being logged in to it is the authorization:
    .venv\Scripts\python.exe admin_pin_reset.py
Type RESET to confirm. Removes outputs/admin_pin.json only (calibration, machine choice and paste setup stay).
Restart Studio; the next Admin open asks for a new PIN. One log row.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from henna_preview.admin_lock import AdminLock  # noqa: E402


def main(argv=None, ask=input, lock=None):
    lock = lock or AdminLock()
    if not lock.pin_set():
        print("No admin PIN is set on this PC - nothing to reset. Open Admin in Studio to set one.")
        return 0
    if ask("Type RESET to remove the admin PIN: ").strip() != "RESET":
        print("Not reset.")
        return 1
    lock.reset()
    print("Admin PIN removed. Restart Henna Studio; the next Admin open asks for a new PIN.")
    try:
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Admin PIN reset (on the PC)", None,
               "admin_pin_reset.py", "admin PIN removed", "removed", "OK", "", "forgotten PIN; reset on the PC")
    except Exception as e:
        print(f"(log row not written: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
