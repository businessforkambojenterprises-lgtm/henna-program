"""Step 21: HENNA DESIGN PREVIEW from a line-art SVG - preview only, NO G-code written.

The design (henna_preview.svg_design) is scaled to fit --fit-mm, placed on the user's ACCEPTED
hand region (centre = region middle unless given) and every stroke is planned like the step 19
circle: exact planned X/Y every 0.5 mm, skin measured under each point from the 15-frame median,
nozzle = max(smoothed skin + standoff, raw skin + floor margin), travel Z between strokes.

A design is all or nothing: if any point of any stroke has no usable skin, falls outside the
region, or a stroke would be split by a depth jump, the design is REFUSED (nothing is cut away).
Filled shapes without a stroke are refused too (they would be traced as double outlines).
The G-code text is built in memory only, to run the same independent checks. motion_ready = false.
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
from henna_preview.nozzle_toolpath import NozzlePathError, check_params, gcode_lines, independent_check
from henna_preview.recording_multi import read_frames_median
from henna_preview.region import polygon_mask, require_accepted
from henna_preview.smooth_path import build_smooth_strokes, corner_stats, measure_under_plan
from henna_preview.svg_design import place, read_svg
from step12_nozzle_sim import ROOT, _j
from step19_hand_path_avg import hand_top_from_raw, region_centre_machine

RAPID_MM_MIN = 2500.0  # G0 speed seen in the controller status lines (FS 2453-2750); for the time estimate only


def plan_design(cfg, fr, mask, head, xy_cal, k, depth_fn, p):
    """-> placed strokes, path strokes (one per design stroke), per-stroke stats, centre, scale."""
    d = cfg["design"]
    design = read_svg(os.path.join(ROOT, d["svg"]))
    filled = [s["source"] for s in design["strokes"] if s["filled_only"]]
    if filled:
        raise NozzlePathError(f"design has filled shapes without a line ({', '.join(filled[:5])}); "
                              "henna needs single line-art strokes")
    centre = (region_centre_machine(cfg["_region"]["polygon_px"], fr, head, xy_cal)[0]
              if d["centre_machine_xy"] == "region_centroid" else [float(v) for v in d["centre_machine_xy"]])
    placed, scale = place(design, d["fit_mm"], centre, d.get("rotation_deg", 0.0), spacing_mm=0.5)
    paths, per, problems = [], [], []
    for n, s in enumerate(placed, start=1):
        measured = measure_under_plan(s["plan"], fr, mask, head, xy_cal)
        bad = [m[0] for m in measured if m[0] != "ok"]
        if bad:
            problems.append(f"stroke {n} ({s['source']}): {len(bad)}/{len(measured)} points "
                            f"{'outside the region' if 'outside_region' in bad else 'without usable skin'}")
            per.append({"stroke": n, "source": s["source"], "ok_points": len(measured) - len(bad),
                        "points": len(measured)})
            continue
        st, stats = build_smooth_strokes(s["plan"], s["closed"], measured, head, k, depth_fn, p["standoff_mm"],
                                         p["floor_above_skin_mm"], jump_mm=d["jump_mm"])
        if len(st) != 1:
            problems.append(f"stroke {n} ({s['source']}): split by a depth jump > {d['jump_mm']} mm")
        paths.append(st[0])
        per.append({"stroke": n, "source": s["source"], "points": len(measured), "ok_points": len(measured),
                    "length_mm": s["length_mm"], "closed": s["closed"],
                    "skin_z": [round(float(st[0][:, 2].min()), 3), round(float(st[0][:, 2].max()), 3)],
                    "nozzle_z": [round(float(st[0][:, 3].min()), 3), round(float(st[0][:, 3].max()), 3)],
                    "max_raise_above_standoff_mm": stats["max_raise_above_standoff_due_to_floor_mm"]})
    # skin lower than the nozzle can reach (2026-10-24: fingers in the jig's cut-out, skin at Z -46 with the nozzle
    # 6 mm above it -> Z -40.3, under the Z travel) - a clear refusal, not the re-read's "Z or feed out of range"
    low = [(n, st) for n, st in enumerate(paths, start=1) if st[:, 3].min() < p["firmware_z_floor"]]
    if low:
        pts = np.vstack([st[st[:, 3] < p["firmware_z_floor"]] for _, st in low])
        lo = pts[np.argmin(pts[:, 3])]
        problems.append(f"the skin under {len(low)} stroke(s) is too low for the nozzle: it would have to go to Z "
                        f"{lo[3]:.1f} (skin Z {lo[2]:.1f}, near X{lo[0]:.0f} Y{lo[1]:.0f}) and the lowest it reaches is "
                        f"Z {p['firmware_z_floor']:g} - place the design on a higher part of the hand (the middle of "
                        "the palm), or raise the hand")
    return placed, paths, per, problems, centre, scale


def run_time_s(paths, p):
    """Rough time: rapid travel at travel Z, F-draw descent from travel Z, drawing, rapid lift."""
    t, pos = 0.0, None
    for s in paths:
        if pos is not None:
            t += np.hypot(*(s[0, :2] - pos)) / RAPID_MM_MIN * 60
        t += (p["travel_z"] - s[0, 3]) / p["draw_feed_mm_min"] * 60          # descent
        t += np.linalg.norm(np.diff(s[:, [0, 1, 3]], axis=0), axis=1).sum() / p["draw_feed_mm_min"] * 60
        t += (p["travel_z"] - s[-1, 3]) / RAPID_MM_MIN * 60                  # lift
        pos = s[-1, :2]
    return round(float(t), 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    a = ap.parse_args()
    cfg = _j(a.config)
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

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(ROOT, "outputs", "step21", stamp)
    os.makedirs(out, exist_ok=True)
    top = hand_top_from_raw(fr["depth_raw_aligned"], fr["depth_scale_m_per_unit"], landmarks, k, head["z"], depth_fn)
    verdict, checks = "REFUSED", None
    if not problems:
        if p["travel_z"] < top["hand_top_contact_z"] + p["standoff_mm"]:
            problems.append("travel Z does not clear the hand top + standoff")
        else:
            lines, floors = gcode_lines(paths, p)   # in memory only
            checks = {"draw_moves_checked": independent_check(lines, floors, p), "program_lines": len(lines),
                      "floor": "per point, raw skin + floor margin, on re-read text",
                      "travel_clears_hand_top_plus_standoff": True}
            verdict = "PASS (preview)"

    # ---- figure ----
    intr = fr["colour_intrinsics"]
    fig = plt.figure(figsize=(15, 9.5))
    ax0 = fig.add_subplot(2, 2, 1)
    ax0.imshow(fr["colour_rgb"])
    poly = np.vstack([region["polygon_px"], region["polygon_px"][:1]])
    ax0.plot(poly[:, 0], poly[:, 1], "-", color="#185FA5", lw=1.2, label="accepted region")
    for i, s in enumerate(placed):  # where the design lands on the photo (measured points only)
        for st, cam in measure_under_plan(s["plan"], fr, mask, head, xy["result"]):
            if cam is not None:
                ax0.plot(intr["fx"] * cam[0] / cam[2] + intr["ppx"], intr["fy"] * cam[1] / cam[2] + intr["ppy"],
                         ".", color="#7a3b10", ms=1.5)
    ax0.legend(fontsize=8, loc="lower right"); ax0.set_xticks([]); ax0.set_yticks([])
    ax0.set_title("Design on the colour frame (middle of the median window)")
    ax1 = fig.add_subplot(2, 2, 2)
    for i, s in enumerate(placed, start=1):
        q = np.vstack([s["plan"], s["plan"][:1]]) if s["closed"] else s["plan"]
        bad = any(r["stroke"] == i and r["ok_points"] < r["points"] for r in per)
        ax1.plot(q[:, 0], q[:, 1], "-", color="#E24B4A" if bad else "#3B6D11", lw=1.4)
        ax1.annotate(str(i), q[0], fontsize=7, color="#555555")
    ax1.plot(*centre, "+", color="#185FA5", ms=12, mew=2)
    ax1.set_aspect("equal"); ax1.grid(alpha=.3)
    ax1.set_title("Top view (machine mm) - number = stroke order and start; red = refused stroke")
    ax2 = fig.add_subplot(2, 1, 2)
    x0 = 0
    for i, s in enumerate(paths):
        xs = np.arange(x0, x0 + len(s))
        ax2.plot(xs, s[:, 2], color="#854F0B", lw=1, label="measured skin" if i == 0 else None)
        ax2.plot(xs, s[:, 4], "--", color="#E24B4A", lw=1, label="floor = skin + 1" if i == 0 else None)
        ax2.plot(xs, s[:, 3], color="#3B6D11", lw=2, label=f"nozzle = smoothed skin + {p['standoff_mm']:g}" if i == 0 else None)
        x0 += len(s) + 3
    ax2.axhline(top["hand_top_contact_z"], color="#888888", ls=":", lw=1, label="hand top")
    ax2.grid(alpha=.3); ax2.legend(fontsize=8, ncol=4); ax2.set_ylabel("machine Z (mm)")
    ax2.set_xlabel("points, stroke after stroke (gaps = lift to travel Z)")
    ax2.set_title("Heights along the design")
    tt = run_time_s(paths, p) if paths else None
    fig.suptitle(f"HENNA DESIGN PREVIEW - {verdict} - NOT VALIDATED - motion_ready=false - no G-code written\n"
                 f"{os.path.basename(cfg['design']['svg'])}, fit {cfg['design']['fit_mm']:g} mm, {len(placed)} strokes, "
                 f"{sum(s['length_mm'] for s in placed):.0f} mm of line" + (f", about {tt / 60:.1f} min to run" if tt else ""),
                 color="#A32D2D" if problems else "#3B6D11", fontsize=11)
    fig.tight_layout(); fig.savefig(os.path.join(out, "design_preview.png"), dpi=100); plt.close(fig)

    allz = np.concatenate([s[:, 3] for s in paths]) if paths else np.array([np.nan])
    alls = np.concatenate([s[:, 2] for s in paths]) if paths else np.array([np.nan])
    rep = {"schema": "henna_design_preview/1", "created": stamp, "verdict": verdict, "problems": problems,
           "motion_ready": MOTION_READY, "gcode_written": False, "config": cfg,
           "design": {"svg": cfg["design"]["svg"], "fit_mm": cfg["design"]["fit_mm"], "mm_per_svg_unit": round(scale, 5),
                      "centre_machine_xy": centre, "rotation_deg": cfg["design"].get("rotation_deg", 0.0),
                      "strokes": len(placed), "line_length_mm": round(sum(s["length_mm"] for s in placed), 1)},
           "per_stroke": per, "hand_top": top,
           "skin_z_range": [round(float(np.nanmin(alls)), 3), round(float(np.nanmax(alls)), 3)],
           "nozzle_z_range": [round(float(np.nanmin(allz)), 3), round(float(np.nanmax(allz)), 3)],
           "corners": corner_stats(paths) if paths else None, "estimated_run_time_s": tt,
           "time_note": f"descent and drawing at the draw feed; rapids at about {RAPID_MM_MIN:g} mm/min",
           "checks": checks, "depth": {k2: v for k2, v in fr["multi"].items() if k2 != "frame_numbers_depth_colour"},
           "labels": {"x_y": "PLANNED design; skin measured under each point (median depth)",
                      "z": "smoothed MEASURED skin + standoff, never below raw skin + floor margin",
                      "interpolated_skin_points": 0}}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k2: rep[k2] for k2 in ("verdict", "problems", "design", "hand_top", "skin_z_range",
                                             "nozzle_z_range", "corners", "estimated_run_time_s", "checks")}, indent=2))
    print("Wrote:", out)
    if problems:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
