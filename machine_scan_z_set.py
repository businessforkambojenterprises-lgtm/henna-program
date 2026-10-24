"""Set the height of a machine's scan position (its Z) - NO MOTION, no controller connection.

User 2026-10-24: "this is new Z0" -> "yes, scan Z -1": the camera takes the hand's picture higher up (ball-screw
machine: Z -5 -> Z -1, 4 mm further from the hand: a slightly wider view). It does NOT change which skin is
reachable (travel Z and the Z floor do that).

What it changes (both must agree - Studio checks it, machines.Profiles.check_consistent):
  machines/registry.json   motion.scan_pose_mpos[2] of the machine (the previous registry is kept in machines/history/)
  its approved calibration file   capture_pose_mpos[2] (a copy is kept in machines/history/ first)
What it keeps: the scan position's X and Y, travel Z, the Z floor, the measured calibration - the camera-to-nozzle
model (tool XY = head XY + R cam + t, skin Z = head Z - (depth - k)) holds at any head height.
Rules: the scan Z must be at or below travel Z (the move goes up to travel Z, across, then down to it - machine_motion)
and above the Z floor.
After it: the daily-check reference was recorded at the old height - set a new one (Admin -> Daily check -> Set the
reference, bed as every day, no hand). Jobs scanned before keep their own head position.
Studio must be CLOSED (it reads the values when it starts).
  python machine_scan_z_set.py --scan-z -1 --confirm            (the active machine)
Result: printed summary + outputs/machine_settings/<machine>/<time>_scan_z.json + one log row.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def check_value(scan_z, motion):
    z = float(scan_z)
    travel = float(motion["travel_z"])
    floor = float(motion["firmware_z_floor"])
    if z > travel + 1e-9:
        raise ValueError(f"the scan Z must be at or below travel Z {travel:g} (the move goes up to travel Z first)")
    if z <= floor:
        raise ValueError(f"the scan Z must be above the Z floor {floor:g}")
    return z


def set_scan_z(profiles, machine_id, scan_z, stamp=None):
    """-> record dict. Changes the calibration file (if any) and the registry; keeps copies of both."""
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    m = profiles.get(machine_id)
    motion = profiles.motion(m)
    old = [float(v) for v in motion["scan_pose_mpos"]]
    z = check_value(scan_z, motion)
    new = [old[0], old[1], z]
    hist = os.path.join(os.path.dirname(profiles.registry_path), "history")
    os.makedirs(hist, exist_ok=True)
    cal_path = os.path.join(profiles.root, m["calibration"]) if m.get("calibration") else None
    if cal_path:
        shutil.copyfile(cal_path, os.path.join(hist, f"{os.path.basename(cal_path)[:-5]}.before-scan-z-{stamp}.json"))
        with open(cal_path, encoding="utf-8") as f:
            cal = json.load(f)
        cal["capture_pose_mpos"] = new
        tmp = cal_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cal, f, indent=2)
            f.write("\n")
        os.replace(tmp, cal_path)
    note = f"scan Z {old[2]:g} -> {z:g} ({stamp}, machine_scan_z_set.py; X/Y, travel Z and floor unchanged)"

    def change(x):
        x["motion"]["scan_pose_mpos"] = new
        x["motion"]["scan_z_changed"] = note
    profiles._edit(machine_id, change)
    profiles.check_consistent(profiles.get(machine_id))
    return {"machine": machine_id, "time": stamp, "scan_pose_before": old, "scan_pose": new,
            "calibration_file": m.get("calibration"), "note": note}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine", help="machine id (default: the active machine)")
    ap.add_argument("--scan-z", type=float, required=True)
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
        z = check_value(a.scan_z, mo)
    except ValueError as e:
        print(f"STOP: {e}")
        return 1
    print(f"{m['name']}: scan position {mo['scan_pose_mpos']} -> Z {z:g} (X/Y, travel Z {mo['travel_z']:g} and floor "
          f"{mo['firmware_z_floor']:g} unchanged)")
    if not a.confirm:
        print("Nothing changed - add --confirm to change it.")
        return 0
    if studio_running():
        print("STOP: Henna Studio is running - close it first (it reads these values when it starts)")
        return 1
    rec = set_scan_z(profiles, mid, z)
    out = os.path.join(ROOT, "outputs", "machine_settings", mid)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{rec['time']}_scan_z.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    try:
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Scan position height changed", None,
               "machine_scan_z_set.py", "scan Z at or below travel Z", f"{rec['scan_pose_before']} -> {rec['scan_pose']}",
               "changed", "", f"user 2026-10-24: yes, scan Z -1; machine {mid}; set a new daily-check reference")
    except Exception as e:                              # noqa: BLE001 - the change is made; say the log failed
        print(f"(log row not written: {e})")
    print(f"Done: scan position now {rec['scan_pose']} (copies in machines/history/). Start Studio again, then set a "
          "new daily-check reference.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
