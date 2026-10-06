"""Step 17: PAPER TEST program - a 20 mm circle hovering over paper on the block (no hand, no dispensing).

Camera locates the paper surface; the circle is centred on the camera's estimate of the drawn cross;
X/Y are the planned circle, Z = smoothed measured paper + standoff, never below raw paper + floor margin.
The usable surface is limited to pixels whose measured height is within surface_band_mm of the paper
at the cross (so the path cannot run off the block edge). Program contains only G21/G90/G53 G0/G53 G1.
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
from henna_preview.recording import frame_pair_count, read_frame
from henna_preview.smooth_path import build_smooth_strokes, corner_stats, measure_under_plan
from step12_nozzle_sim import ROOT, _j


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--params", required=True)
    a = ap.parse_args()
    P = _j(a.params)
    xy = _j(P["xy_calibration"]); zc = _j(P["z_calibration"]); ds = _j(P["depth_scale"])
    if "nozzle" not in str(xy.get("tool", "")).lower():
        raise SystemExit("X/Y calibration is not a nozzle calibration")
    k, g, d_ref = float(zc["result"]["k_mm"]), float(ds["g"]), float(ds["d_ref_mm"])
    depth_fn = lambda d: correct_depth(d, g, d_ref)  # noqa: E731
    head = P["head"]
    fr = read_frame(P["recording"], frame_pair_count(P["recording"]) // 2)
    raw, scale = fr["depth_raw_aligned"], fr["depth_scale_m_per_unit"]
    # surface mask: measured pixels within the band around the paper height at the cross
    pc, _ = dot_camera_mm(raw, scale, fr["colour_intrinsics"], P["cross_pixel_uv"])
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
    # per-point floor check (re-read text)
    sp = {"standoff_mm": P["standoff_mm"], "floor_above_skin_mm": P["floor_above_surface_mm"],
          "draw_feed_mm_min": P["draw_feed_mm_min"], "travel_feed_mm_min": P["travel_feed_mm_min"],
          "travel_z": P["travel_z"], "firmware_z_floor": -97.0}
    sim_lines, floors = gcode_lines(strokes, sp)
    floor_check(sim_lines, floors, sp)
    # runnable program, global floor = lowest per-point floor
    q = {"lift_mm": 1.0, "travel_z": P["travel_z"], "floor_z": float(min(floors)),
         "draw_feed_mm_min": P["draw_feed_mm_min"], "travel_feed_mm_min": P["travel_feed_mm_min"]}
    pts = [np.column_stack([s[:, 0], s[:, 1], s[:, 3]]) for s in strokes]
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    header = ["PAPER TEST - BLOCK ONLY - NO HAND - NO DISPENSING",
              f"made {stamp} by step17_paper_test.py - 20 mm circle centred on camera estimate X{d['centre_machine_xy'][0]} Y{d['centre_machine_xy'][1]}",
              f"nozzle {P['standoff_mm']} mm above measured paper - floor paper + {P['floor_above_surface_mm']} mm - travel Z {P['travel_z']}",
              "Run with UGS - home first - stand at the emergency stop"]
    lines = program(pts, q, header)
    program_check(lines, q)
    out = os.path.join(ROOT, "outputs", "step17", stamp)
    os.makedirs(out, exist_ok=True)
    name = f"PAPER_TEST_NO_HAND_{stamp}.gcode"
    with open(os.path.join(out, name), "w", encoding="ascii", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    s = strokes[0]
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.6))
    ax[0].plot(s[:, 0], s[:, 1], "-", color="#3B6D11", lw=2, label="planned nozzle circle")
    ax[0].plot(*d["centre_machine_xy"], "+", color="#185FA5", ms=14, mew=2, label="cross (camera estimate)")
    if P.get("cross_machine_xy_nozzle_jogged"):
        ax[0].plot(*P["cross_machine_xy_nozzle_jogged"], "x", color="#E24B4A", ms=10, mew=2, label="cross (nozzle jogged)")
    ax[0].set_aspect("equal"); ax[0].grid(alpha=.3); ax[0].legend(fontsize=8); ax[0].set_title("Top view (machine mm)")
    ax[1].plot(s[:, 2], color="#854F0B", label="measured paper")
    ax[1].plot(s[:, 4], "--", color="#E24B4A", label="floor = paper + 1")
    ax[1].plot(s[:, 3], color="#3B6D11", lw=2, label="nozzle = smoothed paper + 2")
    ax[1].grid(alpha=.3); ax[1].legend(fontsize=8); ax[1].set_title("Heights (machine Z)")
    fig.suptitle("PAPER TEST - BLOCK ONLY - NO HAND - NO DISPENSING", color="#A32D2D", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(out, "paper_test_preview.png"), dpi=110); plt.close(fig)
    allz = s[:, 3]
    rep = {"schema": "paper_test/1", "created": stamp, "program_file": name, "params": P,
           "paper_z_at_cross": round(float(z_cross), 3), "path_stats": stats, "corners": corner_stats(strokes),
           "nozzle_z_range": [round(float(allz.min()), 3), round(float(allz.max()), 3)],
           "paper_z_range_under_path": [round(float(s[:, 2].min()), 3), round(float(s[:, 2].max()), 3)],
           "global_floor_in_program": round(q["floor_z"], 3), "lines": len(lines),
           "checks": "per-point floor (paper + 1) on re-read text; allowed commands only; rapids only at travel Z"}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k2: rep[k2] for k2 in ("program_file", "paper_z_at_cross", "path_stats", "corners", "nozzle_z_range",
                                             "paper_z_range_under_path", "global_floor_in_program", "lines")}, indent=2))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
