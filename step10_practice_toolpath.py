"""Step 10: PENCIL PRACTICE toolpath (simulation only, not validated, do not send).

Places a circle/curve on the calibration recording's frame (paper target),
projects it onto measured depth (breaks at missing depth / jumps, never bridged),
maps camera X/Y to machine X/Y with a VERIFIED pencil calibration, and writes:
  toolpath.json, practice_gcode.txt (header + invalid first line), report.json, overlay.png
Z comes only from the user's practice_params.json. No machine connection.
"""
import argparse
import datetime as dt
import glob
import json
import os

import numpy as np
from PIL import Image, ImageDraw

from henna_preview import MOTION_READY
from henna_preview.design import DesignError, centre_depth_mm, check_params as check_design, plan_samples, project
from henna_preview.practice_toolpath import (PracticeError, check_params, gcode_lines, independent_check,
                                             to_machine_strokes)
from henna_preview.recording import read_frame

ROOT = os.path.dirname(os.path.abspath(__file__))
PARAMS_FILE = os.path.join(ROOT, "practice_params.json")


def load_params():
    with open(PARAMS_FILE, encoding="utf-8") as f:
        p = json.load(f)
    check_params(p)
    return p


def find_verification(calib_path):
    """A saved verification that used this calibration, or None."""
    best = None
    for v in glob.glob(os.path.join(ROOT, "outputs", "webui", "*-verify*", "verification.json")):
        with open(v, encoding="utf-8") as f:
            r = json.load(f)
        if os.path.normcase(os.path.abspath(r.get("calibration_file", ""))) == os.path.normcase(os.path.abspath(calib_path)):
            best = (v, r)
    return best


def require_pencil_verified(calib_path, calib):
    if "pencil" not in str(calib.get("tool", "")).lower():
        raise PracticeError("practice toolpaths are only allowed with a PENCIL calibration")
    approved = load_params().get("approved_calibration")
    if not approved or os.path.normcase(os.path.abspath(os.path.join(ROOT, approved))) != os.path.normcase(os.path.abspath(calib_path)):
        raise PracticeError("only the calibration named as approved_calibration in practice_params.json may be used")
    ver = find_verification(calib_path)
    if ver is None:
        raise PracticeError("this calibration has no saved verification at a second head pose")
    return ver


def compute(calib_path, calib, frame, design, params):
    ver_path, ver = require_pencil_verified(calib_path, calib)
    check_design(design["shape"], design["size_mm"], design["rotation_deg"], design["jump_mm"])
    raw, scale, intr = frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    mask = np.ones(raw.shape, bool)  # paper target: no hand region; missing depth still breaks the path
    zc = centre_depth_mm(raw, scale, design["centre_px"], mask)
    if zc is None:
        raise DesignError("no measured depth at the design centre")
    samples, closed, _ = plan_samples(design["shape"], design["size_mm"], design["centre_px"], design["rotation_deg"],
                                      intr["fx"], zc)
    res = project(raw, scale, intr, mask, samples, closed, design["jump_mm"])
    head = calib["inputs"]["head_pose_at_capture"]
    R, t = np.asarray(calib["result"]["R"]), np.asarray(calib["result"]["t_mm"])
    strokes = to_machine_strokes([s["points_mm"] for s in res["segments"]], [head["x"], head["y"]], R, t)
    lines = gcode_lines(strokes, params)
    moves = independent_check(lines, params)
    return {"samples_px": samples.round(2).tolist(), "status": res["status"], "segments": res["segments"],
            "projection_stats": res["stats"], "strokes_machine_xy": [s.round(3).tolist() for s in strokes],
            "gcode_lines": lines, "moves": moves, "verification": {"file": ver_path, "rms_mm": ver["result"]["rms_mm"],
                                                                  "max_error_mm": ver["result"]["max_error_mm"]}}


def render(rgb, out):
    pil = Image.fromarray(rgb)
    d = ImageDraw.Draw(pil)
    s = out["samples_px"]
    d.line([tuple(p) for p in s], fill=(127, 119, 221), width=1)
    for seg in out["segments"]:
        d.line([tuple(p) for p in seg["pixels_uv"]], fill=(59, 160, 30), width=3)
    for (u, v), st in zip(s, out["status"]):
        if st != "ok":
            d.ellipse([u - 2, v - 2, u + 2, v + 2], fill=(226, 75, 74))
    d.text((8, 8), "PENCIL PRACTICE - NOT VALIDATED - DO NOT SEND", fill=(226, 75, 74))
    return pil


def save(out_dir, calib_path, calib, frame, design, params, out):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "practice_gcode.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out["gcode_lines"]) + "\n")
    with open(os.path.join(out_dir, "toolpath.json"), "w", encoding="utf-8") as f:
        json.dump({"strokes_machine_xy": out["strokes_machine_xy"], "moves": out["moves"]}, f)
    render(frame["colour_rgb"], out).save(os.path.join(out_dir, "overlay.png"))
    rep = {
        "schema": "pencil_practice_toolpath/1",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY,
        "status": "PENCIL PRACTICE - NOT VALIDATED - DO NOT SEND",
        "output_is_executable": "text only, saved as .txt, first command deliberately invalid; a sender may still continue past errors",
        "tool": calib.get("tool"),
        "calibration": os.path.abspath(calib_path), "verification": out["verification"],
        "head_pose_at_capture": calib["inputs"]["head_pose_at_capture"],
        "recording": calib["inputs"]["recording"], "frame_index": calib["inputs"]["frame_index"],
        "design": design, "projection": out["projection_stats"],
        "practice_params": params, "z_source": "user fixed draw_z (camera depth NOT converted to machine Z)",
        "lines": len(out["gcode_lines"]), "strokes": len(out["strokes_machine_xy"]),
        "independent_check": "passed: every line re-read; Z >= floor; X/Y inside travel; draw moves at draw_z; no valve/spindle commands",
        "not_validated": ["camera-to-NOZZLE offset", "machine Z from camera depth", "hand rest / working height",
                          "Z floor protects only this program, not manual jogs", "firmware Z floor (-97) is below the bed"],
    }
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--calibration", required=True)
    ap.add_argument("--shape", default="circle", choices=["circle", "arc"])
    ap.add_argument("--size-mm", type=float, default=30.0)
    ap.add_argument("--centre", type=float, nargs=2, required=True, metavar=("U", "V"))
    ap.add_argument("--rotation", type=float, default=0.0)
    ap.add_argument("--jump-mm", type=float, default=3.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    calib = json.load(open(a.calibration, encoding="utf-8"))
    frame = read_frame(calib["inputs"]["recording"], calib["inputs"]["frame_index"])
    design = {"shape": a.shape, "size_mm": a.size_mm, "centre_px": list(a.centre), "rotation_deg": a.rotation, "jump_mm": a.jump_mm}
    params = load_params()
    out = compute(a.calibration, calib, frame, design, params)
    d = a.out or os.path.join("outputs", "step10", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    rep = save(d, a.calibration, calib, frame, design, params, out)
    print(json.dumps({k: rep[k] for k in ("status", "lines", "strokes", "projection")}, indent=2))
    print("Wrote:", os.path.abspath(d))


if __name__ == "__main__":
    main()
