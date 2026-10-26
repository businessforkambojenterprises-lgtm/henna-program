"""User-reviewed hand region on one recorded frame.

The landmarks only *suggest* an outline. A region becomes usable for later
steps only after the user accepts it, and only for the exact recording +
frame it was drawn on. Pixels without measured depth inside the outline are
excluded, never filled.

Frames: polygon vertices are colour-image pixels (u right, v down).
3D points are colour-camera frame millimetres from the RealSense SDK's own
deprojection with the colour lens model. Not machine coordinates.
"""
import json
import os

import numpy as np
from matplotlib.path import Path

# Palm outline seeds: wrist, thumb base, index/middle/ring/pinky knuckles.
PALM_LANDMARKS = (0, 1, 5, 9, 13, 17)


class RegionError(ValueError):
    """Raised for invalid, unaccepted or mismatched regions."""


def convex_hull(points):
    """Monotone-chain convex hull, counter-clockwise in image coordinates."""
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) < 3:
        return [list(p) for p in pts]

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return [list(p) for p in lower[:-1] + upper[:-1]]


def suggest_region(landmarks_px, margin_frac):
    """Palm hull pulled toward its centre by margin_frac (0 = hull itself). A suggestion only."""
    if not 0.0 <= margin_frac < 0.95:
        raise RegionError("margin must be between 0 and 0.95")
    lm = np.asarray(landmarks_px, dtype=float)
    if lm.shape != (21, 2):
        raise RegionError("need 21 landmarks to suggest a region")
    hull = np.array(convex_hull(lm[list(PALM_LANDMARKS)]))
    c = hull.mean(axis=0)
    return (c + (1.0 - margin_frac) * (hull - c)).round(1).tolist()


def check_polygon(polygon, width, height):
    p = np.asarray(polygon, dtype=float)
    if p.ndim != 2 or p.shape[1] != 2 or len(p) < 3:
        raise RegionError("an outline needs at least 3 corner points")
    if len(p) > 200:
        raise RegionError("too many corner points (max 200)")
    if not np.isfinite(p).all():
        raise RegionError("outline has invalid coordinates")
    if (p[:, 0] < 0).any() or (p[:, 1] < 0).any() or (p[:, 0] > width).any() or (p[:, 1] > height).any():
        raise RegionError("outline must stay inside the image")
    x, y = p[:, 0], p[:, 1]
    if abs(0.5 * np.dot(x, np.roll(y, 1)) - 0.5 * np.dot(y, np.roll(x, 1))) < 1.0:
        raise RegionError("outline has no area")
    return p


def polygon_mask(polygon, width, height):
    """Boolean mask of pixels whose centres lie inside the outline."""
    p = check_polygon(polygon, width, height)
    v, u = np.mgrid[0:height, 0:width]
    inside = Path(p).contains_points(np.column_stack([u.ravel(), v.ravel()]))
    return inside.reshape(height, width)


def to_rs_intrinsics(d):
    import pyrealsense2 as rs
    i = rs.intrinsics()
    i.width, i.height = int(d["width"]), int(d["height"])
    i.fx, i.fy, i.ppx, i.ppy = float(d["fx"]), float(d["fy"]), float(d["ppx"]), float(d["ppy"])
    i.model = getattr(rs.distortion, d["model"])
    i.coeffs = [float(c) for c in d["coeffs"]]
    return i


def _deproject_sdk(us, vs, depth_m, intr_dict):
    import pyrealsense2 as rs
    intr = to_rs_intrinsics(intr_dict)
    out = np.empty((len(us), 3))
    for k, (u, v, z) in enumerate(zip(us, vs, depth_m)):
        out[k] = rs.rs2_deproject_pixel_to_point(intr, [float(u), float(v)], float(z))
    return out


def _deproject_numpy(us, vs, depth_m, intr_dict):
    """librealsense's rs2_deproject_pixel_to_point for 'none' and 'inverse_brown_conrady' (its 10 iterations), on
    whole arrays. -> (N, 3) metres, or None for another lens model."""
    model = str(intr_dict.get("model", "none")).split(".")[-1]
    if model not in ("none", "inverse_brown_conrady"):
        return None
    c = [float(v) for v in intr_dict.get("coeffs") or [0.0] * 5]
    x = (np.asarray(us, float) - float(intr_dict["ppx"])) / float(intr_dict["fx"])
    y = (np.asarray(vs, float) - float(intr_dict["ppy"])) / float(intr_dict["fy"])
    if model == "inverse_brown_conrady":
        xo, yo = x, y
        for _ in range(10):
            r2 = x * x + y * y
            icdist = 1.0 / (1.0 + ((c[4] * r2 + c[1]) * r2 + c[0]) * r2)
            xq, yq = x / icdist, y / icdist
            dx = 2 * c[2] * xq * yq + c[3] * (r2 + 2 * xq * xq)
            dy = 2 * c[3] * xq * yq + c[2] * (r2 + 2 * yq * yq)
            x, y = (xo - dx) * icdist, (yo - dy) * icdist
    z = np.asarray(depth_m, float)
    return np.column_stack([x * z, y * z, z])


def _deproject_one(u, v, z, intr_dict):
    """The same formula for one pixel in plain floats (a numpy call per pixel costs more than the maths)."""
    model = str(intr_dict.get("model", "none")).split(".")[-1]
    c = [float(q) for q in intr_dict.get("coeffs") or [0.0] * 5]
    x = (float(u) - float(intr_dict["ppx"])) / float(intr_dict["fx"])
    y = (float(v) - float(intr_dict["ppy"])) / float(intr_dict["fy"])
    if model == "inverse_brown_conrady":
        xo, yo = x, y
        for _ in range(10):
            r2 = x * x + y * y
            icdist = 1.0 / (1.0 + ((c[4] * r2 + c[1]) * r2 + c[0]) * r2)
            xq, yq = x / icdist, y / icdist
            dx = 2 * c[2] * xq * yq + c[3] * (r2 + 2 * xq * xq)
            dy = 2 * c[3] * xq * yq + c[2] * (r2 + 2 * yq * yq)
            x, y = (xo - dx) * icdist, (yo - dy) * icdist
    z = float(z)
    return (x * z, y * z, z)


_FAST_OK = {}
SMALL = 16                    # up to this many pixels: plain floats
FAST_CHECK_MM = 1e-3          # the array version must agree with the SDK this closely, else the SDK is used


def _lens_key(intr_dict):
    return (str(intr_dict.get("model")), float(intr_dict["fx"]), float(intr_dict["fy"]), float(intr_dict["ppx"]),
            float(intr_dict["ppy"]), tuple(float(q) for q in intr_dict.get("coeffs") or ()),
            intr_dict.get("width"), intr_dict.get("height"))


def _fast_ok(intr_dict):
    """Checked once per lens: the array version against the SDK on pixels across the whole picture."""
    key = _lens_key(intr_dict)
    if key not in _FAST_OK:
        w, h = float(intr_dict.get("width", 848)), float(intr_dict.get("height", 480))
        g = np.linspace(0.0, 1.0, 7)
        us, vs = np.meshgrid(g * (w - 1), g * (h - 1))
        z = np.linspace(0.08, 0.5, us.size)
        fast = _deproject_numpy(us.ravel(), vs.ravel(), z, intr_dict)
        one = None if fast is None else np.array([_deproject_one(a, b, c, intr_dict)
                                                  for a, b, c in zip(us.ravel(), vs.ravel(), z)])
        try:
            sdk = _deproject_sdk(us.ravel(), vs.ravel(), z, intr_dict)
            _FAST_OK[key] = fast is not None and max(float(np.abs(fast - sdk).max()),
                                                     float(np.abs(one - sdk).max())) * 1000.0 <= FAST_CHECK_MM
        except ImportError:
            _FAST_OK[key] = fast is not None    # no SDK on this computer: the published formula
    return _FAST_OK[key]


def deproject_mm(us, vs, depth_m, intr_dict):
    """SDK deprojection (includes the stated lens model). Returns (N, 3) millimetres. On whole arrays when the lens
    model allows it and that version agreed with the SDK for this lens (2026-10-26: one pixel at a time through the
    SDK was ~13 s of the 16 s program step)."""
    if len(us) and _fast_ok(intr_dict):
        if len(us) <= SMALL:
            return np.array([_deproject_one(u, v, z, intr_dict) for u, v, z in zip(us, vs, depth_m)]) * 1000.0
        return _deproject_numpy(us, vs, depth_m, intr_dict) * 1000.0
    return _deproject_sdk(us, vs, depth_m, intr_dict) * 1000.0


def load_bundle(run_dir):
    """Load a step-4 frame bundle plus the identity of the frame it came from."""
    b = os.path.join(run_dir, "frame_bundle.npz")
    r = os.path.join(run_dir, "report.json")
    if not (os.path.isfile(b) and os.path.isfile(r)):
        raise RegionError("not a hand-detection run (frame_bundle.npz / report.json missing)")
    with np.load(b, allow_pickle=False) as z:
        bundle = {k: z[k] for k in z.files}
    with open(r, encoding="utf-8") as f:
        rep = json.load(f)
    bundle["colour_intrinsics"] = json.loads(str(bundle.pop("colour_intrinsics_json")))
    bundle["depth_scale_m_per_unit"] = float(bundle["depth_scale_m_per_unit"])
    bundle["source"] = {"recording": rep["input"]["path"], "recording_sha256": rep["input"]["sha256"],
                        "frame_index": rep["input"]["frame_index"], "hand_run": os.path.abspath(run_dir)}
    bundle["hand_detected"] = rep.get("hand_detected", False)
    return bundle


def evaluate_region(bundle, polygon, max_points=None):
    """Measured points inside the outline. Missing depth is counted and excluded."""
    raw = bundle["depth_raw_aligned"]
    h, w = raw.shape
    mask = polygon_mask(polygon, w, h)
    measured = mask & (raw > 0)
    missing = mask & (raw == 0)
    vs, us = np.nonzero(measured)
    if max_points and len(us) > max_points:
        step = int(np.ceil(len(us) / max_points))
        us, vs = us[::step], vs[::step]
    else:
        step = 1
    z_m = raw[vs, us].astype(np.float64) * bundle["depth_scale_m_per_unit"]
    pts = deproject_mm(us, vs, z_m, bundle["colour_intrinsics"]) if len(us) else np.zeros((0, 3))
    zmm = raw[measured].astype(np.float64) * bundle["depth_scale_m_per_unit"] * 1000.0
    stats = {
        "inside_px": int(mask.sum()),
        "measured_px": int(measured.sum()),
        "missing_px_excluded": int(missing.sum()),
        "missing_pct": round(100.0 * missing.sum() / max(1, mask.sum()), 2),
        "depth_mm_range": [round(float(zmm.min()), 1), round(float(zmm.max()), 1)] if len(zmm) else None,
        "points_every_nth": step,
    }
    return {"mask": mask, "missing": missing, "points_mm": pts, "pixels_uv": np.column_stack([us, vs]), "stats": stats}


def region_record(bundle, polygon, margin_frac, stats, accepted, edited):
    return {
        "schema": "reviewed_region/1",
        "accepted": bool(accepted),
        "motion_ready": False,
        "source": bundle["source"],
        "polygon_px": [[round(float(x), 1), round(float(y), 1)] for x, y in polygon],
        "polygon_frame": "colour image pixels, u right, v down, origin top-left",
        "suggestion": {"from_landmarks": [int(i) for i in PALM_LANDMARKS], "margin_frac": margin_frac,
                       "edited_by_user": bool(edited)},
        "stats": stats,
        "points_frame": "colour camera frame (SDK deprojection, colour lens model), millimetres; not machine coordinates",
        "missing_depth_policy": "excluded, never filled",
    }


def require_accepted(record, recording_sha256, frame_index):
    """Later steps call this before using a region."""
    if not record.get("accepted"):
        raise RegionError("region has not been accepted by the user")
    src = record.get("source", {})
    if src.get("recording_sha256") != recording_sha256 or src.get("frame_index") != frame_index:
        raise RegionError("region was drawn on a different recording or frame")
    return record
