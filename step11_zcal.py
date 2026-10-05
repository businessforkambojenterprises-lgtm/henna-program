"""Step 11: Z calibration (camera depth -> machine Z of the tool tip).

Inputs: a verified X/Y calibration, Z-cal recording A (+ optional B at another head Z),
head poses at capture, and tool touch readings on a flat block. Read-only on
recordings. No machine connection. motion_ready=false.

Example: .venv\\Scripts\\python step11_zcal.py --config validation\\S2026-10-05-A\\zcal_inputs.json
"""
import argparse
import datetime as dt
import json
import os

from PIL import Image, ImageDraw

from henna_preview import MOTION_READY
from henna_preview.recording import frame_pair_count, read_frame
from henna_preview.zcal import solve_k


def run(cfg):
    calib = json.load(open(cfg["xy_calibration"], encoding="utf-8"))
    a, b = cfg["recording_a"], cfg.get("recording_b")
    fa = read_frame(a["path"], a.get("frame", frame_pair_count(a["path"]) // 2))
    fb = read_frame(b["path"], b.get("frame", frame_pair_count(b["path"]) // 2)) if b else None
    res = solve_k(cfg["touches"], fa, a["head"], calib["result"], fb, b["head"] if b else None)
    return calib, fa, res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    calib, fa, res = run(cfg)
    out = a.out or os.path.join("outputs", "step11", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    os.makedirs(out, exist_ok=True)
    pil = Image.fromarray(fa["colour_rgb"])
    d = ImageDraw.Draw(pil)
    for r in res["touches"]:
        u, v = r["pixel_uv"]
        d.ellipse([u - 6, v - 6, u + 6, v + 6], outline=(59, 160, 30), width=2)
        d.text((u + 8, v - 14), f"{r['label']} k={r['k_mm']}", fill=(59, 160, 30))
    pil.save(os.path.join(out, "zcal_overlay.png"))
    rep = {
        "schema": "camera_tool_z_calibration/1",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY, "gcode": None,
        "tool": calib.get("tool"),
        "model": "Z_contact = Z_capture - (camera_depth_mm - k_mm)",
        "xy_calibration": os.path.abspath(cfg["xy_calibration"]),
        "inputs": cfg, "result": res,
        "valid_only_for": "this tool, camera mount and capture orientation; depth measured along the camera axis",
        "warnings": ["Touch spots located through the X/Y calibration (about 2 mm X/Y uncertainty).",
                     "Camera tilt is not modelled; z_scale_check near 1.0 indicates it is small.",
                     "Not machine-ready."],
    }
    json.dump(rep, open(os.path.join(out, "z_calibration.json"), "w", encoding="utf-8"), indent=2)
    print(json.dumps({k: res[k] for k in res if k != "touches"}, indent=2))
    for r in res["touches"]:
        print(r["label"], {k: r.get(k) for k in ("pixel_uv", "xy_miss_mm", "depth_a_mm", "k_mm", "z_contact",
                                                  "predicted_z_contact", "z_residual_mm", "depth_change_mm")})
    print("Wrote:", os.path.abspath(out))


if __name__ == "__main__":
    main()
