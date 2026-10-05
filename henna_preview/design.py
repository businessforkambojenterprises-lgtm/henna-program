"""Place a simple design inside an accepted region and project it onto measured depth.

Designs: a circle or a short curve (240° arc). Size is given in millimetres and
converted to image pixels with the colour fx and the measured depth at the
design centre, so the size is approximate on a surface that faces the camera.
The true length along the surface is reported from the measured 3D points.

Projection never bridges: a sample is rejected if it is outside the region or
has no measured depth, and the path is broken wherever neighbouring accepted
samples differ in depth by more than jump_mm. Every 3D path point is the
measured point of the pixel nearest the planned sample; nothing is interpolated.
"""
import math

import numpy as np

from henna_preview.region import deproject_mm

SHAPES = {"circle": 360.0, "arc": 240.0}
OK, OUTSIDE, MISSING = "ok", "outside_region", "missing_depth"


class DesignError(ValueError):
    """Raised for invalid design parameters."""


def check_params(shape, size_mm, rotation_deg, jump_mm):
    if shape not in SHAPES:
        raise DesignError("shape must be 'circle' or 'arc'")
    for name, v, lo, hi in (("size_mm", size_mm, 1.0, 100.0), ("jump_mm", jump_mm, 0.1, 50.0)):
        if not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
            raise DesignError(f"{name} must be between {lo} and {hi}")
    if not isinstance(rotation_deg, (int, float)) or not math.isfinite(rotation_deg):
        raise DesignError("rotation must be a number")


def centre_depth_mm(raw, depth_scale, centre_px, region_mask, radius_px=5):
    """Median measured depth near the centre, inside the region. None if nothing is measured there."""
    h, w = raw.shape
    u, v = int(round(centre_px[0])), int(round(centre_px[1]))
    if not (0 <= u < w and 0 <= v < h):
        return None
    sl = (slice(max(0, v - radius_px), v + radius_px + 1), slice(max(0, u - radius_px), u + radius_px + 1))
    vals = raw[sl][(raw[sl] > 0) & region_mask[sl]]
    return float(np.median(vals)) * depth_scale * 1000.0 if len(vals) else None


def plan_samples(shape, size_mm, centre_px, rotation_deg, fx, centre_z_mm):
    """Planned sample positions in image pixels, about one per pixel along the curve."""
    r_px = 0.5 * size_mm * fx / centre_z_mm
    span = math.radians(SHAPES[shape])
    n = max(24, int(math.ceil(span * r_px)))
    a0 = math.radians(rotation_deg)
    closed = shape == "circle"
    t = a0 + span * np.arange(n + (0 if closed else 1)) / n
    pts = np.column_stack([centre_px[0] + r_px * np.cos(t), centre_px[1] + r_px * np.sin(t)])
    return pts, closed, r_px


def project(raw, depth_scale, intr, region_mask, samples, closed, jump_mm):
    """Classify samples and split the path into unbroken measured segments."""
    h, w = raw.shape
    uv = np.rint(samples).astype(int)
    status = []
    for u, v in uv:
        if not (0 <= u < w and 0 <= v < h) or not region_mask[v, u]:
            status.append(OUTSIDE)
        elif raw[v, u] == 0:
            status.append(MISSING)
        else:
            status.append(OK)
    idx = list(range(len(samples))) + ([0] if closed else [])
    segments, cur, breaks = [], [], []
    prev_z = None
    for k in idx:
        if status[k] != OK:
            if len(cur) > 1:
                segments.append(cur)
            cur, prev_z = [], None
            continue
        z = raw[uv[k][1], uv[k][0]] * depth_scale * 1000.0
        if prev_z is not None and abs(z - prev_z) > jump_mm:
            breaks.append(k)
            if len(cur) > 1:
                segments.append(cur)
            cur = []
        cur.append(k)
        prev_z = z
    if len(cur) > 1:
        segments.append(cur)

    seg_out = []
    for seg in segments:
        pix, last = [], None
        for k in seg:  # one 3D point per measured pixel; consecutive duplicates dropped
            p = (int(uv[k][0]), int(uv[k][1]))
            if p != last:
                pix.append(p)
                last = p
        pix = np.array(pix)
        z_m = raw[pix[:, 1], pix[:, 0]].astype(np.float64) * depth_scale
        xyz = deproject_mm(pix[:, 0], pix[:, 1], z_m, intr)
        length = float(np.linalg.norm(np.diff(xyz, axis=0), axis=1).sum()) if len(xyz) > 1 else 0.0
        seg_out.append({"sample_idx": seg, "pixels_uv": pix.tolist(), "points_mm": xyz.round(2).tolist(),
                        "length_mm": round(length, 2)})
    n_unique = len(samples)
    counts = {s: sum(1 for x in status if x == s) for s in (OK, OUTSIDE, MISSING)}
    complete = counts[OK] == n_unique and not breaks and len(seg_out) == 1
    return {
        "status": status,
        "segments": seg_out,
        "jump_breaks_at_samples": breaks,
        "stats": {
            "planned_samples": n_unique,
            "on_measured_depth": counts[OK],
            "rejected_outside_region": counts[OUTSIDE],
            "rejected_missing_depth": counts[MISSING],
            "depth_jump_breaks": len(breaks),
            "segments": len(seg_out),
            "path_length_mm": round(sum(s["length_mm"] for s in seg_out), 2),
            "path_length_note": "summed between whole measured pixels, so a smooth curve reads roughly 5-15% long",
            "path_points_measured": sum(len(s["points_mm"]) for s in seg_out),
            "path_points_interpolated": 0,
        },
        "complete_unbroken": bool(complete),
    }
