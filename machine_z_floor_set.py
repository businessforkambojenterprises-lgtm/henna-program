"""Set a machine's Z floor (the lowest Z Studio will ever send) - NO MOTION, no controller connection.

User 2026-10-31: the belt drive's Z went down to -190 (its carriage end) with the controller's soft limits off, far below
the -97 floor Studio kept. The floor is one number in two files (Studio checks they agree, machines.Profiles.check_consistent):
  machines/registry.json   motion.firmware_z_floor of the machine (the previous registry is kept in machines/history/)
  its approved calibration file   "firmware_z_floor" (a copy is kept in machines/history/ first)
It keeps: the scan position, travel Z, the calibration's measured values. The floor must be below the scan Z and travel Z;
it sits END_MARGIN_MM above the carriage end the user measured.
The machine id is REQUIRED (there is no default): the ball-screw machine's -49 stays as it is.
The controller has its own limit, $132 (Z max travel, positive) - set it to the carriage end by hand, then turn $20 back on.
Studio must be CLOSED (it reads the values when it starts).
  python machine_z_floor_set.py --machine belt-drive --floor -189 --confirm
Result: printed summary + outputs/machine_settings/<machine>/<time>_z_floor.json + one log row.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
END_MARGIN_MM = 1.0                  # advice only: the floor is this far above the carriage end


def check_value(floor, motion):
    fl = float(floor)
    scan_z, travel_z = float(motion["scan_pose_mpos"][2]), float(motion["travel_z"])
    if fl >= min(scan_z, travel_z):
        raise ValueError(f"the floor must be below the scan Z {scan_z:g} and travel Z {travel_z:g}")
    return fl


def set_floor(profiles, machine_id, floor, stamp=None):
    """-> record dict. Changes the calibration file (if any) and the registry; keeps copies of both."""
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    m = profiles.get(machine_id)
    motion = profiles.motion(m)
    old = float(motion["firmware_z_floor"])
    fl = check_value(floor, motion)
    hist = os.path.join(os.path.dirname(profiles.registry_path), "history")
    os.makedirs(hist, exist_ok=True)
    cal_path = os.path.join(profiles.root, m["calibration"]) if m.get("calibration") else None
    if cal_path:
        shutil.copyfile(cal_path, os.path.join(hist, f"{os.path.basename(cal_path)[:-5]}.before-z-floor-{stamp}.json"))
        with open(cal_path, encoding="utf-8") as f:
            cal = json.load(f)
        cal["firmware_z_floor"] = fl
        tmp = cal_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(cal, f, indent=2)
            f.write("\n")
        os.replace(tmp, cal_path)
    note = f"Z floor {old:g} -> {fl:g} ({stamp}, machine_z_floor_set.py; scan position and travel Z unchanged)"

    def change(x):
        x["motion"]["firmware_z_floor"] = fl
        x["motion"]["z_floor_changed"] = note
    profiles._edit(machine_id, change)
    profiles.check_consistent(profiles.get(machine_id))
    return {"machine": machine_id, "time": stamp, "z_floor_before": old, "z_floor": fl,
            "controller_132_to_set": round(abs(fl) + END_MARGIN_MM, 3), "calibration_file": m.get("calibration"),
            "note": note}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine", required=True, help="machine id (required - no default)")
    ap.add_argument("--floor", type=float, required=True, help="Z floor, negative (Z 0 is the top)")
    ap.add_argument("--confirm", action="store_true", help="really change it")
    a = ap.parse_args(argv)
    sys.path.insert(0, ROOT)
    from henna_preview.machines import Profiles
    from machine_travel_z_set import studio_running
    profiles = Profiles(ROOT)
    m = profiles.get(a.machine)
    mo = profiles.motion(m)
    try:
        fl = check_value(a.floor, mo)
    except ValueError as e:
        print(f"STOP: {e}")
        return 1
    print(f"{m['name']}: Z floor {float(mo['firmware_z_floor']):g} -> {fl:g} (scan Z {mo['scan_pose_mpos'][2]:g} "
          f"and travel Z {mo['travel_z']:g} unchanged)")
    print(f"  controller $132 to match: {abs(fl) + END_MARGIN_MM:g} (type it on the controller's page, then $20=1)")
    if not a.confirm:
        print("Nothing changed - add --confirm to change it.")
        return 0
    if studio_running():
        print("STOP: Henna Studio is running - close it first (it reads these values when it starts)")
        return 1
    rec = set_floor(profiles, a.machine, fl)
    out = os.path.join(ROOT, "outputs", "machine_settings", a.machine)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{rec['time']}_z_floor.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    try:
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Z floor changed", None,
               "machine_z_floor_set.py", "floor below scan and travel Z", f"{rec['z_floor_before']} -> {rec['z_floor']}",
               "changed", "", f"user 2026-10-31: belt drive Z end -190; machine {a.machine}")
    except Exception as e:                              # noqa: BLE001 - the change is made; say the log failed
        print(f"(log row not written: {e})")
    print(f"DONE: Z floor is {fl:g}. Set the controller's $132 to {rec['controller_132_to_set']:g}, then $20=1.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
