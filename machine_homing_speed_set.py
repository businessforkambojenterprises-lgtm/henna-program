"""Set ONE controller setting: the homing seek rate $25 (mm/min) - NO MOTION.
User 2026-10-16: "can we increase homing speed?". The ball-screw DLC32 homes its fast approach at $25=100 mm/min
(read 2026-10-14): up to about 4 min for X. The slow final touch $24 (50 mm/min, sets the accuracy) is not changed;
Z never moves faster than its max rate $112 (300) anyway. This tool sends only:
  ?    status (must be Idle or Alarm - nothing moving)        $$   all settings (before and after)
  $25=<value>   the one write, value 100 - 1000, only after the old value was read
and refuses everything else before it reaches the port (the same wrapper as machine_laser_mode_set.py). Studio must
be DISCONNECTED (one user per port). Nothing moves; home as usual afterwards.
Result: printed summary + outputs/machine_settings/<machine>/<time>_homing_seek.json + one log row.
  python machine_homing_speed_set.py --value 500 [--port COM4 | --port /dev/ttyUSB0]
"""
import datetime as dt
import json
import os
import sys

from machine_settings_check import ROOT, active_machine, find_port, parse_settings, read_lines, studio_connected

SETTING = 25
MIN_RATE, MAX_RATE = 100.0, 1000.0
TRIES = 3


def write_command(value):
    value = float(value)
    if not MIN_RATE <= value <= MAX_RATE:
        raise ValueError(f"homing seek rate $25 must be {MIN_RATE:g} to {MAX_RATE:g} mm/min")
    return f"${SETTING}={value:.3f}\n".encode("ascii")


class OnlyHomingSeek:
    """Serial port wrapper: write() passes only ?, $$ and the one $25=<value> line - anything else raises."""

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


def read_settings(ser):
    """$$ until $25 and $24 are in the reply (a late boot 'ok' can end the first reply, as in the laser-mode tool)."""
    num = {}
    for _ in range(TRIES):
        ser.write(b"$$\n")
        num, _ = parse_settings(read_lines(ser, 6.0, until_ok=True))
        if SETTING in num and 24 in num:
            return num
        read_lines(ser, 1.0)
    return num


def change_homing_seek(ser, value):
    """ser: an open port wrapped in OnlyHomingSeek. Returns {'status','before','after','reply','ok'}."""
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
    ok = "ok" in reply and abs(after.get(SETTING, -1) - float(value)) < 1e-6 and \
        all(after.get(k) == v for k, v in before.items() if k != SETTING)
    return {"status": status[-1], "before": before, "after": after, "reply": reply, "ok": ok}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    import serial
    if "--value" not in argv:
        print("usage: python machine_homing_speed_set.py --value 500 [--port COM4]")
        return 1
    value = float(argv[argv.index("--value") + 1])
    write_command(value)
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
    ser = OnlyHomingSeek(s, value)
    try:
        r = change_homing_seek(ser, value)
    finally:
        s.close()
    result = {"time": dt.datetime.now().isoformat(timespec="seconds"), "machine": machine_id, "port": port,
              "sent": [x.decode().strip() for x in ser.sent], "status_before": r["status"],
              "homing_seek_before": r["before"].get(SETTING), "homing_seek_after": r["after"].get(SETTING),
              "homing_locate_24": r["after"].get(24), "z_max_rate_112": r["after"].get(112), "reply": r["reply"],
              "other_settings_unchanged": r["ok"], "settings_after": {f"${k}": v for k, v in sorted(r["after"].items())}}
    out_dir = os.path.join(ROOT, "outputs", "machine_settings", machine_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "_homing_seek.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print(f"\n=== HOMING SEEK $25 ({machine_id}, {port}) - one setting, nothing moved ===")
    print(f"  status before: {r['status']}")
    print(f"  $25 before: {r['before'].get(SETTING)}   after: {r['after'].get(SETTING)}   reply: {' '.join(r['reply'])}")
    print(f"  $24 (slow final touch, unchanged): {r['after'].get(24)}   $112 (Z max rate): {r['after'].get(112)}")
    print("  RESULT: " + ("OK - only $25 changed" if r["ok"] else "CHECK - send this output to Claude"))
    print(f"  saved: {out}")
    try:
        sys.path.insert(0, ROOT)
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Controller setting: homing seek $25 (no motion)",
               None, " ; ".join(result["sent"]), f"$25 {r['before'].get(SETTING)} -> {value:g}, nothing else changed",
               f"$25 after {r['after'].get(SETTING)}; $24 {r['after'].get(24)}; reply {' '.join(r['reply'])}; "
               f"other settings unchanged {r['ok']}", "OK" if r["ok"] else "CHECK", "",
               f"machine_homing_speed_set.py; machine {machine_id}; {os.path.relpath(out, ROOT)}; user: faster homing")
    except Exception as e:
        print(f"  (log row not written: {e})")
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
