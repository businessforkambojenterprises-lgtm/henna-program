"""Step 5: review the usable hand region on one detected frame.

Reads a step-4 hand-detection run folder. Writes only into --out.
A region is saved with accepted=false unless --accept is given; later steps
refuse unaccepted regions. No camera, controller or G-code. motion_ready=false.

Example (suggest, review the picture, then accept):
    .venv\\Scripts\\python step05_region.py --run-dir outputs\\step04\\first_run
    .venv\\Scripts\\python step05_region.py --run-dir outputs\\step04\\first_run --accept
"""
import argparse
import base64
import datetime as dt
import io
import json
import os
import time

import numpy as np
from PIL import Image, ImageDraw

from henna_preview.region import evaluate_region, load_bundle, region_record, suggest_region

DEFAULT_MARGIN = 0.15  # suggestion only; the user adjusts or redraws


def render_overlay(rgb, mask, missing, polygon):
    """Colour image, outside region dimmed, missing depth inside region magenta, outline drawn."""
    img = rgb.astype(np.float32)
    img[~mask] *= 0.45
    img[missing] = 0.4 * img[missing] + 0.6 * np.array([255, 0, 255])
    pil = Image.fromarray(img.clip(0, 255).astype(np.uint8))
    d = ImageDraw.Draw(pil)
    pts = [tuple(p) for p in polygon]
    d.line(pts + [pts[0]], fill=(24, 95, 165), width=3)
    for x, y in pts:
        d.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(255, 255, 255), outline=(24, 95, 165), width=2)
    return pil


def holes_png_data_url(raw):
    a = np.zeros(raw.shape + (4,), np.uint8)
    a[raw == 0] = [255, 0, 255, 150]
    return png_data_url(Image.fromarray(a, "RGBA"))


def png_data_url(pil):
    buf = io.BytesIO()
    pil.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def save_region_outputs(out, bundle, polygon, margin, edited, accepted):
    t0 = time.perf_counter()
    res = evaluate_region(bundle, polygon)
    t_eval = time.perf_counter() - t0
    os.makedirs(out, exist_ok=True)
    rec = region_record(bundle, polygon, margin, res["stats"], accepted, edited)
    rec["created"] = dt.datetime.now().isoformat(timespec="seconds")
    rec["points"] = {"measured": int(len(res["points_mm"])), "interpolated": 0, "planned": 0}
    rec["warnings"] = [
        "Region outline is a user decision; landmarks only suggested it.",
        "Pixels without measured depth inside the outline are excluded, not filled.",
        "Points are colour-camera millimetres, not machine coordinates. No head pose is applied.",
    ] + ([] if accepted else ["NOT ACCEPTED: later steps will refuse this region."])
    rec["timings_s"] = {"evaluate_and_deproject": round(t_eval, 4)}
    render_overlay(bundle["colour_rgb"], res["mask"], res["missing"], polygon).save(os.path.join(out, "region_overlay.png"))
    np.savez_compressed(os.path.join(out, "region_points_camera_mm.npz"),
                        points_mm=res["points_mm"].astype(np.float32), pixels_uv=res["pixels_uv"].astype(np.int32))
    with open(os.path.join(out, "region.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    return rec, res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, help="a step-4 output folder (has frame_bundle.npz)")
    ap.add_argument("--margin", type=float, default=DEFAULT_MARGIN, help="pull the suggestion inward (0..0.95)")
    ap.add_argument("--polygon", help="JSON file with your own [[u, v], ...] outline instead of the suggestion")
    ap.add_argument("--accept", action="store_true", help="mark this region as reviewed and accepted by you")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    bundle = load_bundle(args.run_dir)
    if not bundle["hand_detected"] or len(bundle["landmarks_px"]) != 21:
        if not args.polygon:
            raise SystemExit("no hand landmarks in this run: pass --polygon with your own outline")
    if args.polygon:
        with open(args.polygon, encoding="utf-8") as f:
            polygon, edited = json.load(f), True
    else:
        polygon, edited = suggest_region(bundle["landmarks_px"], args.margin), False
    out = args.out or os.path.join("outputs", "step05", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
    rec, _ = save_region_outputs(out, bundle, polygon, args.margin, edited, args.accept)
    print(json.dumps({k: rec[k] for k in ("accepted", "stats", "points", "timings_s")}, indent=2))
    print("Wrote:", os.path.abspath(out))


if __name__ == "__main__":
    main()
