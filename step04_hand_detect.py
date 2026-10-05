"""Step 4: load one frame from a RealSense recording and detect the hand.

Read-only on the recording. Writes only into --out (default outputs/step04/<time>).
No live camera, no controller, no G-code. motion_ready is always false.

Example:
    .venv\\Scripts\\python step04_hand_detect.py --input "%USERPROFILE%\\Documents\\20261001_083523.db3"
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

from henna_preview import MOTION_READY
from henna_preview.depth_surface import raw_to_mm
from henna_preview.hand_detect import (CONNECTIONS, LANDMARK_NAMES, MODEL_PATH, detect_hands,
                                       measured_depth_at, verify_model)
from henna_preview.recording import RecordingError, check_frame_index, frame_pair_count, read_frame


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def save_overlay(path, rgb, depth_mm, hands, title):
    h, w = rgb.shape[:2]
    fig = plt.figure(figsize=(w / 80, h / 80 + 0.5), dpi=80)
    ax = fig.add_axes([0, 0, 1, h / (h + 40)])
    ax.imshow(rgb)
    holes = np.zeros((h, w, 4))
    holes[~(depth_mm > 0)] = [1, 0, 1, 0.45]  # magenta = no measured depth
    ax.imshow(holes)
    for hand in hands:
        p = np.array(hand["landmarks_px"])
        for a, b in CONNECTIONS:
            ax.plot(p[[a, b], 0], p[[a, b], 1], color="#1D9E75", lw=2)
        ok = np.array([z is not None for z in hand["measured_depth_mm"]])
        ax.scatter(p[ok, 0], p[ok, 1], s=28, c="#085041", edgecolors="white", linewidths=0.8, zorder=3)
        ax.scatter(p[~ok, 0], p[~ok, 1], s=60, c="#E24B4A", marker="x", linewidths=2, zorder=3)
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.axis("off")
    fig.text(0.01, 0.99, title, va="top", fontsize=9)
    fig.text(0.01, 0.995 - 18 / (h + 40), "teal = landmarks on measured depth · red x = landmark on missing depth · "
             "magenta = no depth · motion_ready=false", va="top", fontsize=8)
    fig.savefig(path)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help=".bag or .db3 recording")
    ap.add_argument("--frame", type=int, default=None, help="0-based colour+depth pair (default: middle of a .db3)")
    ap.add_argument("--min-confidence", type=float, default=0.5,
                    help="MediaPipe detection/presence threshold (0.5 is MediaPipe's default)")
    ap.add_argument("--model", default=MODEL_PATH)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    t0 = time.perf_counter()
    timings = {}
    src = os.path.abspath(args.input)
    model_sha = verify_model(args.model)
    count = frame_pair_count(src)
    frame = args.frame
    if frame is None:
        if count is None:
            raise RecordingError("pass --frame for .bag recordings")
        frame = count // 2
    check_frame_index(frame, count)
    if not 0.0 < args.min_confidence <= 1.0:
        raise ValueError("--min-confidence must be between 0 and 1")
    out = args.out or os.path.join("outputs", "step04", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    timings["checks_s"] = time.perf_counter() - t0

    fr = read_frame(src, frame)
    timings["read_and_align_s"] = fr["read_time_s"]
    rgb, raw = fr["colour_rgb"], fr["depth_raw_aligned"]
    depth_mm = raw_to_mm(raw, fr["depth_scale_m_per_unit"])
    depth_mm[raw == 0] = np.nan  # 0 = no measurement; never filled

    t1 = time.perf_counter()
    hands = detect_hands(rgb, args.model, num_hands=1, min_confidence=args.min_confidence)
    timings["detect_s"] = time.perf_counter() - t1
    for hand in hands:
        hand["measured_depth_mm"] = measured_depth_at(hand["landmarks_px"], depth_mm)

    t2 = time.perf_counter()
    title = (f"{os.path.basename(src)} · frame {frame}"
             + (f" · hand found ({hands[0]['handedness_score']:.2f})" if hands else " · NO HAND FOUND"))
    plt.imsave(os.path.join(out, "colour.png"), rgb)
    save_overlay(os.path.join(out, "overlay.png"), rgb, depth_mm, hands, title)
    np.savez_compressed(
        os.path.join(out, "frame_bundle.npz"),
        colour_rgb=rgb, depth_raw_aligned=raw, depth_scale_m_per_unit=fr["depth_scale_m_per_unit"],
        colour_intrinsics_json=json.dumps(fr["colour_intrinsics"]),
        landmarks_px=np.array(hands[0]["landmarks_px"]) if hands else np.zeros((0, 2)))
    timings["write_s"] = time.perf_counter() - t2
    timings["total_s"] = time.perf_counter() - t0

    missing_lm = [LANDMARK_NAMES[i] for h in hands for i, z in enumerate(h["measured_depth_mm"]) if z is None]
    valid = np.isfinite(depth_mm)
    warnings = [
        "Landmarks are image-space guides from a pretrained model, not the hand boundary.",
        "World landmarks are model estimates, not measured skin height or machine coordinates.",
        "Handedness label assumes a mirrored (selfie) image and may be swapped for this camera; it is not used.",
        "Viewer post-processing state is not proven from the file; frames are treated as raw.",
        "Aligned depth uses the colour camera's lens model; 3D conversion must use those intrinsics (next step).",
    ]
    if not hands:
        warnings.insert(0, "No hand detected in this frame.")
    if missing_lm:
        warnings.insert(0, "Landmarks on missing depth: " + ", ".join(missing_lm))

    report = {
        "step": "04 load recording frame + hand detection (read-only)",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY,
        "input": {"path": src, "sha256": sha256(src), "frame_index": frame, "frame_pairs_in_file": count,
                  **fr["frame"], "streams": fr["streams"], "device": fr["device"]},
        "model": {"file": os.path.abspath(args.model), "sha256": model_sha, "type": "MediaPipe Hand Landmarker (pretrained)",
                  "min_confidence": args.min_confidence},
        "units": {"depth_raw": "Z16", "depth_scale_m_per_unit": fr["depth_scale_m_per_unit"],
                  "depth_mm": "raw * depth_scale * 1000", "units_source": "stated in recording"},
        "coordinate_frames": {
            "landmarks_px": "colour image pixels, u right, v down, origin top-left",
            "depth": "aligned to the colour image grid by the RealSense SDK",
            "machine": None,
        },
        "colour_intrinsics": fr["colour_intrinsics"],
        "depth_intrinsics_before_alignment": fr["depth_intrinsics_before_alignment"],
        "extrinsics_depth_to_colour": fr["extrinsics_depth_to_colour"],
        "depth_valid_fraction": round(float(valid.mean()), 4),
        "hand_detected": bool(hands),
        "hands": [{
            "handedness_label_unverified": h["handedness_label"],
            "score": round(h["handedness_score"], 4),
            "landmarks": [{"name": n, "u_px": round(p[0], 1), "v_px": round(p[1], 1),
                           "measured_depth_mm": (round(z, 1) if z is not None else None)}
                          for n, p, z in zip(LANDMARK_NAMES, h["landmarks_px"], h["measured_depth_mm"])],
            "world_landmarks_model_estimate_m": h["world_landmarks_model_estimate_m"],
        } for h in hands],
        "points": {"measured": "depth values at landmark pixels only", "interpolated": 0, "planned": 0},
        "warnings": warnings,
        "timings_s": {k: round(v, 4) for k, v in timings.items()},
        "timing_note": "measured on this PC for one frame; includes playing the file up to this frame; not a real-time claim",
    }
    with open(os.path.join(out, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({"hand_detected": report["hand_detected"], "frame": frame,
                      "score": report["hands"][0]["score"] if hands else None,
                      "landmarks_on_missing_depth": missing_lm, "depth_valid_fraction": report["depth_valid_fraction"],
                      "timings_s": report["timings_s"]}, indent=2))
    print("Wrote:", os.path.abspath(out))


if __name__ == "__main__":
    main()
