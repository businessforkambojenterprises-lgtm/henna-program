"""Step 8: camera-to-tool X/Y calibration from clicked dots (practice run: PENCIL).

Inputs: a recording + frame where all target dots are visible, the head pose
at capture, the tool's hovered X/Y over each dot (targets JSON), and the
pixel you clicked for each dot. Writes calibration.json + calib_overlay.png.
Read-only on the recording. No machine connection. motion_ready=false.

Example:
    .venv\\Scripts\\python step08_calibrate.py --recording %USERPROFILE%\\Documents\\20261003_121952.db3 ^
        --targets validation\\S2026-10-03-A\\pencil_targets.json --clicks my_clicks.json
"""
import argparse
import datetime as dt
import hashlib
import json
import os

import numpy as np
from PIL import Image, ImageDraw

from henna_preview import MOTION_READY
from henna_preview.calibration import CalibrationError, dot_camera_mm, solve
from henna_preview.recording import frame_pair_count, read_frame


def load_targets(path):
    with open(path, encoding="utf-8") as f:
        t = json.load(f)
    if "targets" not in t or "head_pose_at_capture" not in t:
        raise CalibrationError("targets file needs 'targets' and 'head_pose_at_capture'")
    return t


def calibrate(frame, targets, clicks):
    """frame: dict from read_frame. clicks: {label: [u, v]}. Returns the full result."""
    raw, scale, intr = frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    head = targets["head_pose_at_capture"]
    dots, used = [], []
    for label, uv in sorted(clicks.items()):
        if label not in targets["targets"]:
            raise CalibrationError(f"'{label}' is not in the targets file")
        p, n = dot_camera_mm(raw, scale, intr, uv)
        d = {"label": label, "pixel_uv": [round(float(uv[0]), 1), round(float(uv[1]), 1)],
             "machine_xy": targets["targets"][label], "depth_pixels_used": n}
        if p is None:
            d["status"] = "rejected: no measured depth near the click"
        else:
            d["camera_mm"] = [round(float(c), 2) for c in p]
            d["status"] = "used"
            used.append(d)
        dots.append(d)
    if len(used) < 3:
        raise CalibrationError("fewer than 3 clicked dots have measured depth")
    r = solve([d["camera_mm"][:2] for d in used], [d["machine_xy"] for d in used], [head["x"], head["y"]])
    for d, res in zip(used, r["residuals"]):
        d["residual_mm"] = [round(float(res[0]), 2), round(float(res[1]), 2)]
        d["residual_len_mm"] = round(float(np.hypot(*res)), 2)
    zs = [d["camera_mm"][2] for d in used]
    return {"dots": dots, "R": r["R"].round(6).tolist(), "t_mm": [round(float(v), 2) for v in r["t"]],
            "rotation_deg": round(r["rotation_deg"], 2), "mirrored": r["mirrored"],
            "rms_mm": round(r["rms_mm"], 2), "max_residual_mm": round(r["max_residual_mm"], 2),
            "scale_check": round(r["scale_check"], 4), "dots_used": len(used),
            "target_depth_mm_range": [round(min(zs), 1), round(max(zs), 1)]}


def render(rgb, result):
    pil = Image.fromarray(rgb)
    d = ImageDraw.Draw(pil)
    for dot in result["dots"]:
        u, v = dot["pixel_uv"]
        ok = dot["status"] == "used"
        col = (59, 160, 30) if ok else (226, 75, 74)
        d.ellipse([u - 6, v - 6, u + 6, v + 6], outline=col, width=2)
        txt = dot["label"] + (f" {dot['residual_len_mm']:.1f}mm" if ok else " no depth")
        d.text((u + 8, v - 14), txt, fill=col)
    return pil


def build_record(recording, frame_index, frame, targets, targets_path, clicks, result):
    h = hashlib.sha256()
    with open(recording, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    head = targets["head_pose_at_capture"]
    return {
        "schema": "camera_tool_xy_calibration/1",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY,
        "gcode": None,
        "tool": targets.get("tool", "unknown"),
        "session_id": targets.get("session_id"),
        "axes": "X/Y only (Z excluded)",
        "valid_only_for": f"this tool, head Z {head['z']} at capture, this camera mount and bed; re-measure after any change",
        "model": "tool_XY = head_XY_at_capture + R @ camera_xy_mm + t",
        "inputs": {"recording": os.path.abspath(recording), "recording_sha256": h.hexdigest(),
                   "frame_index": frame_index, "frame": frame["frame"], "targets_file": os.path.abspath(targets_path),
                   "head_pose_at_capture": head, "clicks_px": clicks},
        "camera_intrinsics_colour": frame["colour_intrinsics"],
        "result": result,
        "how_to_read": {
            "t_mm": "X/Y offset (mm) added to the head position; with R it maps a camera point to the tool X/Y over it",
            "rotation_deg": "camera X/Y axes rotated relative to machine X/Y",
            "mirrored": "true if camera axes are flipped relative to machine axes",
            "rms_mm / max_residual_mm": "how far the fitted model misses your hovered points; includes hover aim and click error",
            "scale_check": "should be close to 1.0; far from 1 means camera mm and machine mm disagree",
        },
        "warnings": [
            "PENCIL calibration practice run. Not the nozzle. Not machine-ready.",
            "One head pose only: the offset is fitted, not independently verified. A second recording at another head pose is needed to verify it.",
            "Camera tilt is not modelled; valid only near the target height used here.",
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--recording", required=True)
    ap.add_argument("--frame", type=int)
    ap.add_argument("--targets", required=True)
    ap.add_argument("--clicks", required=True, help='JSON {"K0": [u, v], ...}')
    ap.add_argument("--out")
    a = ap.parse_args()
    targets = load_targets(a.targets)
    clicks = json.load(open(a.clicks, encoding="utf-8"))
    n = frame_pair_count(a.recording)
    fi = a.frame if a.frame is not None else (n // 2 if n else 0)
    frame = read_frame(a.recording, fi)
    result = calibrate(frame, targets, clicks)
    out = a.out or os.path.join("outputs", "step08", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    rec = build_record(a.recording, fi, frame, targets, a.targets, clicks, result)
    json.dump(rec, open(os.path.join(out, "calibration.json"), "w", encoding="utf-8"), indent=2)
    render(frame["colour_rgb"], result).save(os.path.join(out, "calib_overlay.png"))
    print(json.dumps({k: result[k] for k in ("t_mm", "rotation_deg", "mirrored", "rms_mm", "max_residual_mm", "scale_check")}, indent=2))
    print("Wrote:", os.path.abspath(out))


if __name__ == "__main__":
    main()
