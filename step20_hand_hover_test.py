"""Step 20: HOVER TEST over a PROSTHETIC hand - no person - no dispensing.

Same path as step 19 (plan-view design on the accepted region, skin from the 15-frame median,
exact planned X/Y, nozzle = max(smoothed skin + standoff, raw skin + floor margin), path breaks
where there is no usable skin). The program is written ONLY with --write-gcode, and only when the
settings name a prosthetic hand as the subject. Program text: G21, G90, G53 G0 Z (travel),
G53 G0 X Y (at travel Z), G53 G1 X Y Z F - re-read independently with per-point floors.
Not validated for a person: motion_ready = false.
"""
import argparse
import datetime as dt
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from henna_preview import MOTION_READY
from henna_preview.depth_scale import correct_depth
from henna_preview.dry_run import independent_check as program_check, program
from henna_preview.nozzle_toolpath import NozzlePathError, check_params, gcode_lines, independent_check as floor_check
from henna_preview.recording_multi import read_frames_median
from henna_preview.region import polygon_mask, require_accepted
from henna_preview.smooth_path import corner_stats
from step12_nozzle_sim import ROOT, _j
from step19_hand_path_avg import hand_top_from_raw, path_for


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--write-gcode", action="store_true", help="write the runnable program (approval needed)")
    a = ap.parse_args()
    cfg = _j(a.config)
    if "prosthetic" not in str(cfg.get("subject", "")).lower():
        raise NozzlePathError("hover tests are only for a prosthetic hand: set subject to the prosthetic hand")
    p = {k: cfg[k] for k in ("standoff_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min",
                             "travel_z", "firmware_z_floor")}
    check_params(p)
    head = cfg["hand_capture"]["head"]
    region = _j(os.path.join(cfg["region_dir"], "region.json"))
    hand_rep = _j(os.path.join(cfg["hand_run_dir"], "report.json"))
    if os.path.basename(hand_rep["input"]["path"]) != cfg["hand_capture"]["recording"]:
        raise NozzlePathError("hand run is not the recording whose head pose is given")
    require_accepted(region, hand_rep["input"]["sha256"], hand_rep["input"]["frame_index"])
    xy, zc, ds = _j(cfg["xy_calibration"]), _j(cfg["z_calibration"]), _j(cfg["depth_scale"])
    if "nozzle" not in str(xy.get("tool", "")).lower() or "nozzle" not in str(zc.get("tool", "")).lower():
        raise NozzlePathError("X/Y and Z calibrations must both be NOZZLE calibrations")
    k, g, d_ref = float(zc["result"]["k_mm"]), float(ds["g"]), float(ds["d_ref_mm"])
    depth_fn = lambda d, g=g, d_ref=d_ref: correct_depth(d, g, d_ref)  # noqa: E731

    fi, nf = int(hand_rep["input"]["frame_index"]), int(cfg["frames"])
    fr = read_frames_median(hand_rep["input"]["path"], fi - nf // 2, nf, cfg["min_valid_frac"], cfg["max_mad_mm"])
    with np.load(os.path.join(ROOT, cfg["hand_run_dir"], "frame_bundle.npz")) as b:
        bundle_depth, landmarks = b["depth_raw_aligned"], b["landmarks_px"]
    if not np.array_equal(bundle_depth, fr["depth_raw_single_middle"]):
        raise NozzlePathError("middle frame of the median window is not the hand-detection frame")
    h, w = fr["depth_raw_aligned"].shape
    mask = polygon_mask(region["polygon_px"], w, h)
    centre = [float(v) for v in cfg["design"]["centre_machine_xy"]]
    plan, measured, strokes, stats = path_for(fr, cfg, mask, head, xy["result"], k, depth_fn, centre, p)
    top = hand_top_from_raw(fr["depth_raw_aligned"], fr["depth_scale_m_per_unit"], landmarks, k, head["z"], depth_fn)
    if p["travel_z"] < top["hand_top_contact_z"] + p["standoff_mm"]:
        raise NozzlePathError("travel Z does not clear the hand top + standoff")

    sim_lines, floors = gcode_lines(strokes, p)       # per-point floors, re-read
    floor_check(sim_lines, floors, p)
    q = {"lift_mm": 1.0, "travel_z": p["travel_z"], "floor_z": float(min(floors)),
         "draw_feed_mm_min": p["draw_feed_mm_min"], "travel_feed_mm_min": p["travel_feed_mm_min"]}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = cfg["design"]
    header = ["HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING",
              f"made {stamp} by step20_hand_hover_test.py - {nf}-frame median depth - {d['diameter_mm']:g} mm {d['shape']} at X{centre[0]} Y{centre[1]}",
              f"nozzle {p['standoff_mm']:g} mm above measured skin - floor skin + {p['floor_above_skin_mm']:g} mm - travel Z {p['travel_z']:g} - {len(strokes)} strokes",
              "Run with UGS - home first - hand not moved since the recording - stand at the emergency stop"]
    lines = program([np.column_stack([s[:, 0], s[:, 1], s[:, 3]]) for s in strokes], q, header)
    program_check(lines, q)
    out = os.path.join(ROOT, "outputs", "step20", stamp)
    os.makedirs(out, exist_ok=True)
    name = None
    if a.write_gcode:
        name = f"HOVER_TEST_PROSTHETIC_HAND_{stamp}.gcode"
        with open(os.path.join(out, name), "w", encoding="ascii", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    fig, ax = plt.subplots(1, 2, figsize=(13, 5))
    ok = np.array([m[0] == "ok" for m in measured])
    ax[0].plot(plan[:, 0], plan[:, 1], "-", color="#BBBBBB", lw=1, label="planned circle")
    if (~ok).any():
        ax[0].plot(plan[~ok, 0], plan[~ok, 1], "x", color="#E24B4A", ms=5, label="no usable skin (nozzle lifts)")
    for i, s in enumerate(strokes):
        ax[0].plot(s[:, 0], s[:, 1], "-", color="#3B6D11", lw=2, label="nozzle path" if i == 0 else None)
        ax[0].annotate(str(i + 1), s[0, :2], fontsize=9, color="#3B6D11")
    ax[0].set_aspect("equal"); ax[0].grid(alpha=.3); ax[0].legend(fontsize=8); ax[0].set_title("Top view (machine mm), stroke order")
    for i, s in enumerate(strokes):
        ax[1].plot(s[:, 2], color="#854F0B", lw=1, label="measured skin" if i == 0 else None)
        ax[1].plot(s[:, 4], "--", color="#E24B4A", lw=1, label="floor = skin + 1" if i == 0 else None)
        ax[1].plot(s[:, 3], color="#3B6D11", lw=2, label=f"nozzle = smoothed skin + {p['standoff_mm']:g}" if i == 0 else None)
    ax[1].axhline(top["hand_top_contact_z"], color="#888888", ls=":", lw=1, label="hand top")
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=8); ax[1].set_xlabel("point along each stroke"); ax[1].set_ylabel("machine Z (mm)")
    ax[1].set_title("Heights (machine Z)")
    fig.suptitle("HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING", color="#A32D2D", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(out, "hover_test_preview.png"), dpi=110); plt.close(fig)

    allz = np.concatenate([s[:, 3] for s in strokes])
    rep = {"schema": "hand_hover_test/1", "created": stamp, "motion_ready_for_person": MOTION_READY,
           "program_file": name, "config": cfg, "depth": {k2: v for k2, v in fr["multi"].items()
                                                          if k2 != "frame_numbers_depth_colour"},
           "hand_top": top, "path_stats": stats, "corners": corner_stats(strokes),
           "nozzle_z_range": [round(float(allz.min()), 3), round(float(allz.max()), 3)],
           "skin_z_range": [round(float(np.concatenate([s[:, 2] for s in strokes]).min()), 3),
                            round(float(np.concatenate([s[:, 2] for s in strokes]).max()), 3)],
           "global_floor_in_program": round(q["floor_z"], 3), "lines": len(lines),
           "checks": "per-point floor (skin + margin) on re-read text; allowed commands only; rapids only at travel Z; "
                     "travel Z clears hand top + standoff"}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k2: rep[k2] for k2 in ("program_file", "hand_top", "path_stats", "corners", "nozzle_z_range",
                                             "skin_z_range", "global_floor_in_program", "lines")}, indent=2))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
