"""Step 6: place a simple design in an ACCEPTED region and project it onto measured depth.

Reads an accepted region folder (region.json) and its hand-detection frame.
Writes only into --out. Refuses unaccepted or mismatched regions.
No camera, controller or G-code. motion_ready is always false.

Example:
    .venv\\Scripts\\python step06_design.py --region-dir outputs\\webui\\<time>-region --shape circle --size-mm 20
"""
import argparse
import datetime as dt
import json
import math
import os
import time

import numpy as np
from PIL import Image, ImageDraw

from henna_preview import MOTION_READY
from henna_preview.design import (MISSING, OUTSIDE, SHAPES, DesignError, centre_depth_mm, check_params,
                                  plan_samples, project)
from henna_preview.region import load_bundle, polygon_mask, require_accepted

DEFAULT_JUMP_MM = 3.0  # borrowed from the earlier pipeline's jump_mm; review it


def load_accepted_region(region_dir):
    with open(os.path.join(region_dir, "region.json"), encoding="utf-8") as f:
        rec = json.load(f)
    bundle = load_bundle(rec["source"]["hand_run"])
    require_accepted(rec, bundle["source"]["recording_sha256"], bundle["source"]["frame_index"])
    h, w = bundle["depth_raw_aligned"].shape
    mask = polygon_mask(rec["polygon_px"], w, h)
    return rec, bundle, mask


def region_centroid(polygon):
    p = np.asarray(polygon, float)
    return [round(float(p[:, 0].mean()), 1), round(float(p[:, 1].mean()), 1)]


def compute(bundle, mask, shape, size_mm, centre_px, rotation_deg, jump_mm):
    check_params(shape, size_mm, rotation_deg, jump_mm)
    t0 = time.perf_counter()
    raw, scale, intr = bundle["depth_raw_aligned"], bundle["depth_scale_m_per_unit"], bundle["colour_intrinsics"]
    zc = centre_depth_mm(raw, scale, centre_px, mask)
    if zc is None:
        raise DesignError("no measured depth inside the region at the design centre; move the design")
    samples, closed, r_px = plan_samples(shape, size_mm, centre_px, rotation_deg, intr["fx"], zc)
    res = project(raw, scale, intr, mask, samples, closed, jump_mm)
    res["planned"] = {
        "shape": shape, "size_mm": size_mm, "centre_px": [float(c) for c in centre_px], "rotation_deg": rotation_deg,
        "centre_depth_mm_measured": round(zc, 1), "radius_px": round(r_px, 2), "closed": closed,
        "samples_px": np.round(samples, 2).tolist(),
        "planned_length_mm_flat_estimate": round(0.5 * size_mm * math.radians(SHAPES[shape]), 2),
    }
    res["timings_s"] = {"plan_and_project": round(time.perf_counter() - t0, 4)}
    return res


def render(rgb, mask, polygon, res):
    img = rgb.astype(np.float32)
    img[~mask] *= 0.45
    pil = Image.fromarray(img.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil)
    pts = [tuple(p) for p in polygon]
    d.line(pts + [pts[0]], fill=(24, 95, 165), width=2)
    s = res["planned"]["samples_px"]
    for k, (u, v) in enumerate(s):  # planned curve, thin purple
        u2, v2 = s[(k + 1) % len(s)]
        if k + 1 < len(s) or res["planned"]["closed"]:
            d.line([(u, v), (u2, v2)], fill=(127, 119, 221), width=1)
    for seg in res["segments"]:  # accepted measured segments, green
        d.line([tuple(p) for p in seg["pixels_uv"]], fill=(59, 160, 30), width=3)
    for (u, v), st in zip(s, res["status"]):
        if st in (OUTSIDE, MISSING):
            d.ellipse([u - 2.5, v - 2.5, u + 2.5, v + 2.5], fill=(226, 75, 74))
    for k in res["jump_breaks_at_samples"]:
        u, v = s[k]
        d.ellipse([u - 6, v - 6, u + 6, v + 6], outline=(226, 75, 74), width=2)
    return pil


def report(rec, bundle, res, jump_mm):
    st = res["stats"]
    warnings = []
    if not res["complete_unbroken"]:
        warnings.append("Path is NOT complete: it is broken or has rejected samples. See red marks.")
    if st["rejected_missing_depth"]:
        warnings.append(f"{st['rejected_missing_depth']} planned samples fall on missing depth (not bridged).")
    if st["rejected_outside_region"]:
        warnings.append(f"{st['rejected_outside_region']} planned samples fall outside the accepted region.")
    if st["depth_jump_breaks"]:
        warnings.append(f"{st['depth_jump_breaks']} breaks where neighbouring depth jumps more than {jump_mm} mm.")
    warnings += [
        "Design size is converted with the measured depth at its centre; it is approximate on a tilted or curved surface.",
        "Path points are measured pixels nearest the planned curve; nothing is interpolated.",
        "Camera-frame millimetres only. No camera-to-nozzle, head pose or machine mapping is applied.",
    ]
    return {
        "schema": "design_preview/1",
        "created": dt.datetime.now().isoformat(timespec="seconds"),
        "motion_ready": MOTION_READY,
        "gcode": None,
        "inputs": {"recording": bundle["source"]["recording"], "recording_sha256": bundle["source"]["recording_sha256"],
                   "frame_index": bundle["source"]["frame_index"], "hand_run": bundle["source"]["hand_run"],
                   "region_accepted": rec["accepted"], "region_created": rec.get("created")},
        "units": {"path_points": "mm", "image": "px", "depth_scale_m_per_unit": bundle["depth_scale_m_per_unit"]},
        "coordinate_frames": {"pixels": "colour image, u right, v down",
                              "points_mm": "colour camera frame (SDK deprojection, colour lens model)",
                              "machine": None},
        "camera_intrinsics": bundle["colour_intrinsics"],
        "params": {**{k: res["planned"][k] for k in ("shape", "size_mm", "centre_px", "rotation_deg")},
                   "jump_mm": jump_mm, "jump_mm_source": "borrowed from earlier pipeline unless changed"},
        "planned": {k: res["planned"][k] for k in ("centre_depth_mm_measured", "radius_px", "closed",
                                                   "planned_length_mm_flat_estimate")},
        "validity": {"complete_unbroken": res["complete_unbroken"], **st},
        "segments": [{"length_mm": s["length_mm"], "measured_points": len(s["points_mm"])} for s in res["segments"]],
        "warnings": warnings,
        "timings_s": res["timings_s"],
        "timing_note": "measured on this PC for one design; not a real-time claim",
    }


def save(out, rec, bundle, mask, res, jump_mm):
    os.makedirs(out, exist_ok=True)
    render(bundle["colour_rgb"], mask, rec["polygon_px"], res).save(os.path.join(out, "design_overlay.png"))
    rep = report(rec, bundle, res, jump_mm)
    np.savez_compressed(os.path.join(out, "design_path.npz"),
                        planned_samples_px=np.array(res["planned"]["samples_px"]),
                        sample_status=np.array(res["status"]),
                        **{f"segment_{i}_measured_points_mm": np.array(s["points_mm"]) for i, s in enumerate(res["segments"])},
                        **{f"segment_{i}_pixels_uv": np.array(s["pixels_uv"]) for i, s in enumerate(res["segments"])})
    with open(os.path.join(out, "design_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    return rep


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region-dir", required=True)
    ap.add_argument("--shape", choices=sorted(SHAPES), default="circle")
    ap.add_argument("--size-mm", type=float, default=15.0, help="circle/arc diameter in mm (approximate)")
    ap.add_argument("--centre", type=float, nargs=2, metavar=("U", "V"), help="image pixels; default region centre")
    ap.add_argument("--rotation", type=float, default=0.0, help="degrees")
    ap.add_argument("--jump-mm", type=float, default=DEFAULT_JUMP_MM)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    rec, bundle, mask = load_accepted_region(args.region_dir)
    centre = args.centre or region_centroid(rec["polygon_px"])
    res = compute(bundle, mask, args.shape, args.size_mm, centre, args.rotation, args.jump_mm)
    out = args.out or os.path.join("outputs", "step06", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    rep = save(out, rec, bundle, mask, res, args.jump_mm)
    print(json.dumps({"validity": rep["validity"], "warnings": rep["warnings"][:4]}, indent=2))
    print("Wrote:", os.path.abspath(out))


if __name__ == "__main__":
    main()
