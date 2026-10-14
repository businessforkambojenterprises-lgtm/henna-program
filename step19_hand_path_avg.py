"""Step 19: HAND PATH PREVIEW with MULTI-FRAME depth - preview only, NO G-code written.

A plan-view design (true circle in machine X/Y) on the user's ACCEPTED hand region, with the
skin height under each point taken from the per-pixel median of --frames frames around the
hand-detection frame (henna_preview.recording_multi). Pixels measured too rarely or moving
(e.g. a twitching finger) are unusable, so the path breaks there - nothing is bridged or filled.

Same path rules as step 16: exact planned X/Y, nozzle = max(smoothed skin + standoff,
raw skin + floor margin), travel Z must clear the hand top + standoff. The G-code text is
built in memory only, to run the same independent checks; it is never written to disk.
motion_ready = false. No machine connection.
"""
import argparse
import datetime as dt
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path

from henna_preview import MOTION_READY
from henna_preview.calibration import dot_camera_mm, predict
from henna_preview.depth_scale import correct_depth
from henna_preview.nozzle_toolpath import NozzlePathError, check_params, gcode_lines, independent_check
from henna_preview.plan_design import plan_points
from henna_preview.recording_multi import read_frames_median
from henna_preview.region import convex_hull, polygon_mask, require_accepted
from henna_preview.smooth_path import build_smooth_strokes, corner_stats, measure_under_plan
from step12_nozzle_sim import ROOT, _j


from henna_preview.head_mask import head_pixels  # noqa: E402  (the machine head's own pixels, 2026-10-14)


def hand_top_from_raw(raw, scale, landmarks_px, k_mm, head_z, depth_fn, grow_px=25):
    """Highest measured skin inside the grown landmark hull (0.5th percentile depth) -> contact Z.
    The machine head's own pixels (head_pixels) never count as skin."""
    hull = np.array(convex_hull(landmarks_px))
    c = hull.mean(0)
    hull = c + (hull - c) * (1 + grow_px / max(1.0, np.linalg.norm(hull - c, axis=1).mean()))
    h, w = raw.shape
    v, u = np.mgrid[0:h, 0:w]
    inside = Path(hull).contains_points(np.column_stack([u.ravel(), v.ravel()])).reshape(h, w)
    head = head_pixels(raw, scale, k_mm, depth_fn)
    d = raw[inside & (raw > 0) & ~head].astype(float) * scale * 1000.0
    if not len(d):
        raise NozzlePathError("no usable depth over the hand")
    d_min = float(depth_fn(float(np.percentile(d, 0.5))))
    return {"min_depth_mm": round(d_min, 1), "hand_top_contact_z": round(head_z - (d_min - k_mm), 2),
            "pixels": int(len(d)), "head_pixels_excluded": int((inside & head).sum()),
            "note": "0.5th percentile of usable depth inside grown landmark hull, the machine head's own pixels excluded"}


def region_centre_machine(polygon_px, frame, head, xy_cal):
    """Machine X/Y under the pixel centroid of the accepted region (measured depth there)."""
    poly = np.asarray(polygon_px, float)
    uv = poly.mean(0)
    pc, n = dot_camera_mm(frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"],
                          uv, window=3)
    if pc is None:
        raise NozzlePathError("no usable depth at the region centre")
    m = predict(np.asarray(pc[:2]), [head["x"], head["y"]], np.asarray(xy_cal["R"]), np.asarray(xy_cal["t_mm"]))
    return [round(float(m[0]), 2), round(float(m[1]), 2)], [round(float(uv[0]), 1), round(float(uv[1]), 1)]


def path_for(frame, cfg, mask, head, xy_cal, k, depth_fn, centre, p):
    d = cfg["design"]
    plan, closed = plan_points(d["shape"], d["diameter_mm"], centre, d["rotation_deg"], spacing_mm=0.5)
    measured = measure_under_plan(plan, frame, mask, head, xy_cal)
    strokes, stats = build_smooth_strokes(plan, closed, measured, head, k, depth_fn, p["standoff_mm"],
                                          p["floor_above_skin_mm"], jump_mm=d["jump_mm"])
    return plan, measured, strokes, stats


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

    # median depth around the exact frame the hand was detected and the region accepted on
    fi, nf = int(hand_rep["input"]["frame_index"]), int(cfg["frames"])
    if fi - nf // 2 < 0:
        raise NozzlePathError(f"hand frame {fi} is too early for a {nf}-frame window")
    fr = read_frames_median(hand_rep["input"]["path"], fi - nf // 2, nf, cfg["min_valid_frac"], cfg["max_mad_mm"])
    with np.load(os.path.join(ROOT, cfg["hand_run_dir"], "frame_bundle.npz")) as b:
        bundle_depth, landmarks = b["depth_raw_aligned"], b["landmarks_px"]
    if not np.array_equal(bundle_depth, fr["depth_raw_single_middle"]):
        raise NozzlePathError("middle frame of the median window is not the hand-detection frame")
    single = dict(fr, depth_raw_aligned=fr["depth_raw_single_middle"])
    h, w = fr["depth_raw_aligned"].shape
    mask = polygon_mask(region["polygon_px"], w, h)

    centre, centre_px = (region_centre_machine(region["polygon_px"], fr, head, xy["result"])
                         if cfg["design"]["centre_machine_xy"] == "region_centroid"
                         else (cfg["design"]["centre_machine_xy"], None))
    plan, meas_m, sm, stm = path_for(fr, cfg, mask, head, xy["result"], k, depth_fn, centre, p)
    _, meas_1, s1, st1 = path_for(single, cfg, mask, head, xy["result"], k, depth_fn, centre, p)
    top = hand_top_from_raw(fr["depth_raw_aligned"], fr["depth_scale_m_per_unit"], landmarks, k, head["z"], depth_fn)
    if p["travel_z"] < top["hand_top_contact_z"] + p["standoff_mm"]:
        raise NozzlePathError("travel Z does not clear the hand top + standoff")
    lines, floors = gcode_lines(sm, p)          # in memory only - same checks as the simulators
    n_draw = independent_check(lines, floors, p)

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(ROOT, "outputs", "step19", stamp)
    os.makedirs(out, exist_ok=True)
    intr = fr["colour_intrinsics"]
    fig = plt.figure(figsize=(15, 9.5))
    ax0 = fig.add_subplot(2, 2, 1)
    ax0.imshow(fr["colour_rgb"])
    poly = np.vstack([region["polygon_px"], region["polygon_px"][:1]])
    ax0.plot(poly[:, 0], poly[:, 1], "-", color="#185FA5", lw=1.2, label="accepted region")
    for (st, cam) in meas_m:  # pinhole projection for display only
        if st == "ok":
            ax0.plot(intr["fx"] * cam[0] / cam[2] + intr["ppx"], intr["fy"] * cam[1] / cam[2] + intr["ppy"],
                     ".", color="#3B6D11", ms=3)
    ax0.plot([], [], ".", color="#3B6D11", label="path on usable skin")
    ax0.legend(fontsize=8, loc="lower right"); ax0.set_title("Colour frame (middle of the median window)")
    ax0.set_xticks([]); ax0.set_yticks([])
    ax1 = fig.add_subplot(2, 2, 2)
    ok = np.array([m[0] == "ok" for m in meas_m])
    ax1.plot(plan[:, 0], plan[:, 1], "-", color="#BBBBBB", lw=1, label="planned circle")
    if (~ok).any():
        ax1.plot(plan[~ok, 0], plan[~ok, 1], "x", color="#E24B4A", ms=5, label="no usable skin (path breaks)")
    for i, s in enumerate(sm):
        ax1.plot(s[:, 0], s[:, 1], "-", color="#3B6D11", lw=2, label="nozzle path" if i == 0 else None)
    ax1.plot(*centre, "+", color="#185FA5", ms=12, mew=2, label="centre (region middle)")
    ax1.set_aspect("equal"); ax1.grid(alpha=.3); ax1.legend(fontsize=8); ax1.set_title("Top view (machine mm)")
    for j, (strokes, t) in enumerate(((s1, "ONE frame"), (sm, f"MEDIAN of {nf} frames"))):
        axh = fig.add_subplot(2, 2, 3 + j)
        for i, s in enumerate(strokes):
            axh.plot(s[:, 2], color="#854F0B", lw=1, label="measured skin" if i == 0 else None)
            axh.plot(s[:, 4], "--", color="#E24B4A", lw=1, label="floor = skin + 1" if i == 0 else None)
            axh.plot(s[:, 3], color="#3B6D11", lw=2, label=f"nozzle = smoothed skin + {p['standoff_mm']:g}" if i == 0 else None)
        axh.axhline(top["hand_top_contact_z"], color="#888888", ls=":", lw=1, label="hand top")
        axh.grid(alpha=.3); axh.legend(fontsize=8); axh.set_xlabel("point along each stroke"); axh.set_ylabel("machine Z (mm)")
        axh.set_title(f"Heights - {t} ({len(strokes)} stroke(s))")
    fig.suptitle("HAND PATH PREVIEW - NOT VALIDATED - motion_ready=false - no G-code written", color="#A32D2D", fontsize=11)
    fig.tight_layout(); fig.savefig(os.path.join(out, "hand_path_preview.png"), dpi=100); plt.close(fig)

    def zr(strokes, col):
        v = np.concatenate([s[:, col] for s in strokes])
        return [round(float(v.min()), 3), round(float(v.max()), 3)]

    rep = {"schema": "hand_path_preview_avg/1", "created": stamp, "motion_ready": MOTION_READY, "gcode_written": False,
           "config": cfg, "centre_machine_xy": centre, "centre_pixel_uv": centre_px,
           "depth": {k2: v for k2, v in fr["multi"].items() if k2 != "frame_numbers_depth_colour"},
           "hand_top": top,
           "median": {"path_stats": stm, "corners": corner_stats(sm), "skin_z_range": zr(sm, 2), "nozzle_z_range": zr(sm, 3)},
           "single_frame": {"path_stats": st1, "corners": corner_stats(s1), "skin_z_range": zr(s1, 2), "nozzle_z_range": zr(s1, 3)},
           "checks": {"draw_moves_checked": n_draw, "floor": "per point, raw skin + floor margin, on re-read text",
                      "travel_clears_hand_top_plus_standoff": True},
           "labels": {"x_y": "PLANNED circle; skin measured under each point (median depth)",
                      "z": "smoothed MEASURED skin + standoff, never below raw skin + floor margin",
                      "interpolated_skin_points": 0}}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k2: rep[k2] for k2 in ("centre_machine_xy", "depth", "hand_top", "median", "single_frame", "checks")},
                     indent=2))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
