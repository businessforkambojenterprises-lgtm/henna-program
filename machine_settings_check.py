"""Read a machine's controller settings - READ-ONLY, NO MOTION (first step of calibrating a machine).

Sends only three GRBL queries, and the serial port is wrapped so nothing else can be sent:
  ?    status (state and position)          $I   build info (firmware)          $$   all settings
From the settings it reports what a calibration needs: steps per mm, maximum travel (the work area), speeds and
accelerations, homing (on, direction, pull-off), soft/hard limits and laser mode. Studio must be DISCONNECTED
(one user per port). Result: printed summary + outputs/machine_settings/<machine>/<time>.json + one log row.
  python machine_settings_check.py [--port COM3]
"""
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
ALLOWED = (b"?", b"$I\n", b"$$\n")
CH340 = (0x1A86, 0x7523)

NAMES = {0: "step pulse (us)", 1: "step idle delay (ms)", 2: "step port invert mask", 3: "direction invert mask",
         4: "step enable invert", 5: "limit pins invert", 6: "probe pin invert", 10: "status report mask",
         11: "junction deviation (mm)", 12: "arc tolerance (mm)", 13: "report inches", 20: "soft limits",
         21: "hard limits", 22: "homing cycle", 23: "homing direction invert mask", 24: "homing feed (mm/min)",
         25: "homing seek (mm/min)", 26: "homing debounce (ms)", 27: "homing pull-off (mm)", 30: "max spindle speed",
         31: "min spindle speed", 32: "laser mode", 100: "X steps/mm", 101: "Y steps/mm", 102: "Z steps/mm",
         110: "X max rate (mm/min)", 111: "Y max rate (mm/min)", 112: "Z max rate (mm/min)",
         120: "X acceleration (mm/s2)", 121: "Y acceleration (mm/s2)", 122: "Z acceleration (mm/s2)",
         130: "X max travel (mm)", 131: "Y max travel (mm)", 132: "Z max travel (mm)"}
_NUM = re.compile(r"^\$(\d+)=(-?[\d.]+)")
_NAMED = re.compile(r"^\$([A-Za-z][\w/]*)=(.*)$")


class OnlyQueries:
    """Serial port wrapper: write() passes only ?, $I and $$ - anything else raises before reaching the port."""

    def __init__(self, ser):
        self._ser = ser
        self.sent = []

    def write(self, data):
        data = bytes(data)
        if data not in ALLOWED:
            raise RuntimeError(f"this tool refuses to send {data!r} - only ?, $I and $$ are allowed")
        self.sent.append(data)
        return self._ser.write(data)

    def __getattr__(self, name):
        return getattr(self._ser, name)


# ---------- pure helpers (tests/test_machine_settings_check.py) ----------
def parse_settings(lines):
    """'$100=80.000' / '$Axes/X/StepsPerMm=80' lines -> ({number: value}, {name: text})."""
    num, named = {}, {}
    for ln in lines:
        m = _NUM.match(ln.strip())
        if m:
            num[int(m.group(1))] = float(m.group(2))
            continue
        m = _NAMED.match(ln.strip())
        if m:
            named[m.group(1)] = m.group(2).strip()
    return num, named


def summarize(num):
    """The numbers a calibration needs, in words. Missing settings are None."""
    g = num.get

    def axes(base):
        return {a: g(base + i) for i, a in enumerate("XYZ")}

    def mask_axes(v):
        return None if v is None else [a for i, a in enumerate("XYZ") if int(v) >> i & 1]

    return {"steps_per_mm": axes(100), "max_travel_mm": axes(130), "max_rate_mm_min": axes(110),
            "acceleration_mm_s2": axes(120),
            "homing": {"enabled": None if g(22) is None else bool(g(22)), "pull_off_mm": g(27),
                       "seek_mm_min": g(25), "feed_mm_min": g(24),
                       "direction_inverted_axes": mask_axes(g(23))},
            "soft_limits": None if g(20) is None else bool(g(20)),
            "hard_limits": None if g(21) is None else bool(g(21)),
            "laser_mode": None if g(32) is None else bool(g(32)),
            "direction_inverted_axes": mask_axes(g(3)),
            "report_inches": None if g(13) is None else bool(g(13))}


def notes(s):
    """Plain-language findings for the calibration plan."""
    out = []
    mt = s["max_travel_mm"]
    if all(v is not None for v in mt.values()):
        out.append(f"work area from the settings: X {mt['X']:g} x Y {mt['Y']:g} mm, Z range {mt['Z']:g} mm "
                   "(to be checked with a ruler before Studio uses it)")
    if s["homing"]["enabled"] is False:
        out.append("HOMING IS OFF - Studio's Home ($HZ, $H) needs it on")
    if s["soft_limits"] is False:
        out.append("soft limits are off: the controller does not stop moves beyond its travel - Studio's own limits matter")
    if s["laser_mode"]:
        out.append("laser mode is on (MKS default) - harmless for hover runs; matters later for a dispenser on the spindle output")
    if s["report_inches"]:
        out.append("REPORTS IN INCHES - Studio reads mm; must be off")
    return out


# ---------- the check ----------
def studio_connected():
    try:
        req = urllib.request.Request("http://127.0.0.1:8765/api/machine/status", headers={"Host": "127.0.0.1:8765"})
        with urllib.request.urlopen(req, timeout=1) as r:
            return bool(json.loads(r.read().decode()).get("connected"))
    except Exception:
        return False


def find_port():
    import serial.tools.list_ports as lp
    ch = [p.device for p in lp.comports() if (p.vid, p.pid) == CH340]
    return ch[0] if len(ch) == 1 else None


def read_lines(ser, seconds, until_ok=False):
    out, buf, end = [], b"", time.monotonic() + seconds
    while time.monotonic() < end:
        chunk = ser.read(256)
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            raw, buf = buf.split(b"\n", 1)
            line = raw.decode("ascii", "replace").strip()
            if line:
                out.append(line)
                if until_ok and (line == "ok" or line.lower().startswith("error")):
                    return out
    return out


def active_machine():
    try:
        with open(os.path.join(ROOT, "outputs", "active_machine.json"), encoding="utf-8") as f:
            return json.load(f).get("id") or "unknown"
    except (OSError, ValueError):
        return "unknown"


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    import serial
    if studio_connected():
        print("STOP: Studio is connected to the machine - press Disconnect in Studio first (one user per port).")
        return 1
    port = argv[argv.index("--port") + 1] if "--port" in argv else find_port()
    if not port:
        print("STOP: no single CH340 (machine) USB port found - check the cable and power, or give --port COM3")
        return 1
    machine_id = active_machine()
    s = serial.Serial()
    s.port, s.baudrate, s.timeout, s.write_timeout = port, 115200, 0.1, 1.0
    s.dtr = s.rts = False
    s.open()
    ser = OnlyQueries(s)
    try:
        boot = read_lines(ser, 2.5)                       # a controller that restarts on connect prints its boot lines
        ser.write(b"?")
        status = [ln for ln in read_lines(ser, 1.0) if ln.startswith("<")]
        ser.write(b"$I\n")
        build = read_lines(ser, 3.0, until_ok=True)
        ser.write(b"$$\n")
        settings_lines = read_lines(ser, 6.0, until_ok=True)
    finally:
        s.close()
    num, named = parse_settings(settings_lines)
    summ = summarize(num)
    result = {"time": dt.datetime.now().isoformat(timespec="seconds"), "machine": machine_id, "port": port,
              "sent": [x.decode().strip() for x in ser.sent], "boot_lines": boot, "status": status[-1:] or None,
              "build_info": build, "settings": {f"${k}": v for k, v in sorted(num.items())},
              "settings_named": named, "summary": summ, "notes": notes(summ)}
    out_dir = os.path.join(ROOT, "outputs", "machine_settings", machine_id)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"\n=== MACHINE SETTINGS ({machine_id}, {port}) - read only, nothing moved ===")
    print(f"  status: {result['status']}")
    print(f"  firmware: {' | '.join(ln for ln in build if ln != 'ok')}")
    if not num and not named:
        print("  NO SETTINGS RECEIVED - send this output to Claude")
    for k in sorted(num):
        print(f"  ${k:<4} = {num[k]:<10g} {NAMES.get(k, '')}")
    for k, v in sorted(named.items())[:60]:
        print(f"  ${k} = {v}")
    for n in result["notes"]:
        print(f"  NOTE: {n}")
    print(f"  saved: {out}")
    try:
        sys.path.insert(0, ROOT)
        from henna_preview.web_log import append
        st, mt = summ["steps_per_mm"], summ["max_travel_mm"]
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Machine settings read (no motion)", None,
               "?, $I, $$ only", "controller settings for the calibration",
               f"steps/mm X{st['X']} Y{st['Y']} Z{st['Z']}; max travel X{mt['X']} Y{mt['Y']} Z{mt['Z']}; "
               f"homing {summ['homing']['enabled']} pull-off {summ['homing']['pull_off_mm']}; soft limits "
               f"{summ['soft_limits']}; laser mode {summ['laser_mode']}", "recorded", "",
               f"machine_settings_check.py; machine {machine_id}; {os.path.relpath(out, ROOT)}")
    except Exception as e:
        print(f"  (log row not written: {e})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
