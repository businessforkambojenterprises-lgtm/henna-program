"""Set the X and Y of a machine's scan position - NO MOTION, no controller connection.

User 2026-10-26: "chnge scan position" (head jogged to X86.206 Y235.000 Z-0.5, the camera over the hand on its finger
pad). The camera takes the hand's picture from there. It does NOT change which skin is reachable (travel Z and the Z
floor do that), nor the measured calibration - the camera-to-nozzle model (tool XY = head XY + R cam + t, skin Z =
head Z - (depth - k)) is relative to the head, so it holds at any head position.

What it changes (both must agree - Studio checks it, machines.Profiles.check_consistent):
  machines/registry.json   motion.scan_pose_mpos[0:2] of the machine (the previous registry is kept in machines/history/)
  its approved calibration file   capture_pose_mpos[0:2] (a copy is kept in machines/history/ first)
What it keeps: the scan Z, travel Z, the Z floor, the work area.
Rules: X and Y inside the saved work area (0..X, 0..Y).
After it: the daily-check reference was recorded at the old position - set a new one (Admin -> Daily check -> Set the
reference, bed as every day, no hand). Jobs scanned before keep their own head position.
Studio must be CLOSED (it reads the values when it starts).
  python machine_scan_xy_set.py --x 86.2 --y 235 --confirm            (the active machine)
Result: printed summary + outputs/machine_settings/<machine>/<time>_scan_xy.json + one log row.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def check_value(x, y, work_area):
    x, y = round(float(x), 3), round(float(y), 3)
    wx, wy = (float(v) for v in work_area)
    if not (0 <= x <= wx and 0 <= y <= wy):
        raise ValueError(f"X {x:g} Y {y:g} is outside the work area (X 0-{wx:g}, Y 0-{wy:g})")
    return x, y


def set_scan_xy(profiles, machine_id, x, y, stamp=None):
    """-> record dict. Changes the calibration file (if any) and the registry; keeps copies of both."""
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    m = profiles.get(machine_id)
    motion = profiles.motion(m)
    old = [float(v) for v in motion["scan_pose_mpos"]]
    x, y = check_value(x, y, m["work_area_mm"])
    new = [x, y, old[2]]
    hist = os.path.join(os.path.dirname(profiles.registry_path), "history")
    os.makedirs(hist, exist_ok=True)
    shutil.copyfile(profiles.registry_path, os.path.join(hist, f"registry.before-scan-xy-{stamp}.json"))
    cal_path = os.path.join(profiles.root, m["calibration"]) if m.get("calibration") else None
    if cal_path:
        shutil.copyfile(cal_path, os.path.join(hist, f"{os.path.basename(cal_path)[:-5]}.before-scan-xy-{stamp}.json"))
        with open(cal_path, encoding="utf-8") as f:
            cal = json.load(f)
        cal["capture_pose_mpos"] = new
        tmp = cal_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cal, f, indent=2)
            f.write("\n")
        os.replace(tmp, cal_path)
    note = f"scan X/Y {old[0]:g},{old[1]:g} -> {x:g},{y:g} ({stamp}, machine_scan_xy_set.py; Z, travel Z, floor unchanged)"

    def change(e):
        e["motion"]["scan_pose_mpos"] = new
        e["motion"]["scan_xy_changed"] = note
    profiles._edit(machine_id, change)
    profiles.check_consistent(profiles.get(machine_id))
    return {"machine": machine_id, "time": stamp, "scan_pose_before": old, "scan_pose": new,
            "calibration_file": m.get("calibration"), "note": note}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine", help="machine id (default: the active machine)")
    ap.add_argument("--x", type=float, required=True)
    ap.add_argument("--y", type=float, required=True)
    ap.add_argument("--confirm", action="store_true", help="really change it")
    a = ap.parse_args(argv)
    sys.path.insert(0, ROOT)
    from henna_preview.machines import Profiles
    from machine_travel_z_set import studio_running
    profiles = Profiles(ROOT)
    mid = a.machine or profiles.active_id()
    if not mid:
        print("STOP: no machine chosen - give --machine <id>")
        return 1
    m = profiles.get(mid)
    mo = profiles.motion(m)
    try:
        x, y = check_value(a.x, a.y, m["work_area_mm"])
    except ValueError as e:
        print(f"STOP: {e}")
        return 1
    print(f"{m['name']}: scan position {mo['scan_pose_mpos']} -> X {x:g} Y {y:g} (Z {mo['scan_pose_mpos'][2]:g}, "
          f"travel Z {mo['travel_z']:g} and floor {mo['firmware_z_floor']:g} unchanged)")
    if not a.confirm:
        print("Nothing changed - add --confirm to change it.")
        return 0
    if studio_running():
        print("STOP: Henna Studio is running - close it first (it reads these values when it starts)")
        return 1
    rec = set_scan_xy(profiles, mid, x, y)
    out = os.path.join(ROOT, "outputs", "machine_settings", mid)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{rec['time']}_scan_xy.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    try:
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Scan position X/Y changed", None,
               "machine_scan_xy_set.py", "scan X/Y inside the work area", f"{rec['scan_pose_before']} -> {rec['scan_pose']}",
               "changed", "", f"user 2026-10-26: chnge scan position; machine {mid}; set a new daily-check reference")
    except Exception as e:                              # noqa: BLE001 - the change is made; say the log failed
        print(f"(log row not written: {e})")
    print(f"Done: scan position now {rec['scan_pose']} (copies in machines/history/). Start Studio again, then set a "
          "new daily-check reference.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
