"""Step 14: fit the camera depth scale from recordings at several head Z (same X/Y, scene unchanged).

Read-only on recordings. Writes depth_scale.json + a plot. No machine connection. motion_ready=false.
Example: .venv\\Scripts\\python step14_depth_scale.py --config validation\\S2026-10-05-A\\depth_scale_inputs.json
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
from henna_preview.depth_scale import fit_scale, measure, spot_grid
from henna_preview.recording import frame_pair_count, read_frame


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    recs = cfg["recordings"]
    xy = set((r["head"]["x"], r["head"]["y"]) for r in recs)
    if len(xy) != 1:
        raise SystemExit("all recordings must share the same head X/Y")
    frames = [read_frame(r["path"], frame_pair_count(r["path"]) // 2) for r in recs]
    zs = [r["head"]["z"] for r in recs]
    ref_i = zs.index(cfg["z_ref"])
    spots = spot_grid(frames[ref_i], step_px=cfg.get("grid_step_px", 40))
    D, kept = measure(frames, zs, spots)
    fit = fit_scale(D, zs, cfg["z_ref"])
    # Robust pass: drop spots that are not a clean straight line (edges flipping between surfaces).
    x_all = np.array(zs) - cfg["z_ref"]
    own_rms = np.array([np.sqrt(np.mean((row - np.polyval(np.polyfit(x_all, row, 1), x_all)) ** 2)) for row in D])
    med = float(np.median(fit["per_spot_slope"]))
    good = (own_rms <= cfg.get("max_spot_rms_mm", 0.5)) & (np.abs(fit["per_spot_slope"] - med) <= cfg.get("max_slope_dev", 0.1))
    all_fit = fit
    fit = fit_scale(D[good], zs, cfg["z_ref"])
    D = D[good]
    per = fit["per_spot_slope"]
    res = fit["residuals"]
    os.makedirs(a.out, exist_ok=True)
    out = {
        "schema": "camera_depth_scale/1", "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY, "inputs": cfg,
        "model": "d_camera = a_spot + g * (Z_head - Z_ref); correction d_true = d_ref + (d_camera - d_ref) / g",
        "g": round(fit["g"], 5), "d_ref_mm": cfg["d_ref_mm"],
        "g_all_spots_before_outlier_removal": round(all_fit["g"], 5),
        "outlier_rule": "drop spots whose own straight-line fit RMS > 0.5 mm or slope > 0.1 from median",
        "spots_used": int(good.sum()), "spots_dropped_as_outliers": int((~good).sum()),
        "spots_measured_in_all_recordings": len(kept), "spots_tried": len(spots),
        "fit_rms_mm": round(fit["rms_mm"], 3),
        "per_spot_slope": {"min": round(float(per.min()), 4), "median": round(float(np.median(per)), 4),
                           "max": round(float(per.max()), 4), "p5": round(float(np.percentile(per, 5)), 4),
                           "p95": round(float(np.percentile(per, 95)), 4)},
        "mean_residual_by_head_z_mm": {str(z): round(float(res[:, i].mean()), 3) for i, z in enumerate(zs)},
        "spot_depth_range_mm": [round(float(D.min()), 1), round(float(D.max()), 1)],
        "note": "Z axis verified by caliper (49.89 mm for 50 commanded), so g describes the camera.",
    }
    json.dump(out, open(os.path.join(a.out, "depth_scale.json"), "w", encoding="utf-8"), indent=2)
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    x = np.array(zs)
    for row in D[:: max(1, len(D) // 60)]:
        ax[0].plot(x, row - row[ref_i], color="#888", lw=0.5, alpha=0.6)
    ax[0].plot(x, x - cfg["z_ref"], "k--", label="if camera were exact (slope 1)")
    ax[0].plot(x, fit["g"] * (x - cfg["z_ref"]), color="#3B6D11", lw=2, label=f"fit g = {fit['g']:.4f}")
    ax[0].set_xlabel("head Z (mm)"); ax[0].set_ylabel("depth change vs Z ref (mm)"); ax[0].legend(fontsize=8)
    ax[0].grid(alpha=.3); ax[0].set_title("Camera depth change vs real head move")
    ax[1].hist(per, bins=30, color="#378ADD"); ax[1].axvline(1.0, color="k", ls="--")
    ax[1].set_xlabel("slope per spot"); ax[1].set_title("Spot-by-spot slope"); ax[1].grid(alpha=.3)
    fig.tight_layout(); fig.savefig(os.path.join(a.out, "depth_scale.png"), dpi=110); plt.close(fig)
    print(json.dumps({k: out[k] for k in out if k not in ("inputs", "model", "schema", "created")}, indent=2))


if __name__ == "__main__":
    main()
