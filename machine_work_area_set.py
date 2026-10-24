"""Set a machine's measured X/Y work area - NO MOTION, no controller connection.

User 2026-10-24: "check y travel" -> "y can travel 42mm extra": the ball-screw machine's work area 225 x 176 was
PROVISIONAL (copied from the belt-drive machine's Jog box on 2026-10-13, inside the controller's own 400 x 250). At
Y 176 the gantry could still travel 42 mm (ruler): the end is about Y 218 -> Y 213 with a 5 mm margin.
What it changes: machines/registry.json work_area_mm (+ work_area_status: measured, how, when); the previous registry
is kept in machines/history/. Studio applies it at start to the Jog, the program box and both re-reads; the step
scripts read it themselves (machine_motion.apply_active_work_area). The calibration is unchanged.
Rules: each side more than 50 mm and at most the controller's max travel (--controller-max, default 400 x 250).
After it: one slow jog to the new edge at the E-stop (Admin -> Jog), nothing may touch. Studio must be CLOSED.
  python machine_work_area_set.py --x 225 --y 213 --how "Y: +42 mm past 176 measured with a ruler, 5 mm margin" --confirm
Result: printed summary + outputs/machine_settings/<machine>/<time>_work_area.json + one log row.
"""
import argparse
import datetime as dt
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def check_value(x, y, controller_max=(400.0, 250.0)):
    x, y = float(x), float(y)
    for name, v, hi in (("X", x, float(controller_max[0])), ("Y", y, float(controller_max[1]))):
        if not 50.0 < v <= hi:
            raise ValueError(f"the {name} side must be more than 50 mm and at most the controller's {hi:g} mm")
    return x, y


def set_work_area(profiles, machine_id, x, y, how, stamp=None, controller_max=(400.0, 250.0)):
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    x, y = check_value(x, y, controller_max)
    m = profiles.get(machine_id)
    old = list(m.get("work_area_mm") or [])

    def change(r):
        r["work_area_mm"] = [x, y]
        r["work_area_status"] = f"MEASURED {stamp} (machine_work_area_set.py): {how}; was {old}"
    profiles._edit(machine_id, change)
    profiles.check_consistent(profiles.get(machine_id))
    return {"machine": machine_id, "time": stamp, "work_area_before": old, "work_area": [x, y], "how": how}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine", help="machine id (default: the active machine)")
    ap.add_argument("--x", type=float, required=True)
    ap.add_argument("--y", type=float, required=True)
    ap.add_argument("--how", required=True, help="how it was measured (kept in the registry)")
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
    try:
        x, y = check_value(a.x, a.y)
    except ValueError as e:
        print(f"STOP: {e}")
        return 1
    print(f"{m['name']}: work area {m.get('work_area_mm')} -> [{x:g}, {y:g}] mm ({a.how})")
    if not a.confirm:
        print("Nothing changed - add --confirm to change it.")
        return 0
    if studio_running():
        print("STOP: Henna Studio is running - close it first (it reads the work area when it starts)")
        return 1
    rec = set_work_area(profiles, mid, x, y, a.how)
    out = os.path.join(ROOT, "outputs", "machine_settings", mid)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{rec['time']}_work_area.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    try:
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Work area measured", None,
               "machine_work_area_set.py", "X/Y box inside the controller's travel",
               f"{rec['work_area_before']} -> {rec['work_area']} mm; {a.how}", "changed", "",
               f"user 2026-10-24: y can travel 42mm extra; machine {mid}")
    except Exception as e:                              # noqa: BLE001 - the change is made; say the log failed
        print(f"(log row not written: {e})")
    print(f"Done: work area {x:g} x {y:g} mm. Start Studio; jog slowly to the new edge at the E-stop once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
