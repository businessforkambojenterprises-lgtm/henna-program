"""Set a machine's travel height (travel Z) - NO MOTION, no controller connection.

User 2026-10-22: "fix this anyhow in the software, no hardware should be touched" - a real-hand design was refused
because its travel lines crossed skin at Z -12, 7 mm under travel Z -5 where 9 mm are needed. The Z carriage stops at
Z 0 (home, the top); the ball-screw machine travelled 5 mm below it. Raising travel Z towards Z 0 gives every travel
move that much more room over the hand, without touching the head.

What it changes (both must agree - Studio checks it, machines.Profiles.check_consistent):
  machines/registry.json   motion.travel_z of the machine (the previous registry is kept in machines/history/)
  its approved calibration file   "travel_z" (a copy is kept in machines/history/ first)
What it keeps: the scan position (Z -5 on the ball-screw machine - the calibration and the daily-check reference were
made there), the Z floor, the calibration's measured values. travel Z must be at or above the scan Z, at most
MAX_TRAVEL_Z (0.5 mm below the top) and above the Z floor.
After it: programs written at the old travel Z are refused (their first and last moves name it) - write them again;
the normal Jog moves X / Y only at the new travel height or higher.
Studio must be CLOSED (it reads the values when it starts).
  python machine_travel_z_set.py --travel-z -1 --confirm            (the active machine)
  python machine_travel_z_set.py --machine ball-screw --travel-z -1 --confirm
Result: printed summary + outputs/machine_settings/<machine>/<time>_travel_z.json.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
MAX_TRAVEL_Z = -0.5
GAP_MM = 9.0                         # nozzle height (6) + 3 mm: travel Z must be this far above the skin under it


def studio_running(url="http://127.0.0.1:8765/api/machines"):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"Host": "127.0.0.1:8765"}), timeout=1):
            return True
    except Exception:                                   # noqa: BLE001 - no answer: not running
        return False


def check_value(travel_z, motion):
    tz = float(travel_z)
    scan_z = float(motion["scan_pose_mpos"][2])
    floor = float(motion["firmware_z_floor"])
    if tz > MAX_TRAVEL_Z + 1e-9:
        raise ValueError(f"travel Z must be {MAX_TRAVEL_Z:g} or lower (Z 0 is the top, where homing stops)")
    if tz < scan_z - 1e-9:
        raise ValueError(f"travel Z must be at or above the scan position's Z {scan_z:g}")
    if tz <= floor:
        raise ValueError(f"travel Z must be above the Z floor {floor:g}")
    return tz


def set_travel_z(profiles, machine_id, travel_z, stamp=None):
    """-> record dict. Changes the calibration file (if any) and the registry; keeps copies of both."""
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    m = profiles.get(machine_id)
    motion = profiles.motion(m)
    old = float(motion["travel_z"])
    tz = check_value(travel_z, motion)
    hist = os.path.join(os.path.dirname(profiles.registry_path), "history")
    os.makedirs(hist, exist_ok=True)
    cal_path = os.path.join(profiles.root, m["calibration"]) if m.get("calibration") else None
    if cal_path:
        backup = os.path.join(hist, f"{os.path.basename(cal_path)[:-5]}.before-travel-z-{stamp}.json")
        shutil.copyfile(cal_path, backup)
        with open(cal_path, encoding="utf-8") as f:
            cal = json.load(f)
        cal["travel_z"] = tz
        tmp = cal_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cal, f, indent=2)
            f.write("\n")
        os.replace(tmp, cal_path)
    note = f"travel Z {old:g} -> {tz:g} ({stamp}, machine_travel_z_set.py; scan position and floor unchanged)"

    def change(x):
        x["motion"]["travel_z"] = tz
        x["motion"]["travel_z_changed"] = note
    profiles._edit(machine_id, change)
    profiles.check_consistent(profiles.get(machine_id))
    return {"machine": machine_id, "time": stamp, "travel_z_before": old, "travel_z": tz,
            "calibration_file": m.get("calibration"), "skin_allowed_under_travel_up_to_z": round(tz - GAP_MM, 2),
            "note": note}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine", help="machine id (default: the active machine)")
    ap.add_argument("--travel-z", type=float, required=True)
    ap.add_argument("--confirm", action="store_true", help="really change it")
    a = ap.parse_args(argv)
    sys.path.insert(0, ROOT)
    from henna_preview.machines import Profiles
    profiles = Profiles(ROOT)
    mid = a.machine or profiles.active_id()
    if not mid:
        print("STOP: no machine chosen - give --machine <id>")
        return 1
    m = profiles.get(mid)
    old = float(profiles.motion(m)["travel_z"])
    try:
        tz = check_value(a.travel_z, profiles.motion(m))
    except ValueError as e:
        print(f"STOP: {e}")
        return 1
    print(f"{m['name']}: travel Z {old:g} -> {tz:g} (scan position Z {profiles.motion(m)['scan_pose_mpos'][2]:g} "
          f"and floor {profiles.motion(m)['firmware_z_floor']:g} unchanged)")
    print(f"  skin under the travel lines allowed up to Z {old - GAP_MM:g} now, Z {tz - GAP_MM:g} after")
    if not a.confirm:
        print("Nothing changed - add --confirm to change it.")
        return 0
    if studio_running():
        print("STOP: Henna Studio is running - close it first (it reads these values when it starts)")
        return 1
    rec = set_travel_z(profiles, mid, tz)
    out = os.path.join(ROOT, "outputs", "machine_settings", mid)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{rec['time']}_travel_z.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    print(f"DONE: travel Z is {tz:g}. Start Studio, then write the program again (Program step).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
