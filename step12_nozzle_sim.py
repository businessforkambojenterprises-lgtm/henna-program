"""Step 12: NOZZLE SIMULATION toolpath for a recorded hand (NOT VALIDATED - DO NOT SEND).

Checks the whole input chain, maps the saved design's MEASURED points to machine
X/Y/Z with the verified nozzle X/Y calibration and the nozzle Z calibration, keeps
every drawing point at skin + standoff and above its own floor (skin + margin),
requires travel Z to clear the highest measured point of the whole hand, then
writes simulation text (header + invalid first line), a report and pictures.
No machine connection. motion_ready=false.
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
from henna_preview.calibration import predict
from henna_preview.nozzle_toolpath import (NozzlePathError, check_params, gcode_lines, independent_check,
                                           map_strokes)
from henna_preview.region import deproject_mm, require_accepted, convex_hull

ROOT = os.path.dirname(os.path.abspath(__file__))


def _j(p):
    with open(os.path.join(ROOT, p), encoding="utf-8") as f:
        return json.load(f)


def hand_top(hand_run_dir, k_mm, head_z, grow_px=25):
    """Highest measured skin point inside the (grown) hull of all 21 landmarks -> contact Z."""
    z = np.load(os.path.join(ROOT, hand_run_dir, "frame_bundle.npz"))
    raw, scale = z["depth_raw_aligned"], float(z["depth_scale_m_per_unit"])
    lm = z["landmarks_px"]
    hull = np.array(convex_hull(lm))
    c = hull.mean(0)
    hull = c + (hull - c) * (1 + grow_px / max(1.0, np.linalg.norm(hull - c, axis=1).mean()))
    h, w = raw.shape
    v, u = np.mgrid[0:h, 0:w]
    inside = Path(hull).contains_points(np.column_stack([u.ravel(), v.ravel()])).reshape(h, w)
    d = raw[inside & (raw > 0)].astype(float) * scale * 1000.0
    if not len(d):
        raise NozzlePathError("no measured depth over the hand")
    d_min = float(np.percentile(d, 0.5))  # robust to single-pixel speckle
    return {"min_depth_mm": round(d_min, 1), "hand_top_contact_z": round(head_z - (d_min - k_mm), 2),
            "pixels": int(len(d)), "note": "0.5th percentile of measured depth inside grown landmark hull"}


def build(cfg):
    p = {k: cfg[k] for k in ("standoff_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min",
                             "travel_z", "firmware_z_floor")}
    check_params(p)
    head = cfg["hand_capture"]["head"]
    design = _j(os.path.join(cfg["design_dir"], "design_report.json"))
    region = _j(os.path.join(cfg["region_dir"], "region.json"))
    xy = _j(cfg["xy_calibration"])
    zc = _j(cfg["z_calibration"])
    # chain checks
    if os.path.basename(design["inputs"]["recording"]) != cfg["hand_capture"]["recording"]:
        raise NozzlePathError("design was not made on the hand recording whose head pose is given")
    require_accepted(region, design["inputs"]["recording_sha256"], design["inputs"]["frame_index"])
    if "nozzle" not in str(xy.get("tool", "")).lower() or "nozzle" not in str(zc.get("tool", "")).lower():
        raise NozzlePathError("X/Y and Z calibrations must both be NOZZLE calibrations")
    k = float(zc["result"]["k_mm"])
    npz = np.load(os.path.join(ROOT, cfg["design_dir"], "design_path.npz"))
    segs = [npz[f"segment_{i}_measured_points_mm"] for i in range(100) if f"segment_{i}_measured_points_mm" in npz.files]
    strokes = map_strokes(segs, head, xy["result"], k, p)
    top = hand_top(cfg["hand_run_dir"], k, head["z"])
    if p["travel_z"] < top["hand_top_contact_z"] + p["standoff_mm"]:
        raise NozzlePathError(f"travel Z {p['travel_z']} does not clear the hand top "
                              f"({top['hand_top_contact_z']}) + standoff")
    lines, floors = gcode_lines(strokes, p)
    n_draw = independent_check(lines, floors, p)
    allz = np.concatenate([s[:, 3] for s in strokes])
    skin = np.concatenate([s[:, 2] for s in strokes])
    return {"params": p, "head": head, "k_mm": k, "strokes": strokes, "lines": lines, "floors": floors,
            "draw_moves": n_draw, "hand_top": top, "design": design, "region": region, "xy": xy, "zc": zc,
            "summary": {"strokes": len(strokes), "draw_points": int(len(allz)), "lines": len(lines),
                        "skin_z_range": [round(float(skin.min()), 2), round(float(skin.max()), 2)],
                        "draw_z_range": [round(float(allz.min()), 2), round(float(allz.max()), 2)],
                        "xy_range": [[round(float(min(s[:, 0].min() for s in strokes)), 2),
                                      round(float(max(s[:, 0].max() for s in strokes)), 2)],
                                     [round(float(min(s[:, 1].min() for s in strokes)), 2),
                                      round(float(max(s[:, 1].max() for s in strokes)), 2)]],
                        "clearance_travel_above_hand_top_mm": round(p["travel_z"] - top["hand_top_contact_z"], 2)}}


def render_sim(out_png, res, cfg):
    z = np.load(os.path.join(ROOT, cfg["region_dir"], "region_points_camera_mm.npz"))
    pts = z["points_mm"][::6]
    xy = res["xy"]["result"]
    head = res["head"]
    mxy = predict(pts[:, :2], [head["x"], head["y"]], np.asarray(xy["R"]), np.asarray(xy["t_mm"]))
    mz = head["z"] - (pts[:, 2] - res["k_mm"])
    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(projection="3d")
    ax.scatter(mxy[:, 0], mxy[:, 1], mz, s=0.6, c=mz, cmap="copper", alpha=0.6, label="measured palm (skin contact Z)")
    for s in res["strokes"]:
        ax.plot(s[:, 0], s[:, 1], s[:, 3], color="#3B6D11", lw=2.5, label="nozzle drawing path (skin + standoff)")
        ax.plot(s[:, 0], s[:, 1], s[:, 4], color="#E24B4A", lw=1, ls="--", label="per-point floor (skin + margin)")
        ax.plot([s[0, 0], s[0, 0]], [s[0, 1], s[0, 1]], [res["params"]["travel_z"], s[0, 3]], color="#378ADD", lw=1)
    ax.set_xlabel("machine X (mm)")
    ax.set_ylabel("machine Y (mm)")
    ax.set_zlabel("machine Z (mm)")
    h, l = ax.get_legend_handles_labels()
    uniq = dict(zip(l, h))
    ax.legend(list(uniq.values()), list(uniq.keys()), loc="upper left", fontsize=8)
    ax.set_title("NOZZLE SIMULATION - NOT VALIDATED - DO NOT SEND\nmachine coordinates; motion_ready=false",
                 fontsize=10, color="#A32D2D")
    fig.tight_layout()
    fig.savefig(out_png, dpi=110)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    cfg = _j(a.config)
    res = build(cfg)
    out = a.out or os.path.join(ROOT, "outputs", "step12", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "NOZZLE_SIM_DO_NOT_SEND.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(res["lines"]) + "\n")
    render_sim(os.path.join(out, "simulation_3d.png"), res, cfg)
    rep = {"schema": "nozzle_simulation_toolpath/1", "created": dt.datetime.now().isoformat(timespec="seconds"),
           "status": "NOZZLE SIMULATION - NOT VALIDATED - DO NOT SEND", "motion_ready": MOTION_READY,
           "inputs": cfg, "k_mm": res["k_mm"], "hand_top": res["hand_top"], "summary": res["summary"],
           "independent_check": f"passed: {res['draw_moves']} drawing moves each >= own floor; allowed commands only",
           "not_validated": ["Z scale check 0.978 unresolved (Z axis vs camera depth)",
                             "hand may move between capture and drawing - nothing tracks it",
                             "Z rail bottom block / holder clearance over the hand not modelled",
                             "no dispensing commands or pressure", "no dry run yet",
                             "firmware Z floor (-97) does not protect the hand"]}
    json.dump(rep, open(os.path.join(out, "report.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({"summary": res["summary"], "hand_top": res["hand_top"], "k_mm": res["k_mm"]}, indent=2))
    print("\n".join(res["lines"][:9]))
    print("Wrote:", out)


if __name__ == "__main__":
    main()
