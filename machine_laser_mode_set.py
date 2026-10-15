"""Set ONE controller setting: laser mode $32 (0 = normal spindle mode, 1 = laser mode) - NO MOTION.
Studio v29 paste dispensing (user approval 2026-10-15: "go A"): the pneumatic valve and the servo dispenser's SSR
are on the controller's spindle/laser output. The ball-screw DLC32 came with $32=1 (laser mode, read 2026-10-14):
in laser mode grbl switches that output by itself (off in G0 moves, during stops and pauses) and does not wait for
the moves before M3/M5 - so the paste would not start and stop where the program says. With $32=0, M3/M5/G4 run in
program order after the earlier moves finish. Studio refuses paste runs and the dispenser test until $32=0.
This tool sends only:
  ?    status (must be Idle or Alarm - nothing moving)        $$   all settings (before and after)
  $32=0 or $32=1   the one write, only after the old value was read
and refuses everything else before it reaches the port (the same wrapper as machine_z_steps_set.py). Studio must be
DISCONNECTED (one user per port). Nothing moves and the position is kept, but home in Studio as usual afterwards.
Result: printed summary + outputs/machine_settings/<machine>/<time>_laser_mode.json + one log row.
  python machine_laser_mode_set.py --value 0 [--port COM4 | --port /dev/ttyUSB0]
"""
import datetime as dt
import json
import os
import sys

from machine_settings_check import ROOT, active_machine, find_port, parse_settings, read_lines, studio_connected

SETTING = 32
TRIES = 3


def read_settings(ser):
    """$$ until $32 and $30 are in the reply (up to TRIES times): a controller that restarted when the port opened
    can send a late boot 'ok' that ends the first reply before any setting (seen on the Pi 2026-10-15)."""
    num = {}
    for _ in range(TRIES):
        ser.write(b"$$\n")
        num, _ = parse_settings(read_lines(ser, 6.0, until_ok=True))
        if SETTING in num and 30 in num:
            return num
        read_lines(ser, 1.0)                              # let the rest arrive before asking again
    return num


def write_command(value):
    if str(value) not in ("0", "1"):
        raise ValueError("laser mode $32 must be 0 (normal spindle mode, for paste) or 1 (laser mode)")
    return f"${SETTING}={value}\n".encode("ascii")


class OnlyLaserMode:
    """Serial port wrapper: write() passes only ?, $$ and the one $32=<0|1> line - anything else raises."""

    def __init__(self, ser, value):
        self._ser = ser
        self.allowed = (b"?", b"$$\n", write_command(value))
        self.sent = []

    def write(self, data):
        data = bytes(data)
        if data not in self.allowed:
            raise RuntimeError(f"this tool refuses to send {data!r} - only ?, $$ and {self.allowed[2]!r}")
        self.sent.append(data)
        return self._ser.write(data)

    def __getattr__(self, name):
        return getattr(self._ser, name)


def change_laser_mode(ser, value):
    """ser: an open port wrapped in OnlyLaserMode. Returns {'status','before','after','reply','ok'}."""
    read_lines(ser, 2.5)
    ser.write(b"?")
    status = [ln for ln in read_lines(ser, 1.0) if ln.startswith("<")]
    if not status:
        raise RuntimeError("no status from the controller - nothing written")
    state = status[-1][1:].split("|")[0].split(":")[0]
    if state not in ("Idle", "Alarm"):
        raise RuntimeError(f"controller is {state} - must be Idle or Alarm (nothing moving); nothing written")
    before = read_settings(ser)
    if SETTING not in before:
        raise RuntimeError(f"could not read ${SETTING} before the change - nothing written")
    ser.write(write_command(value))
    reply = read_lines(ser, 3.0, until_ok=True)
    after = read_settings(ser)
    ok = "ok" in reply and after.get(SETTING) == float(value) and \
        all(after.get(k) == v for k, v in before.items() if k != SETTING)
    return {"status": status[-1], "before": before, "after": after, "reply": reply, "ok": ok}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    import serial
    if "--value" not in argv:
        print("usage: python machine_laser_mode_set.py --value 0 [--port COM4]")
        return 1
    value = argv[argv.index("--value") + 1]
    write_command(value)                                   # value check before anything opens
    if studio_connected():
        print("STOP: Studio is connected to the machine - press Disconnect in Studio first (one user per port).")
        return 1
    port = argv[argv.index("--port") + 1] if "--port" in argv else find_port()
    if not port:
        print("STOP: no single CH340 (machine) USB port found - check the cable and power, or give --port")
        return 1
    machine_id = active_machine()
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, 115200, 0.1, 1.0
    s.dtr = s.rts = False
    s.open()
    ser = OnlyLaserMode(s, value)
    try:
        r = change_laser_mode(ser, value)
    finally:
        s.close()
    result = {"time": dt.datetime.now().isoformat(timespec="seconds"), "machine": machine_id, "port": port,
              "sent": [x.decode().strip() for x in ser.sent], "status_before": r["status"],
              "laser_mode_before": r["before"].get(SETTING), "laser_mode_after": r["after"].get(SETTING),
              "max_spindle_30": r["after"].get(30), "reply": r["reply"], "other_settings_unchanged": r["ok"],
              "settings_after": {f"${k}": v for k, v in sorted(r["after"].items())}}
    out_dir = os.path.join(ROOT, "outputs", "machine_settings", machine_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "_laser_mode.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\n=== LASER MODE $32 ({machine_id}, {port}) - one setting, nothing moved ===")
    print(f"  status before: {r['status']}")
    print(f"  $32 before: {r['before'].get(SETTING)}   after: {r['after'].get(SETTING)}   reply: {' '.join(r['reply'])}")
    print(f"  $30 (full output): {r['after'].get(30)}  - paste programs use S1000")
    print("  RESULT: " + ("OK - only $32 changed" if r["ok"] else "CHECK - send this output to Claude"))
    print(f"  saved: {out}")
    try:
        sys.path.insert(0, ROOT)
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Controller setting: laser mode $32 (no motion)",
               None, " ; ".join(result["sent"]), f"$32 {r['before'].get(SETTING)} -> {value}, nothing else changed",
               f"$32 after {r['after'].get(SETTING)}; $30 {r['after'].get(30)}; reply {' '.join(r['reply'])}; "
               f"other settings unchanged {r['ok']}", "OK" if r["ok"] else "CHECK", "",
               f"machine_laser_mode_set.py; machine {machine_id}; {os.path.relpath(out, ROOT)}; Studio v29 paste "
               "needs normal spindle mode")
    except Exception as e:
        print(f"  (log row not written: {e})")
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
