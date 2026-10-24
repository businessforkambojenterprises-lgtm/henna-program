"""The Z home switch was moved - record it and move the Z floor with it. NO MOTION, no controller connection.

User 2026-10-24: "floor z-40 is also changed, i've changed position of limit switches" - the Z switch moved up:
the tip at Z 0 is now 80 mm above the bed (was 71), so Z 0 is 9 mm higher and every machine Z names a point 9 mm
higher than before. The carriage's physical bottom did not move: the floor (kept about 1.2 mm above it) goes down by the
same amount (ball-screw: -40 -> -49, the tip stays at about 31 mm above the bed there).

What it changes (registry and approved calibration agree - machines.Profiles.check_consistent):
  firmware_z_floor          old floor - moved_up (or --floor)
  z_home_moved_mm           in the calibration: the total the switch moved up since the calibration was made
                            (webui_server_v27._paper_z uses it for "mm above the bed")
  z_home_moved_at           when (webui_server_v29 refuses designs, programs and runs from scans made before it:
                            their heights are in the old numbers)
What it keeps: travel Z and the scan position as numbers (they are read in the new numbers from now on), the measured
calibration (camera and nozzle did not move against each other: k and X/Y still hold).
Check afterwards (machine, at the E-stop): Admin -> Jog Z slowly down to the new floor; the tip should be about 31 mm
above the bed and nothing may touch. Set a new daily-check reference. Studio must be CLOSED.
  python machine_z_home_moved.py --moved-up 9 --confirm            (the active machine; floor -40 -> -49)
Result: printed summary + outputs/machine_settings/<machine>/<time>_z_home_moved.json + one log row.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def new_floor(motion, moved_up, floor=None):
    up = float(moved_up)
    if not 0.0 < up <= 30.0:
        raise ValueError("the switch must have moved up by more than 0 and at most 30 mm")
    f = round(float(motion["firmware_z_floor"]) - up, 3) if floor is None else float(floor)
    if f >= float(motion["scan_pose_mpos"][2]):
        raise ValueError(f"the floor must be below the scan position's Z {motion['scan_pose_mpos'][2]:g}")
    return f


def record_move(profiles, machine_id, moved_up, floor=None, stamp=None, now=None):
    now = now or dt.datetime.now()
    stamp = stamp or now.strftime("%Y%m%d-%H%M%S")
    m = profiles.get(machine_id)
    motion = profiles.motion(m)
    old_floor = float(motion["firmware_z_floor"])
    f = new_floor(motion, moved_up, floor)
    at = now.isoformat(timespec="seconds")
    hist = os.path.join(os.path.dirname(profiles.registry_path), "history")
    os.makedirs(hist, exist_ok=True)
    total = float(moved_up)
    if m.get("calibration"):
        cal_path = os.path.join(profiles.root, m["calibration"])
        shutil.copyfile(cal_path, os.path.join(hist, f"{os.path.basename(cal_path)[:-5]}.before-z-home-{stamp}.json"))
        with open(cal_path, encoding="utf-8") as fh:
            cal = json.load(fh)
        total = round(float(cal.get("z_home_moved_mm") or 0.0) + float(moved_up), 3)
        cal.update(firmware_z_floor=f, z_home_moved_mm=total, z_home_moved_at=at)
        tmp = cal_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(cal, fh, indent=2)
            fh.write("\n")
        os.replace(tmp, cal_path)
    note = (f"Z home switch moved up {float(moved_up):g} mm ({stamp}, machine_z_home_moved.py): floor {old_floor:g} -> "
            f"{f:g}; travel Z and scan position unchanged as numbers")

    def change(x):
        x["motion"]["firmware_z_floor"] = f
        x["motion"]["z_home_moved"] = note
    profiles._edit(machine_id, change)
    profiles.check_consistent(profiles.get(machine_id))
    return {"machine": machine_id, "time": stamp, "at": at, "moved_up_mm": float(moved_up), "total_moved_mm": total,
            "floor_before": old_floor, "floor": f, "note": note}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine", help="machine id (default: the active machine)")
    ap.add_argument("--moved-up", type=float, required=True, help="mm the Z home switch moved up (tip at Z 0 now higher)")
    ap.add_argument("--floor", type=float, help="new Z floor (default: old floor - moved-up)")
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
        f = new_floor(mo, a.moved_up, a.floor)
    except ValueError as e:
        print(f"STOP: {e}")
        return 1
    print(f"{m['name']}: Z home moved up {a.moved_up:g} mm -> floor {mo['firmware_z_floor']:g} -> {f:g} "
          f"(travel Z {mo['travel_z']:g}, scan position {mo['scan_pose_mpos']} unchanged as numbers)")
    if not a.confirm:
        print("Nothing changed - add --confirm to change it.")
        return 0
    if studio_running():
        print("STOP: Henna Studio is running - close it first (it reads these values when it starts)")
        return 1
    rec = record_move(profiles, mid, a.moved_up, a.floor)
    out = os.path.join(ROOT, "outputs", "machine_settings", mid)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{rec['time']}_z_home_moved.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, indent=2)
    try:
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Z home switch moved", None,
               "machine_z_home_moved.py", "floor moves with the switch; old scans refused",
               f"moved up {rec['moved_up_mm']:g} mm (tip at Z 0: 80 mm, was 71); floor {rec['floor_before']:g} -> "
               f"{rec['floor']:g}", "changed", "", f"user 2026-10-24: changed position of limit switches; machine {mid}")
    except Exception as e:                              # noqa: BLE001 - the change is made; say the log failed
        print(f"(log row not written: {e})")
    print(f"Done: floor {rec['floor']:g}. Start Studio; jog Z slowly to {rec['floor']:g} at the E-stop and measure the tip "
          "(about 31 mm above the bed); set a new daily-check reference; scan hands again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
