"""Step 16: smooth nozzle path (exact planned X/Y, smoothed measured skin Z, floor on raw skin)
and a new DRY RUN program with the same user values as step 15.

No machine connection. The nozzle path is NOT validated for a hand (motion_ready=false).
"""
import argparse
import datetime as dt
import json
import os
import re

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from henna_preview import MOTION_READY
from henna_preview.depth_scale import correct_depth
from henna_preview.dry_run import independent_check as dry_check, lift_strokes, program
from henna_preview.nozzle_toolpath import NozzlePathError, check_params, gcode_lines, independent_check
from henna_preview.plan_design import plan_points
from henna_preview.region import polygon_mask, require_accepted
from henna_preview.smooth_path import build_smooth_strokes, corner_stats, measure_under_plan
from step12_nozzle_sim import ROOT, _j, hand_top
from step13_plan_sim import load_frame


def build(cfg, design, window_mm):
    p = {k: cfg[k] for k in ("standoff_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min",
                             "travel_z", "firmware_z_floor")}
    check_params(p)
    head = cfg["hand_capture"]["head"]
    region = _j(os.path.join(cfg["region_dir"], "region.json"))
    hr = _j(os.path.join(cfg["hand_run_dir"], "report.json"))
    if os.path.basename(hr["input"]["path"]) != cfg["hand_capture"]["recording"]:
        raise NozzlePathError("hand run is not the recording whose head pose is given")
    require_accepted(region, hr["input"]["sha256"], hr["input"]["frame_index"])
    xy, zc = _j(cfg["xy_calibration"]), _j(cfg["z_calibration"])
    if "nozzle" not in str(xy.get("tool", "")).lower() or "nozzle" not in str(zc.get("tool", "")).lower():
        raise NozzlePathError("X/Y and Z calibrations must both be NOZZLE calibrations")
    ds = _j(cfg["depth_scale"])
    g, d_ref = float(ds["g"]), float(ds["d_ref_mm"])
    depth_fn = lambda d: correct_depth(d, g, d_ref)  # noqa: E731
    k = float(zc["result"]["k_mm"])
    frame = load_frame(cfg["hand_run_dir"])
    h, w = frame["depth_raw_aligned"].shape
    mask = polygon_mask(region["polygon_px"], w, h)
    plan, closed = plan_points(design["shape"], design["diameter_mm"], design["centre_machine_xy"],
                               design["rotation_deg"], spacing_mm=0.5)
    measured = measure_under_plan(plan, frame, mask, head, xy["result"])
    strokes, stats = build_smooth_strokes(plan, closed, measured, head, k, depth_fn, p["standoff_mm"],
                                          p["floor_above_skin_mm"], window_mm=window_mm, jump_mm=design["jump_mm"])
    top = hand_top(cfg["hand_run_dir"], k, head["z"], depth_fn=depth_fn)
    if p["travel_z"] < top["hand_top_contact_z"] + p["standoff_mm"]:
        raise NozzlePathError("travel Z does not clear the hand top + standoff")
    lines, floors = gcode_lines(strokes, p)
    independent_check(lines, floors, p)
    return p, strokes, stats, top, {"g": g, "d_ref_mm": d_ref, "k_mm": k}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-params", required=True)
    ap.add_argument("--window-mm", type=float, default=3.0)
    a = ap.parse_args()
    dp = _j(a.dry_params)
    cfg = _j(dp["source_config"])
    p, strokes, stats, top, cal = build(cfg, dp["design"], a.window_mm)
    q = {k: dp[k] for k in ("lift_mm", "travel_z", "floor_z", "draw_feed_mm_min", "travel_feed_mm_min")}
    lifted = lift_strokes(strokes, q)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = dp["design"]
    header = ["DRY RUN - NO HAND - NO DISPENSING - EMPTY BED ONLY",
              f"made {stamp} by step16_smooth.py - smooth path - planned X Y - smoothed measured skin Z",
              f"20 mm circle at X{d['centre_machine_xy'][0]} Y{d['centre_machine_xy'][1]} - lifted {q['lift_mm']} mm",
              f"floor Z {q['floor_z']} - travel Z {q['travel_z']} - feeds {q['draw_feed_mm_min']:.0f} / {q['travel_feed_mm_min']:.0f}",
              "Home the machine first - stand at the emergency stop"]
    lines = program(lifted, q, header)
    dry_check(lines, q)
    out = os.path.join(ROOT, "outputs", "step16", stamp)
    os.makedirs(out, exist_ok=True)
    name = f"DRY_RUN_SMOOTH_NO_HAND_{stamp}.gcode"
    with open(os.path.join(out, name), "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    # compare with step 15 file
    old = open(os.path.join(ROOT, "validation/S2026-10-05-A/results/DRY_RUN_NO_HAND_20261005-133542.gcode")).read().splitlines()
    P = np.array([[float(v) for v in re.findall(r"[XYZ](-?\d+\.\d+)", l)] for l in old if l.startswith("G53 G1")])
    old_cs = corner_stats([np.column_stack([P[:, :2], P[:, 2], P[:, 2]])])
    new_cs = corner_stats(strokes)
    s = strokes[0]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.6))
    ax[0].plot(P[:, 0], P[:, 1], "-", color="#E24B4A", lw=1, alpha=0.7, label="step 15 (pixel-snapped)")
    for st in strokes:
        ax[0].plot(st[:, 0], st[:, 1], "-", color="#3B6D11", lw=2, label="step 16 (smooth)")
    ax[0].set_aspect("equal"); ax[0].grid(alpha=.3); ax[0].set_xlabel("X (mm)"); ax[0].set_ylabel("Y (mm)")
    hh, ll = ax[0].get_legend_handles_labels(); u = dict(zip(ll, hh)); ax[0].legend(u.values(), u.keys(), fontsize=8)
    ax[0].set_title("Top view")
    ax[1].plot(s[:, 2], color="#854F0B", lw=1, label="measured skin (raw)")
    ax[1].plot(s[:, 4], "--", color="#E24B4A", lw=1, label="floor = raw skin + 1")
    ax[1].plot(s[:, 3], color="#3B6D11", lw=2, label="nozzle Z (smoothed, never below floor)")
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=8); ax[1].set_xlabel("point along path"); ax[1].set_ylabel("machine Z (mm)")
    ax[1].set_title("Heights on the hand (before the dry-run lift)")
    fig.suptitle("Step 16 smooth path - NOT VALIDATED for a hand", color="#A32D2D", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(out, "smooth_preview.png"), dpi=110); plt.close(fig)
    allz = np.concatenate([l[:, 2] for l in lifted])
    rep = {"schema": "nozzle_smooth_dry_run/1", "created": stamp, "motion_ready_for_hand": MOTION_READY,
           "program_file": name, "dry_params": dp, "smoothing_window_mm": a.window_mm, "calibration": cal,
           "path_stats": stats, "hand_top": top,
           "corners": {"step15_pixel_snapped": old_cs, "step16_smooth": new_cs},
           "dry_run": {"lines": len(lines), "z_range": [round(float(allz.min()), 3), round(float(allz.max()), 3)]},
           "labels": {"x_y": "PLANNED (exact circle); measured skin pixel within 0.5 mm",
                      "z": "smoothed MEASURED skin + standoff; never below RAW measured skin + 1 mm",
                      "interpolated_skin_points": 0}}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k: rep[k] for k in ("program_file", "path_stats", "corners", "dry_run")}, indent=2))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
