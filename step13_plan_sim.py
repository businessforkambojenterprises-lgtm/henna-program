"""Step 13: plan-view (option A) NOZZLE SIMULATION - a true circle in machine X/Y on measured skin.

Same inputs, limits and checks as step 12 (nozzle_sim_inputs.json), but the design is
planned in machine X/Y and the skin is measured under each point. NOT VALIDATED - DO NOT SEND.
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
from henna_preview.nozzle_toolpath import NozzlePathError, check_params, gcode_lines, independent_check, map_strokes
from henna_preview.depth_scale import correct_depth
from henna_preview.plan_design import plan_points, project_plan
from henna_preview.region import polygon_mask, require_accepted
from step12_nozzle_sim import ROOT, _j, hand_top


def load_frame(hand_run_dir):
    z = np.load(os.path.join(ROOT, hand_run_dir, "frame_bundle.npz"))
    return {"depth_raw_aligned": z["depth_raw_aligned"], "depth_scale_m_per_unit": float(z["depth_scale_m_per_unit"]),
            "colour_intrinsics": json.loads(str(z["colour_intrinsics_json"])), "colour_rgb": z["colour_rgb"]}


def build(cfg, shape, diameter, centre, rotation, jump):
    p = {k: cfg[k] for k in ("standoff_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min",
                             "travel_z", "firmware_z_floor")}
    check_params(p)
    head = cfg["hand_capture"]["head"]
    region = _j(os.path.join(cfg["region_dir"], "region.json"))
    hand_rep = _j(os.path.join(cfg["hand_run_dir"], "report.json"))
    if os.path.basename(hand_rep["input"]["path"]) != cfg["hand_capture"]["recording"]:
        raise NozzlePathError("hand run is not the recording whose head pose is given")
    require_accepted(region, hand_rep["input"]["sha256"], hand_rep["input"]["frame_index"])
    xy, zc = _j(cfg["xy_calibration"]), _j(cfg["z_calibration"])
    if "nozzle" not in str(xy.get("tool", "")).lower() or "nozzle" not in str(zc.get("tool", "")).lower():
        raise NozzlePathError("X/Y and Z calibrations must both be NOZZLE calibrations")
    k = float(zc["result"]["k_mm"])
    depth_fn, scale_info = None, None
    if cfg.get("depth_scale"):
        ds = _j(cfg["depth_scale"])
        g, d_ref = float(ds["g"]), float(ds["d_ref_mm"])
        if not 0.9 < g < 1.1:
            raise NozzlePathError(f"depth scale g={g} is outside the plausible range 0.9..1.1")
        depth_fn = lambda d, g=g, d_ref=d_ref: correct_depth(d, g, d_ref)  # noqa: E731
        scale_info = {"g": g, "d_ref_mm": d_ref, "file": cfg["depth_scale"]}
    frame = load_frame(cfg["hand_run_dir"])
    h, w = frame["depth_raw_aligned"].shape
    mask = polygon_mask(region["polygon_px"], w, h)
    plan, closed = plan_points(shape, diameter, centre, rotation)
    proj = project_plan(plan, closed, frame, mask, head, xy["result"], jump_mm=jump)
    strokes = map_strokes(proj["segments_cam_xyz"], head, xy["result"], k, p, depth_fn)
    top = hand_top(cfg["hand_run_dir"], k, head["z"], depth_fn=depth_fn)
    if p["travel_z"] < top["hand_top_contact_z"] + p["standoff_mm"]:
        raise NozzlePathError("travel Z does not clear the hand top + standoff")
    lines, floors = gcode_lines(strokes, p)
    n = independent_check(lines, floors, p)
    allxy = np.vstack([s[:, :2] for s in strokes])
    rad = np.hypot(allxy[:, 0] - centre[0], allxy[:, 1] - centre[1])
    return {"p": p, "plan": plan, "strokes": strokes, "lines": lines, "draw_moves": n, "hand_top": top, "k": k,
            "projection": proj["stats"], "status": proj["status"], "depth_scale": scale_info,
            "roundness": {"planned_radius_mm": diameter / 2, "measured_radius_min_mm": round(float(rad.min()), 2),
                          "measured_radius_max_mm": round(float(rad.max()), 2),
                          "x_span_mm": round(float(np.ptp(allxy[:, 0])), 2), "y_span_mm": round(float(np.ptp(allxy[:, 1])), 2)}}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--shape", default="circle", choices=["circle", "arc"])
    ap.add_argument("--diameter-mm", type=float, default=20.0)
    ap.add_argument("--centre", type=float, nargs=2, required=True, metavar=("X", "Y"), help="machine X/Y")
    ap.add_argument("--rotation", type=float, default=0.0)
    ap.add_argument("--jump-mm", type=float, default=3.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    cfg = _j(a.config)
    r = build(cfg, a.shape, a.diameter_mm, a.centre, a.rotation, a.jump_mm)
    out = a.out or os.path.join(ROOT, "outputs", "step13", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "NOZZLE_SIM_DO_NOT_SEND.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(r["lines"]) + "\n")
    fig, ax = plt.subplots(1, 2, figsize=(10, 4.5))
    ax[0].plot(r["plan"][:, 0], r["plan"][:, 1], ":", color="#7F77DD", label="plan (true circle)")
    for s in r["strokes"]:
        ax[0].plot(s[:, 0], s[:, 1], "-", color="#3B6D11", lw=2, label="nozzle path (measured)")
    ax[0].set_aspect("equal"); ax[0].grid(alpha=.3); ax[0].set_xlabel("machine X (mm)"); ax[0].set_ylabel("machine Y (mm)")
    ax[0].set_title("Top view (equal scale)")
    hh, ll = ax[0].get_legend_handles_labels(); u = dict(zip(ll, hh)); ax[0].legend(u.values(), u.keys(), fontsize=8)
    for s in r["strokes"]:
        ax[1].plot(range(len(s)), s[:, 3], color="#3B6D11", label="nozzle Z")
        ax[1].plot(range(len(s)), s[:, 4], "--", color="#E24B4A", label="floor (skin + margin)")
        ax[1].plot(range(len(s)), s[:, 2], color="#854F0B", lw=1, label="skin")
    hh, ll = ax[1].get_legend_handles_labels(); u = dict(zip(ll, hh)); ax[1].legend(u.values(), u.keys(), fontsize=8)
    ax[1].set_xlabel("point along path"); ax[1].set_ylabel("machine Z (mm)"); ax[1].grid(alpha=.3)
    ax[1].set_title("Heights along the path")
    fig.suptitle("NOZZLE SIMULATION (plan view) - NOT VALIDATED - DO NOT SEND", color="#A32D2D", fontsize=10)
    fig.tight_layout(); fig.savefig(os.path.join(out, "plan_check.png"), dpi=110); plt.close(fig)
    rep = {"schema": "nozzle_plan_simulation/1", "created": dt.datetime.now().isoformat(timespec="seconds"),
           "status": "NOZZLE SIMULATION - NOT VALIDATED - DO NOT SEND", "motion_ready": MOTION_READY,
           "design": {"shape": a.shape, "diameter_mm": a.diameter_mm, "centre_machine_xy": a.centre,
                      "rotation_deg": a.rotation, "jump_mm": a.jump_mm, "planned_in": "machine X/Y (plan view, option A)"},
           "inputs": cfg, "k_mm": r["k"], "depth_scale": r["depth_scale"], "projection": r["projection"], "roundness": r["roundness"],
           "hand_top": r["hand_top"], "independent_check": f"passed: {r['draw_moves']} drawing moves",
           "skin_z_range": [round(float(min(s[:, 2].min() for s in r["strokes"])), 2),
                            round(float(max(s[:, 2].max() for s in r["strokes"])), 2)]}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k: rep[k] for k in ("depth_scale", "projection", "roundness", "skin_z_range", "hand_top")}, indent=2))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
