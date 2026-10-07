"""Step 18: PAPER TEST with MULTI-FRAME depth (revision of step 17; step 17 is kept unchanged).

Same plan and checks as step 17 (20 mm circle on the camera's cross, nozzle = smoothed measured
paper + standoff, never below raw paper + floor margin), but the paper height comes from the
per-pixel median of --frames consecutive frames instead of one frame. Pixels measured in too
few frames or jumping too much are unusable (never filled).

Default: preview + report only, comparing the single middle frame with the median.
The program file is written only with --write-gcode (needs the user's approval for a run).
No machine connection.
"""
import argparse
import datetime as dt
import json
import os

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from henna_preview.calibration import dot_camera_mm
from henna_preview.depth_scale import correct_depth
from henna_preview.dry_run import independent_check as program_check, program
from henna_preview.nozzle_toolpath import gcode_lines, independent_check as floor_check
from henna_preview.plan_design import plan_points
from henna_preview.recording import frame_pair_count
from henna_preview.recording_multi import read_frames_median
from henna_preview.smooth_path import build_smooth_strokes, corner_stats, measure_under_plan
from step12_nozzle_sim import ROOT, _j


def build(P, fr, xy, k, depth_fn):
    """-> strokes, stats, z_cross for one frame dict (single or median depth)."""
    head = P["head"]
    raw, scale = fr["depth_raw_aligned"], fr["depth_scale_m_per_unit"]
    pc, _ = dot_camera_mm(raw, scale, fr["colour_intrinsics"], P["cross_pixel_uv"])
    if pc is None:
        raise SystemExit("no usable depth at the cross")
    z_cross = head["z"] - (depth_fn(pc[2]) - k)
    zmap = np.where(raw > 0, head["z"] - (depth_fn(raw.astype(float) * scale * 1000.0) - k), np.nan)
    mask = np.abs(zmap - z_cross) <= P["surface_band_mm"]
    d = P["design"]
    plan, closed = plan_points(d["shape"], d["diameter_mm"], d["centre_machine_xy"], d["rotation_deg"], spacing_mm=0.5)
    measured = measure_under_plan(plan, fr, mask, head, xy["result"])
    strokes, stats = build_smooth_strokes(plan, closed, measured, head, k, depth_fn, P["standoff_mm"],
                                          P["floor_above_surface_mm"], jump_mm=d["jump_mm"])
    if stats["strokes"] != 1 or stats["on_measured_skin"] != stats["planned_points"]:
        raise SystemExit(f"circle is not fully on the measured paper surface: {stats}")
    return strokes, stats, float(z_cross)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--params", required=True)
    ap.add_argument("--frames", type=int, default=15)
    ap.add_argument("--min-valid-frac", type=float, default=0.8)
    ap.add_argument("--max-mad-mm", type=float, default=1.0)
    ap.add_argument("--write-gcode", action="store_true", help="also write the runnable program (approval needed)")
    a = ap.parse_args()
    P = _j(a.params)
    xy = _j(P["xy_calibration"]); zc = _j(P["z_calibration"]); ds = _j(P["depth_scale"])
    if "nozzle" not in str(xy.get("tool", "")).lower():
        raise SystemExit("X/Y calibration is not a nozzle calibration")
    k, g, d_ref = float(zc["result"]["k_mm"]), float(ds["g"]), float(ds["d_ref_mm"])
    depth_fn = lambda d: correct_depth(d, g, d_ref)  # noqa: E731
    start = frame_pair_count(P["recording"]) // 2 - a.frames // 2  # middle frame = step 17's frame
    fr = read_frames_median(P["recording"], start, a.frames, a.min_valid_frac, a.max_mad_mm)
    single = dict(fr, depth_raw_aligned=fr["depth_raw_single_middle"])
    s1, st1, zc1 = build(P, single, xy, k, depth_fn)
    sm, stm, zcm = build(P, fr, xy, k, depth_fn)

    sp = {"standoff_mm": P["standoff_mm"], "floor_above_skin_mm": P["floor_above_surface_mm"],
          "draw_feed_mm_min": P["draw_feed_mm_min"], "travel_feed_mm_min": P["travel_feed_mm_min"],
          "travel_z": P["travel_z"], "firmware_z_floor": -97.0}
    sim_lines, floors = gcode_lines(sm, sp)
    floor_check(sim_lines, floors, sp)
    q = {"lift_mm": 1.0, "travel_z": P["travel_z"], "floor_z": float(min(floors)),
         "draw_feed_mm_min": P["draw_feed_mm_min"], "travel_feed_mm_min": P["travel_feed_mm_min"]}
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d = P["design"]
    header = ["PAPER TEST - BLOCK ONLY - NO HAND - NO DISPENSING",
              f"made {stamp} by step18_paper_test_avg.py - {a.frames}-frame median depth - 20 mm circle centred on camera estimate X{d['centre_machine_xy'][0]} Y{d['centre_machine_xy'][1]}",
              f"nozzle {P['standoff_mm']} mm above measured paper - floor paper + {P['floor_above_surface_mm']} mm - travel Z {P['travel_z']}",
              "Run with UGS - home first - stand at the emergency stop"]
    lines = program([np.column_stack([s[:, 0], s[:, 1], s[:, 3]]) for s in sm], q, header)
    program_check(lines, q)
    out = os.path.join(ROOT, "outputs", "step18", stamp)
    os.makedirs(out, exist_ok=True)
    name = None
    if a.write_gcode:
        name = f"PAPER_TEST_NO_HAND_AVG_{stamp}.gcode"
        with open(os.path.join(out, name), "w", encoding="ascii", newline="\n") as f:
            f.write("\n".join(lines) + "\n")

    def rng(v):
        return [round(float(v.min()), 3), round(float(v.max()), 3), round(float(v.max() - v.min()), 3)]

    fig, ax = plt.subplots(1, 2, figsize=(13, 4.8), sharey=True)
    for a_, s, t in ((ax[0], s1[0], "ONE frame (as step 17)"), (ax[1], sm[0], f"MEDIAN of {a.frames} frames")):
        a_.plot(s[:, 2], color="#854F0B", label=f"measured paper (range {np.ptp(s[:, 2]):.2f} mm)")
        a_.plot(s[:, 4], "--", color="#E24B4A", label="floor = paper + 1")
        a_.plot(s[:, 3], color="#3B6D11", lw=2, label=f"nozzle (range {np.ptp(s[:, 3]):.2f} mm)")
        a_.grid(alpha=.3); a_.legend(fontsize=8, loc="lower right"); a_.set_title(t); a_.set_xlabel("point along path")
    ax[0].set_ylabel("machine Z (mm)")
    fig.suptitle("PAPER TEST - BLOCK ONLY - NO HAND - NO DISPENSING - preview only"
                 + ("" if a.write_gcode else " (no program written)"), color="#A32D2D", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(out, "single_vs_median.png"), dpi=110); plt.close(fig)

    one, med = s1[0], sm[0]
    rep = {"schema": "paper_test_avg/1", "created": stamp, "program_file": name, "params": P,
           "depth": {k2: v for k2, v in fr["multi"].items() if k2 != "frame_numbers_depth_colour"},
           "single_frame": {"paper_z_at_cross": round(zc1, 3), "paper_z_range_under_path": rng(one[:, 2]),
                            "nozzle_z_range": rng(one[:, 3]), "corners": corner_stats(s1), "path_stats": st1},
           "median": {"paper_z_at_cross": round(zcm, 3), "paper_z_range_under_path": rng(med[:, 2]),
                      "nozzle_z_range": rng(med[:, 3]), "corners": corner_stats(sm), "path_stats": stm},
           "global_floor_in_program": round(q["floor_z"], 3), "lines": len(lines),
           "checks": "per-point floor (paper + 1) on re-read text; allowed commands only; rapids only at travel Z",
           "note": "ranges are [min, max, max-min]; the block top is flat (touches -61.9/-61.8/-61.8)"}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k2: rep[k2] for k2 in ("program_file", "depth", "single_frame", "median",
                                             "global_floor_in_program", "lines")}, indent=2))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
