"""Step 23: DESIGN HOVER TEST with LOW HOPS between strokes - PROSTHETIC hand only - no dispensing.

Step 22 (unchanged, validated) with one difference (user decision 2026-10-10, "treat Z like a laser"): between
strokes the nozzle does not go back to travel Z. It rises straight up to a hop height = highest MEASURED skin
under the straight crossing (+-1.5 mm band) + 5 mm (never below either end's nozzle + 0.5 mm), crosses at that
height and drops onto the next stroke. A crossing with any unmeasured or out-of-region point uses travel Z.
Start and end of the program stay at travel Z. The hop moves are re-read from the program text and the skin
under them is sampled again (henna_preview.hop_path.verify_hops); the text is re-read by program_reread_v2.
--- step 22 description follows ---

Same plan as step 21 (line-art SVG fitted on the accepted region, skin from the 15-frame median
under every point, nozzle = max(smoothed skin + standoff, raw skin + floor margin), all or
nothing). The program is written ONLY with --write-gcode, and only when the settings name a
prosthetic hand as the subject. Program text: G21, G90, G53 G0 Z (travel), G53 G0 X Y (at travel
Z), G53 G1 X Y Z F - every stroke starts with a slow descent from travel Z and ends with a lift.
Re-read independently with per-point floors. Three gap-check points are listed in the report.
Optional "surface_offset_file": a touch-measured offset on the same recording raises the surface
(henna_preview.surface_offset; prosthetic only, can only raise the nozzle).
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
from henna_preview.hop_path import HOP_CLEARANCE_MM, plan_hops, skin_max, verify_hops
from henna_preview.program_reread_v2 import reread_v2
from henna_preview.nozzle_toolpath import NozzlePathError, check_params, gcode_lines, independent_check as floor_check
from henna_preview.recording_multi import read_frames_median
from henna_preview.region import polygon_mask, require_accepted
from henna_preview.smooth_path import corner_stats
from henna_preview.surface_offset import load_offset, shifted_depth_fn
from step12_nozzle_sim import ROOT, _j
from step19_hand_path_avg import hand_top_from_raw
from step21_design_preview import RAPID_MM_MIN, plan_design


def program_with_hops(lifted, hops, p, header):
    """Like dry_run.program, but each gap between strokes rises only to its hop height."""
    fd = int(round(p["draw_feed_mm_min"]))
    L = [f"({h})" for h in header] + ["G21", "G90", f"G53 G0 Z{p['travel_z']:.3f}"]
    for i, s in enumerate(lifted):
        if i:
            L.append(f"G53 G0 Z{hops[i - 1]['z']:.3f}")
        L.append(f"G53 G0 X{s[0, 0]:.3f} Y{s[0, 1]:.3f}")
        for x, y, z in s:
            L.append(f"G53 G1 X{x:.3f} Y{y:.3f} Z{z:.3f} F{fd}")
    L.append(f"G53 G0 Z{p['travel_z']:.3f}")
    return L


def run_time_hops(paths, hops, p):
    """Rough time: drops and drawing at the draw feed, rises and crossings at the rapid rate."""
    t, prev_z, pos = 0.0, p["travel_z"], None
    for i, s in enumerate(paths):
        if i:
            hz = hops[i - 1]["z"]
            t += abs(hz - prev_z) / RAPID_MM_MIN * 60 + np.hypot(*(s[0, :2] - pos)) / RAPID_MM_MIN * 60
            prev_z = hz
        t += abs(prev_z - s[0, 3]) / p["draw_feed_mm_min"] * 60
        t += np.linalg.norm(np.diff(s[:, [0, 1, 3]], axis=0), axis=1).sum() / p["draw_feed_mm_min"] * 60
        prev_z, pos = s[-1, 3], s[-1, :2]
    t += (p["travel_z"] - prev_z) / RAPID_MM_MIN * 60
    return round(float(t), 1)


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
    offset = None
    if cfg.get("surface_offset_file"):  # optional: measured by nozzle touches on this recording (prosthetic only)
        offset = load_offset(os.path.join(ROOT, cfg["surface_offset_file"]), cfg["hand_capture"]["recording"],
                             p["standoff_mm"])
        depth_fn = shifted_depth_fn(depth_fn, offset["applied_mm"])

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
    clearance = float(cfg.get("hop_clearance_mm", HOP_CLEARANCE_MM))
    if clearance < HOP_CLEARANCE_MM:
        raise NozzlePathError(f"hop clearance must be at least {HOP_CLEARANCE_MM:g} mm")
    sample = lambda pts: skin_max(pts, fr, mask, head, xy["result"], k, depth_fn)  # noqa: E731
    hops = plan_hops(paths, sample, p["travel_z"], clearance)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = cfg["design"]
    tt = run_time_hops(paths, hops, p)
    low = [h for h in hops if h["low"]]
    header = ["DESIGN HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING",
              f"made {stamp} by step23_design_hop_program.py - {nf}-frame median depth - {os.path.basename(d['svg'])} "
              f"fit {d['fit_mm']:g} mm at X{centre[0]} Y{centre[1]}",
              f"nozzle {p['standoff_mm']:g} mm above measured skin - floor skin + {p['floor_above_skin_mm']:g} mm - "
              f"travel Z {p['travel_z']:g} - {len(paths)} strokes - about {tt / 60:.1f} min",
              (f"surface raised by touch-measured offset {offset['applied_mm']:.3f} mm" if offset
               else "no surface offset"),
              f"between strokes: {len(low)} of {len(hops)} low hops {clearance:g} mm above the highest skin under the "
              f"crossing - others at travel Z",
              "Run from Studio - home first - hand not moved since the recording - stand at the emergency stop"]
    lines = program_with_hops([np.column_stack([s[:, 0], s[:, 1], s[:, 3]]) for s in paths], hops, p, header)
    text = "\n".join(lines) + "\n"
    reread_v2(text, p["travel_z"], q["floor_z"], p["draw_feed_mm_min"])
    hop_check = verify_hops(text, sample, p["travel_z"], clearance)
    if len(hop_check) != len(low):
        raise NozzlePathError("program text does not contain exactly the planned low hops")
    out = os.path.join(ROOT, "outputs", "step23", stamp)
    os.makedirs(out, exist_ok=True)
    name = None
    if a.write_gcode:
        name = f"DESIGN_HOVER_TEST_PROSTHETIC_{stamp}.gcode"
        with open(os.path.join(out, name), "w", encoding="ascii", newline="\n") as f:
            f.write(text)
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
    x0 = 0
    for i, s in enumerate(paths[:-1]):
        x0 += len(s)
        ax[1].plot([x0 - 1, x0 + 2], [hops[i]["z"]] * 2, color="#185FA5", lw=2, label="hop height" if i == 0 else None)
        x0 += 3
    ax[1].legend(fontsize=8, ncol=2)
    ax[1].set_xlabel("points, stroke after stroke (blue = hop between strokes)"); ax[1].set_title("Heights (machine Z)")
    fig.suptitle(f"DESIGN HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING - {len(paths)} strokes, "
                 f"about {tt / 60:.1f} min" + ("" if a.write_gcode else " - preview only, no program written"),
                 color="#A32D2D", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(out, "design_hover_preview.png"), dpi=110); plt.close(fig)

    allz = np.concatenate([s[:, 3] for s in paths])
    alls = np.concatenate([s[:, 2] for s in paths])
    rep = {"schema": "design_hop_program/1", "created": stamp, "motion_ready_for_person": MOTION_READY,
           "program_file": name, "config": cfg,
           "design": {"svg": d["svg"], "fit_mm": d["fit_mm"], "mm_per_svg_unit": round(scale, 5),
                      "centre_machine_xy": centre, "rotation_deg": d.get("rotation_deg", 0.0), "strokes": len(paths),
                      "line_length_mm": round(sum(s["length_mm"] for s in placed), 1)},
           "surface_offset": offset or {"applied_mm": 0.0, "note": "none"},
           "per_stroke": per, "hand_top": top, "gap_check_points": gaps,
           "skin_z_range": [round(float(alls.min()), 3), round(float(alls.max()), 3)],
           "nozzle_z_range": [round(float(allz.min()), 3), round(float(allz.max()), 3)],
           "corners": corner_stats(paths), "estimated_run_time_s": tt,
           "global_floor_in_program": round(q["floor_z"], 3), "lines": len(lines),
           "hops": {"clearance_mm": clearance, "low": len(low), "total": len(hops),
                    "z_range": [min(h["z"] for h in low), max(h["z"] for h in low)] if low else None,
                    "per_gap": hops, "independent_check": hop_check},
           "depth": {k2: v for k2, v in fr["multi"].items() if k2 != "frame_numbers_depth_colour"},
           "checks": "per-point floor (skin + margin) on re-read text; allowed commands only; low hops re-read from the "
                     "text and re-sampled on measured skin (+clearance); travel Z clears hand top + standoff; design "
                     "all-or-nothing on usable skin inside the region"}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k2: rep[k2] for k2 in ("program_file", "surface_offset", "design", "hand_top", "skin_z_range",
                                             "nozzle_z_range",
                                             "corners", "estimated_run_time_s", "global_floor_in_program", "lines",
                                             "gap_check_points")}, indent=2))
    print("Hops:", json.dumps(rep["hops"]["z_range"]), f"{len(low)} low of {len(hops)}")
    print("Wrote:", out)


if __name__ == "__main__":
    main()
