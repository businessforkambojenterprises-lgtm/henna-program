"""Step 9: verify a saved camera-to-tool X/Y calibration at a different head pose.

The calibration is NOT re-fitted. Read-only on the recording. No machine
connection. motion_ready=false.

Example:
    .venv\\Scripts\\python step09_verify.py --calibration outputs\\webui\\<time>-calib\\calibration.json ^
        --recording %USERPROFILE%\\Documents\\20261003_123351.db3 ^
        --targets validation\\S2026-10-03-A\\pencil_targets_pose2.json --clicks pose2_clicks.json
"""
import argparse
import datetime as dt
import hashlib
import json
import os

from PIL import Image, ImageDraw

from henna_preview import MOTION_READY
from henna_preview.recording import frame_pair_count, read_frame
from henna_preview.verify import verify
from step08_calibrate import load_targets


def render(rgb, result):
    pil = Image.fromarray(rgb)
    d = ImageDraw.Draw(pil)
    for dot in result["dots"]:
        u, v = dot["pixel_uv"]
        ok = dot["status"] == "used"
        col = (59, 160, 30) if ok else (226, 75, 74)
        d.ellipse([u - 6, v - 6, u + 6, v + 6], outline=col, width=2)
        d.text((u + 8, v - 14), dot["label"] + (f" {dot['error_mm']:.1f}mm" if ok else " no depth"), fill=col)
    return pil


def build_record(calib_path, calib, recording, frame_index, frame, targets, targets_path, clicks, result):
    h = hashlib.sha256()
    with open(recording, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return {
        "schema": "camera_tool_xy_verification/1",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY,
        "gcode": None,
        "tool": targets.get("tool"),
        "axes": "X/Y only (Z excluded)",
        "calibration_file": os.path.abspath(calib_path),
        "calibration_head_pose": calib["inputs"]["head_pose_at_capture"],
        "verification_head_pose": targets["head_pose_at_capture"],
        "inputs": {"recording": os.path.abspath(recording), "recording_sha256": h.hexdigest(),
                   "frame_index": frame_index, "frame": frame["frame"], "targets_file": os.path.abspath(targets_path),
                   "clicks_px": clicks},
        "result": result,
        "how_to_read": "error = position predicted from the saved calibration minus the hovered tool position; "
                       "compare rms_mm with the calibration's own fit RMS. Not re-fitted.",
        "warnings": ["PENCIL practice run. Not the nozzle. Not machine-ready.",
                     "Verified at the same head height (Z) only; camera tilt is not modelled."],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calibration", required=True)
    ap.add_argument("--recording", required=True)
    ap.add_argument("--frame", type=int)
    ap.add_argument("--targets", required=True)
    ap.add_argument("--clicks", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    calib = json.load(open(a.calibration, encoding="utf-8"))
    targets = load_targets(a.targets)
    clicks = json.load(open(a.clicks, encoding="utf-8"))
    n = frame_pair_count(a.recording)
    fi = a.frame if a.frame is not None else (n // 2 if n else 0)
    frame = read_frame(a.recording, fi)
    result = verify(frame, targets, clicks, calib)
    out = a.out or os.path.join("outputs", "step09", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    rec = build_record(a.calibration, calib, a.recording, fi, frame, targets, a.targets, clicks, result)
    json.dump(rec, open(os.path.join(out, "verification.json"), "w", encoding="utf-8"), indent=2)
    render(frame["colour_rgb"], result).save(os.path.join(out, "verify_overlay.png"))
    print(json.dumps({k: result[k] for k in ("dots_used", "rms_mm", "max_error_mm", "mean_error_xy_mm", "calibration_fit_rms_mm")}, indent=2))
    print("Wrote:", os.path.abspath(out))


if __name__ == "__main__":
    main()
