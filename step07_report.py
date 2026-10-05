"""Step 7: build the final preview report for one saved design.

Example:
    .venv\\Scripts\\python step07_report.py --design-dir outputs\\webui\\<time>-design
Writes final_report.json and final_overlay.png into --out. motion_ready=false.
"""
import argparse
import datetime as dt
import json
import os
import shutil

import webui_server as base
from henna_preview.final_report import build_final_report


def region_dir_for(design_dir):
    rid = json.load(open(os.path.join(design_dir, "design_report.json"), encoding="utf-8"))["inputs"].get("region")
    if not rid or ":" not in rid:
        raise SystemExit("design report does not name its region; pass --region-dir")
    key, name = rid.split(":", 1)
    roots = {"web": base.OUT_ROOT, "cli": os.path.join(base.ROOT, "outputs", "step05")}
    return os.path.join(roots[key], name)


def write(design_dir, region_dir, out, check_recording=True):
    rep = build_final_report(design_dir, region_dir, check_recording)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "final_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    shutil.copyfile(os.path.join(design_dir, "design_overlay.png"), os.path.join(out, "final_overlay.png"))
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design-dir", required=True)
    ap.add_argument("--region-dir")
    ap.add_argument("--out")
    a = ap.parse_args()
    out = a.out or os.path.join("outputs", "step07", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    rep = write(a.design_dir, a.region_dir or region_dir_for(a.design_dir), out)
    print(json.dumps({k: rep[k] for k in ("motion_ready", "validity")}, indent=2))
    print("Wrote:", os.path.abspath(out))


if __name__ == "__main__":
    main()
