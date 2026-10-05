"""Step 2: inspect an existing depth scan as camera-frame millimetre points.

Read-only on the input file. Writes only into --out (default outputs/step02).
No camera, no controller, no G-code. motion_ready is always false.

Example:
    python step02_inspect_depth.py --depth-units mm
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import time

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap

from henna_preview import MOTION_READY
from henna_preview.depth_surface import (
    MISSING, NOISY, UNSTABLE, VALID, STATUS_NAMES,
    classify_depth, deproject, raw_to_mm, require_intrinsics, status_counts,
)

DEFAULT_INPUT = os.path.join(os.path.expanduser("~"), "palm_3d_scans", "ptg_scan_raw.npz")
FRAME = "RealSense SDK camera frame (pinhole deprojection): x right, y down, z forward; mm"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--depth-units", required=True, choices=["mm", "raw"],
                    help="units of the stored 'median' array; the file does not say, so you must choose")
    ap.add_argument("--units-confirmed", action="store_true",
                    help="set only once you have confirmed the units from how the file was made")
    ap.add_argument("--min-valid-frac", type=float, default=0.6,
                    help="pixels measured in fewer frames than this are 'unstable'")
    ap.add_argument("--max-mad-mm", type=float, default=1.0,
                    help="pixels whose frame-to-frame spread exceeds this are 'noisy'")
    ap.add_argument("--out", default=os.path.join("outputs", "step02"))
    args = ap.parse_args()

    t0 = time.perf_counter()
    timings = {}
    src = os.path.abspath(args.input)
    with np.load(src, allow_pickle=False) as z:
        data = {k: z[k] for k in z.files}
    timings["load_s"] = time.perf_counter() - t0

    median = data["median"].astype(np.float64)
    fx, fy, ppx, ppy = (float(v) for v in data["intr"])  # stored order: fx, fy, ppx, ppy
    h, w = median.shape
    intr = require_intrinsics(
        w, h, fx, fy, ppx, ppy,
        distortion="not stored in input; pinhole used, no distortion correction applied",
        source=f"{os.path.basename(src)}:intr (width/height taken from depth array shape)")
    depth_scale = float(data["depth_scale"]) if "depth_scale" in data else None

    finite = median[np.isfinite(median)]
    unit_evidence = {
        "stored_range": [float(finite.min()), float(finite.max())],
        "if_mm_range_mm": [float(finite.min()), float(finite.max())],
        "if_raw_range_mm": (raw_to_mm([finite.min(), finite.max()], depth_scale).tolist()
                            if depth_scale else None),
    }
    depth_mm = median if args.depth_units == "mm" else raw_to_mm(median, depth_scale)

    t1 = time.perf_counter()
    vf = data["valid_frac"].astype(np.float64) if "valid_frac" in data else None
    mad = data["mad"].astype(np.float64) if "mad" in data else None
    if mad is not None and args.depth_units == "raw":
        mad = raw_to_mm(mad, depth_scale)
    status = classify_depth(depth_mm, vf, mad, args.min_valid_frac, args.max_mad_mm)
    xyz = deproject(depth_mm, intr)
    xyz[status != VALID] = np.nan  # rejected pixels are excluded, not repaired
    timings["process_s"] = time.perf_counter() - t1

    os.makedirs(args.out, exist_ok=True)
    t2 = time.perf_counter()

    # Picture 1: depth + status map
    colors = {VALID: "#2ca02c", MISSING: "#d62728", UNSTABLE: "#ff7f0e", NOISY: "#9467bd"}
    cmap = ListedColormap([colors[k] for k in sorted(colors)])
    fig, axs = plt.subplots(1, 2, figsize=(13, 5))
    shown = np.where(status == VALID, depth_mm, np.nan)
    im = axs[0].imshow(shown, cmap="viridis")
    axs[0].set_facecolor("#ff00ff")
    axs[0].set_title("Accepted measured depth (mm)\nmagenta = missing or rejected (not filled)")
    fig.colorbar(im, ax=axs[0], fraction=0.046)
    axs[1].imshow(status, cmap=cmap, vmin=0, vmax=3, interpolation="nearest")
    counts = status_counts(status)
    axs[1].set_title("Pixel status: " + ", ".join(
        f"{STATUS_NAMES[k]}={counts[STATUS_NAMES[k]]}" for k in sorted(colors)), fontsize=9)
    for k, c in colors.items():
        axs[1].plot([], [], "s", color=c, label=STATUS_NAMES[k])
    axs[1].legend(loc="lower right", fontsize=8)
    for a in axs:
        a.set_xlabel("image u (px)")
        a.set_ylabel("image v (px)")
    fig.suptitle(f"{os.path.basename(src)} | units: {args.depth_units}"
                 f"{'' if args.units_confirmed else ' (UNCONFIRMED)'} | motion_ready=false", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "depth_status.png"), dpi=110)
    plt.close(fig)

    # Picture 2: 3D scatter of accepted points (subsampled for drawing only)
    pts = xyz[status == VALID]
    step = max(1, len(pts) // 25000)
    sub = pts[::step]
    fig = plt.figure(figsize=(8, 7))
    ax = fig.add_subplot(projection="3d")
    ax.scatter(sub[:, 0], sub[:, 1], sub[:, 2], c=sub[:, 2], s=0.5, cmap="viridis")
    ax.set_xlabel("camera x (mm, right)")
    ax.set_ylabel("camera y (mm, down)")
    ax.set_zlabel("camera z (mm, forward)")
    ax.invert_zaxis()  # nearer points drawn on top, like looking down from the camera
    ax.set_title(f"Measured points only, every {step}th of {len(pts)} shown\n{FRAME}", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(args.out, "points_3d.png"), dpi=110)
    plt.close(fig)

    np.savez_compressed(os.path.join(args.out, "points_camera_mm.npz"),
                        xyz_mm=xyz.astype(np.float32), status=status)
    timings["write_s"] = time.perf_counter() - t2
    timings["total_s"] = time.perf_counter() - t0

    report = {
        "step": "02 inspect existing depth (read-only)",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY,
        "input": {
            "path": src,
            "sha256": sha256(src),
            "arrays": {k: [str(v.dtype), list(v.shape)] for k, v in data.items()},
            "camera": json.loads(str(data["cam"])) if "cam" in data else None,
            "capture_time_local": (dt.datetime.fromtimestamp(float(data["time"])).isoformat(timespec="seconds")
                                   if "time" in data else None),
            "frames_combined": int(data["n_frames"]) if "n_frames" in data else None,
            "head_position_at_capture_as_recorded": data["park"].tolist() if "park" in data else None,
            "head_position_note": "copied from file for traceability only; not verified, not used",
        },
        "has_colour_image": False,
        "intrinsics": intr.to_dict(),
        "depth_scale_m_per_unit": depth_scale,
        "depth_units_chosen": args.depth_units,
        "depth_units_confirmed": bool(args.units_confirmed),
        "unit_evidence": unit_evidence,
        "coordinate_frame": FRAME,
        "not_machine_coordinates": True,
        "params": {"min_valid_frac": args.min_valid_frac, "max_mad_mm": args.max_mad_mm},
        "pixel_status_counts": counts,
        "points": {"measured_accepted": int(len(pts)), "interpolated": 0, "planned": 0},
        "valid_xyz_range_mm": ({ax_: [float(pts[:, i].min()), float(pts[:, i].max())]
                                for i, ax_ in enumerate("xyz")} if len(pts) else None),
        "warnings": [
            "No colour image in this file: hand detection cannot run on it.",
            "No distortion coefficients stored: pinhole model used without correction.",
            "Depth units are not stated in the file." + ("" if args.units_confirmed else " UNCONFIRMED."),
            "Hand region not identified; all accepted pixels (hand and bed) are shown.",
        ],
        "timings_s": {k: round(v, 4) for k, v in timings.items()},
        "timing_note": "measured on this PC for this one file; not a real-time claim",
    }
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(json.dumps({k: report[k] for k in ("pixel_status_counts", "points", "valid_xyz_range_mm",
                                            "unit_evidence", "timings_s")}, indent=2))
    print("Wrote:", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
