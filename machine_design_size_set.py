"""Set the largest design size (the Design step's Size slider) - NO MOTION, no controller connection.

User 2026-10-24: "increase size of design to 100mm" - the rule fit_mm_allowed was [10, 40] mm (the design's longest
side). It is one of the processing rules copied into a machine's calibration when it is approved
(webui_server_v26 RULE_KEYS), so it is changed in both places:
  the machine's approved calibration file   "fit_mm_allowed" [min, MAX]   (a copy is kept in machines/history/ first)
  current_calibration.json                  "fit_mm_allowed"              (the rules the next approval copies; copy kept)
What it keeps: everything else - the measured calibration, the nozzle heights, travel Z, the floor. A bigger design
still has to pass the design check on the measured skin (every stroke on usable, reachable skin, the travel lines
clear) - on a hand most 100 mm designs will not fit the reachable part; they fit the prosthetic or paper.
Studio must be CLOSED (it reads the calibration when it starts).
  python machine_design_size_set.py --max-mm 100 --confirm            (the active machine)
Result: printed summary + outputs/machine_settings/<machine>/<time>_design_size.json + one log row.
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
MAX_LIMIT_MM = 150.0                 # the work area is 225 x 176 mm; the camera sees about 400 x 230 mm at the bed


def check_value(max_mm, current):
    v = float(max_mm)
    lo = float(current[0])
    if not lo < v <= MAX_LIMIT_MM:
        raise ValueError(f"the largest design size must be above the smallest ({lo:g} mm) and at most "
                         f"{MAX_LIMIT_MM:g} mm")
    return v


def _rewrite(path, hist, stamp, key_value):
    shutil.copyfile(path, os.path.join(hist, f"{os.path.basename(path)[:-5]}.before-design-size-{stamp}.json"))
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    old = list(doc.get("fit_mm_allowed", []))
    doc["fit_mm_allowed"] = key_value
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return old


def set_design_size(profiles, machine_id, max_mm, stamp=None, rules_path=None):
    stamp = stamp or dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    m = profiles.get(machine_id)
    if not m.get("calibration"):
        raise ValueError(f"{m['name']} has no approved calibration - nothing to change")
    cal_path = os.path.join(profiles.root, m["calibration"])
    with open(cal_path, encoding="utf-8") as f:
        cur = json.load(f)["fit_mm_allowed"]
    v = check_value(max_mm, cur)
    new = [float(cur[0]), v]
    hist = os.path.join(os.path.dirname(profiles.registry_path), "history")
    os.makedirs(hist, exist_ok=True)
    before = _rewrite(cal_path, hist, stamp, new)
    rules_path = rules_path or os.path.join(profiles.root, "current_calibration.json")
    rules_before = _rewrite(rules_path, hist, stamp, new) if os.path.isfile(rules_path) else None
    profiles.check_consistent(profiles.get(machine_id))
    return {"machine": machine_id, "time": stamp, "fit_mm_allowed_before": before, "fit_mm_allowed": new,
            "rules_before": rules_before, "calibration_file": m["calibration"]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--machine", help="machine id (default: the active machine)")
    ap.add_argument("--max-mm", type=float, required=True)
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
    if not m.get("calibration"):
        print(f"STOP: {m['name']} has no approved calibration")
        return 1
    with open(os.path.join(ROOT, m["calibration"]), encoding="utf-8") as f:
        cur = json.load(f)["fit_mm_allowed"]
    try:
        v = check_value(a.max_mm, cur)
    except ValueError as e:
        print(f"STOP: {e}")
        return 1
    print(f"{m['name']}: design size {cur[0]:g}-{cur[1]:g} mm -> {cur[0]:g}-{v:g} mm")
    if not a.confirm:
        print("Nothing changed - add --confirm to change it.")
        return 0
    if studio_running():
        print("STOP: Henna Studio is running - close it first (it reads the calibration when it starts)")
        return 1
    rec = set_design_size(profiles, mid, v)
    out = os.path.join(ROOT, "outputs", "machine_settings", mid)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, f"{rec['time']}_design_size.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    try:
        from henna_preview.web_log import append
        append(os.path.join(ROOT, "validation_log_S2026-10-05-A.csv"), 0, "Design size limit changed", None,
               "machine_design_size_set.py", "largest design size", f"{rec['fit_mm_allowed_before']} -> "
               f"{rec['fit_mm_allowed']} mm", "changed", "", f"user 2026-10-24: increase size of design to 100mm; "
               f"machine {mid}")
    except Exception as e:                              # noqa: BLE001 - the change is made; say the log failed
        print(f"(log row not written: {e})")
    print(f"Done: design size now {rec['fit_mm_allowed'][0]:g}-{rec['fit_mm_allowed'][1]:g} mm "
          f"(copies in machines/history/). Start Studio again.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
