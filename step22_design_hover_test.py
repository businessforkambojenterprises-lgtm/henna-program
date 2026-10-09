"""Step 22: DESIGN HOVER TEST over a PROSTHETIC hand - no person - no dispensing.

Same plan as step 21 (line-art SVG fitted on the accepted region, skin from the 15-frame median
under every point, nozzle = max(smoothed skin + standoff, raw skin + floor margin), all or
nothing). The program is written ONLY with --write-gcode, and only when the settings name a
prosthetic hand as the subject. Program text: G21, G90, G53 G0 Z (travel), G53 G0 X Y (at travel
Z), G53 G1 X Y Z F - every stroke starts with a slow descent from travel Z and ends with a lift.
Re-read independently with per-point floors. Three gap-check points are listed in the report.
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
from step19_hand_path_avg import hand_top_from_raw
from step21_design_preview import plan_design, run_time_s


def gap_points(paths):
    """Three program points spread over the design: highest skin, lowest skin, and the point farthest from both."""
    pts = np.vstack([s[:, :4] for s in paths])
    hi, lo = int(np.argmax(pts[:, 2])), int(np.argmin(pts[:, 2]))
    d = np.minimum(np.hypot(*(pts[:, :2] - pts[hi, :2]).T), np.hypot(*(pts[:, :2] - pts[lo, :2]).T))
    mid = int(np.argmax(d))
    return [{"name": n, "x": round(float(pts[i, 0]), 3), "y": round(float(pts[i, 1]), 3),
             "program_z": round(float(pts[i, 3]), 3), "camera_skin_z": round(float(pts[i, 2]), 3)}
            for n, i in (("H1 highest skin", hi), ("H2 lowest skin", lo), ("H3 spread", mid))]


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
    if fi - nf // 2 < 0:
        raise NozzlePathError(f"hand frame {fi} is too early for a {nf}-frame window")
    fr = read_frames_median(hand_rep["input"]["path"], fi - nf // 2, nf, cfg["min_valid_frac"], cfg["max_mad_mm"])
    with np.load(os.path.join(ROOT, cfg["hand_run_dir"], "frame_bundle.npz")) as b:
        bundle_depth, landmarks = b["depth_raw_aligned"], b["landmarks_px"]
    if not np.array_equal(bundle_depth, fr["depth_raw_single_middle"]):
        raise NozzlePathError("middle frame of the median window is not the hand-detection frame")
    h, w = fr["depth_raw_aligned"].shape
    mask = polygon_mask(region["polygon_px"], w, h)
    cfg["_region"] = region
    placed, paths, per, problems, centre, scale = plan_design(cfg, fr, mask, head, xy["result"], k, depth_fn, p)
    cfg.pop("_region")
    if problems:
        raise NozzlePathError("design refused - nothing written:\n  " + "\n  ".join(problems))
    top = hand_top_from_raw(fr["depth_raw_aligned"], fr["depth_scale_m_per_unit"], landmarks, k, head["z"], depth_fn)
    if p["travel_z"] < top["hand_top_contact_z"] + p["standoff_mm"]:
        raise NozzlePathError("travel Z does not clear the hand top + standoff")

    sim_lines, floors = gcode_lines(paths, p)          # per-point floors, re-read
    floor_check(sim_lines, floors, p)
    q = {"lift_mm": 1.0, "travel_z": p["travel_z"], "floor_z": float(min(floors)),
         "draw_feed_mm_min": p["draw_feed_mm_min"], "travel_feed_mm_min": p["travel_feed_mm_min"]}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = cfg["design"]
    tt = run_time_s(paths, p)
    header = ["DESIGN HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING",
              f"made {stamp} by step22_design_hover_test.py - {nf}-frame median depth - {os.path.basename(d['svg'])} "
              f"fit {d['fit_mm']:g} mm at X{centre[0]} Y{centre[1]}",
              f"nozzle {p['standoff_mm']:g} mm above measured skin - floor skin + {p['floor_above_skin_mm']:g} mm - "
              f"travel Z {p['travel_z']:g} - {len(paths)} strokes - about {tt / 60:.1f} min",
              "Run with UGS - home first - hand not moved since the recording - stand at the emergency stop"]
    lines = program([np.column_stack([s[:, 0], s[:, 1], s[:, 3]]) for s in paths], q, header)
    program_check(lines, q)
    out = os.path.join(ROOT, "outputs", "step22", stamp)
    os.makedirs(out, exist_ok=True)
    name = None
    if a.write_gcode:
        name = f"DESIGN_HOVER_TEST_PROSTHETIC_{stamp}.gcode"
        with open(os.path.join(out, name), "w", encoding="ascii", newline="\n") as f:
            f.write("\n".join(lines) + "\n")
    gaps = gap_points(paths)

    fig, ax = plt.subplots(1, 2, figsize=(14, 5.6), gridspec_kw={"width_ratios": [1, 1.6]})
    for i, s in enumerate(paths, start=1):
        ax[0].plot(s[:, 0], s[:, 1], "-", color="#3B6D11", lw=1.4)
        ax[0].annotate(str(i), s[0, :2], fontsize=7, color="#555555")
    for gp, c in zip(gaps, ("#E24B4A", "#185FA5", "#BA7517")):
        ax[0].plot(gp["x"], gp["y"], "o", ms=8, mfc="none", mec=c, mew=2, label=gp["name"])
    ax[0].set_aspect("equal"); ax[0].grid(alpha=.3); ax[0].legend(fontsize=8, loc="lower right")
    ax[0].set_title("Top view (machine mm) - stroke order; circles = gap-check points")
    x0 = 0
    for i, s in enumerate(paths):
        xs = np.arange(x0, x0 + len(s))
        ax[1].plot(xs, s[:, 2], color="#854F0B", lw=1, label="measured skin" if i == 0 else None)
        ax[1].plot(xs, s[:, 4], "--", color="#E24B4A", lw=1, label="floor = skin + 1" if i == 0 else None)
        ax[1].plot(xs, s[:, 3], color="#3B6D11", lw=2, label=f"nozzle = smoothed skin + {p['standoff_mm']:g}" if i == 0 else None)
        x0 += len(s) + 3
    ax[1].axhline(top["hand_top_contact_z"], color="#888888", ls=":", lw=1, label="hand top")
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=8, ncol=2); ax[1].set_ylabel("machine Z (mm)")
    ax[1].set_xlabel("points, stroke after stroke (gaps = lift to travel Z)"); ax[1].set_title("Heights (machine Z)")
    fig.suptitle(f"DESIGN HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING - {len(paths)} strokes, "
                 f"about {tt / 60:.1f} min" + ("" if a.write_gcode else " - preview only, no program written"),
                 color="#A32D2D", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(out, "design_hover_preview.png"), dpi=110); plt.close(fig)

    allz = np.concatenate([s[:, 3] for s in paths])
    alls = np.concatenate([s[:, 2] for s in paths])
    rep = {"schema": "design_hover_test/1", "created": stamp, "motion_ready_for_person": MOTION_READY,
           "program_file": name, "config": cfg,
           "design": {"svg": d["svg"], "fit_mm": d["fit_mm"], "mm_per_svg_unit": round(scale, 5),
                      "centre_machine_xy": centre, "rotation_deg": d.get("rotation_deg", 0.0), "strokes": len(paths),
                      "line_length_mm": round(sum(s["length_mm"] for s in placed), 1)},
           "per_stroke": per, "hand_top": top, "gap_check_points": gaps,
           "skin_z_range": [round(float(alls.min()), 3), round(float(alls.max()), 3)],
           "nozzle_z_range": [round(float(allz.min()), 3), round(float(allz.max()), 3)],
           "corners": corner_stats(paths), "estimated_run_time_s": tt,
           "global_floor_in_program": round(q["floor_z"], 3), "lines": len(lines),
           "depth": {k2: v for k2, v in fr["multi"].items() if k2 != "frame_numbers_depth_colour"},
           "checks": "per-point floor (skin + margin) on re-read text; allowed commands only; rapids only at travel Z; "
                     "travel Z clears hand top + standoff; design all-or-nothing on usable skin inside the region"}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k2: rep[k2] for k2 in ("program_file", "design", "hand_top", "skin_z_range", "nozzle_z_range",
                                             "corners", "estimated_run_time_s", "global_floor_in_program", "lines",
                                             "gap_check_points")}, indent=2))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
