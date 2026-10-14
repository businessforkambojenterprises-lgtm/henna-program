"""Set ONE controller setting: steps/mm of one axis ($102 Z, or $100 X / $101 Y with --axis) - NO MOTION.
User approval 2026-10-13 (Z, ball-screw ruler check); X/Y added 2026-10-14 (user yes: the X/Y fit showed machine Y
2.4 % and X 0.9 % short of the camera).

Why: the Z ruler check (A 40 mm at Z-20, B 32 mm at Z-60) showed Z moving 8 mm for 40 mm commanded with $102=100,
so Z steps/mm should be about 100 x 40 / 8 = 500. This tool sends only:
  ?    status (must be Idle or Alarm - nothing moving)        $$   all settings (before and after)
  $10n=<value>   the one write ($100 X, $101 Y, $102 Z), value 300 - 800, only after the old value was read
and refuses everything else before it reaches the port (same wrapper idea as machine_settings_check.py). Studio must
be DISCONNECTED (one user per port). After the change the controller's position no longer matches the head: HOME
AGAIN before any move. Result: printed summary + outputs/machine_settings/<machine>/<time>_z_steps.json + one log row.
  python machine_z_steps_set.py --value 500 [--axis Z|X|Y] [--port COM4] [--reason "ruler check ..."]
"""
import datetime as dt
import json
import os
import sys

from machine_settings_check import (ROOT, active_machine, find_port, parse_settings, read_lines,
                                    studio_connected)

Z_STEPS_MIN, Z_STEPS_MAX = 300.0, 800.0
AXES = {"X": 100, "Y": 101, "Z": 102}


def write_command(value, axis="Z"):
    value = float(value)
    if axis not in AXES:
        raise ValueError("axis must be X, Y or Z")
    if not Z_STEPS_MIN <= value <= Z_STEPS_MAX:
        raise ValueError(f"{axis} steps/mm must be between {Z_STEPS_MIN:g} and {Z_STEPS_MAX:g}")
    return f"${AXES[axis]}={value:.3f}\n".encode("ascii")


class OnlyZSteps:
    """Serial port wrapper: write() passes only ?, $$ and the one $10n=<value> line - anything else raises."""

    def __init__(self, ser, value, axis="Z"):
        self._ser = ser
        self.allowed = (b"?", b"$$\n", write_command(value, axis))
        self.sent = []

    def write(self, data):
        data = bytes(data)
        if data not in self.allowed:
            raise RuntimeError(f"this tool refuses to send {data!r} - only ?, $$ and {self.allowed[2]!r}")
        self.sent.append(data)
        return self._ser.write(data)

    def __getattr__(self, name):
        return getattr(self._ser, name)


def change_z_steps(ser, value, axis="Z"):
    """ser: an open port wrapped in OnlyZSteps. Returns {'status','before','after','reply','ok'}; raises if not safe."""
    read_lines(ser, 2.5)                                  # boot lines of a controller that restarts on connect
    ser.write(b"?")
    status = [ln for ln in read_lines(ser, 1.0) if ln.startswith("<")]
    if not status:
        raise RuntimeError("no status from the controller - nothing written")
    state = status[-1][1:].split("|")[0].split(":")[0]
    if state not in ("Idle", "Alarm"):
        raise RuntimeError(f"controller is {state} - must be Idle or Alarm (nothing moving); nothing written")
    ser.write(b"$$\n")
    before, _ = parse_settings(read_lines(ser, 6.0, until_ok=True))
    n = AXES[axis]
    if n not in before:
        raise RuntimeError(f"could not read ${n} before the change - nothing written")
    ser.write(write_command(value, axis))
    reply = read_lines(ser, 3.0, until_ok=True)
    ser.write(b"$$\n")
    after, _ = parse_settings(read_lines(ser, 6.0, until_ok=True))
    ok = "ok" in reply and abs(after.get(n, -1) - float(value)) < 1e-6 and \
        all(after.get(k) == v for k, v in before.items() if k != n)
    return {"status": status[-1], "before": before, "after": after, "reply": reply, "ok": ok}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    import serial
    if "--value" not in argv:
        print("usage: python machine_z_steps_set.py --value 500 [--port COM4]")
        return 1
    value = float(argv[argv.index("--value") + 1])
    axis = argv[argv.index("--axis") + 1].upper() if "--axis" in argv else "Z"
    n = AXES.get(axis, 0)
    reason = argv[argv.index("--reason") + 1] if "--reason" in argv else "Z ruler check A40 B32 (8 mm for 40 commanded)"
    write_command(value, axis)                             # axis and range check before anything opens
    if studio_connected():
        print("STOP: Studio is connected to the machine - press Disconnect in Studio first (one user per port).")
        return 1
    port = argv[argv.index("--port") + 1] if "--port" in argv else find_port()
    if not port:
        print("STOP: no single CH340 (machine) USB port found - check the cable and power, or give --port COM4")
        return 1
    machine_id = active_machine()
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, 115200, 0.1, 1.0
    s.dtr = s.rts = False
    s.open()
    ser = OnlyZSteps(s, value, axis)
    try:
        r = change_z_steps(ser, value, axis)
    finally:
        s.close()
    result = {"time": dt.datetime.now().isoformat(timespec="seconds"), "machine": machine_id, "port": port,
              "sent": [x.decode().strip() for x in ser.sent], "status_before": r["status"],
              "axis": axis, "steps_before": r["before"].get(n), "steps_after": r["after"].get(n), "reply": r["reply"],
              "other_settings_unchanged": r["ok"], "settings_after": {f"${k}": v for k, v in sorted(r["after"].items())}}
    out_dir = os.path.join(ROOT, "outputs", "machine_settings", machine_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"_{axis.lower()}_steps.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\n=== {axis} STEPS/MM ({machine_id}, {port}) - one setting, nothing moved ===")
    print(f"  status before: {r['status']}")
    print(f"  ${n} before: {r['before'].get(n)}   after: {r['after'].get(n)}   reply: {' '.join(r['reply'])}")
    print("  RESULT: " + (f"OK - only ${n} changed" if r["ok"] else "CHECK - send this output to Claude"))
    print("  HOME AGAIN in Studio before any move.")
    print(f"  saved: {out}")
    try:
        sys.path.insert(0, ROOT)
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, f"Controller setting: {axis} steps/mm (no motion)",
               None, " ; ".join(result["sent"]), f"${n} {r['before'].get(n)} -> {value:g}, nothing else changed",
               f"${n} after {r['after'].get(n)}; reply {' '.join(r['reply'])}; other settings unchanged {r['ok']}",
               "OK" if r["ok"] else "CHECK", "",
               f"machine_z_steps_set.py; machine {machine_id}; {os.path.relpath(out, ROOT)}; "
               f"{reason}; home again before any move")
    except Exception as e:
        print(f"  (log row not written: {e})")
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
