"""Plan-view design (option A): a true circle/curve in machine X/Y, then measured skin under each point.

For each planned machine X/Y point:
  1. map it into the camera view with the inverse of the verified X/Y calibration,
  2. take the MEASURED pixel whose 3D point lies closest to it (miss must be <= max_miss_mm),
  3. reject it if that pixel is outside the accepted region or has no depth nearby.
The path then breaks at rejected points and at neighbouring depth jumps; nothing is bridged.
Each kept point is a measured camera-frame point (x, y, z in mm); its machine X/Y may differ
from the plan by at most max_miss_mm.
"""
import math

import numpy as np

from henna_preview.calibration import CalibrationError, predict
from henna_preview.zcal import camera_xy_for_tool, find_pixel
from henna_preview.region import deproject_mm

SHAPES = {"circle": 360.0, "arc": 240.0}


class PlanError(ValueError):
    """Raised for invalid plan-view design parameters."""


def plan_points(shape, diameter_mm, centre_xy, rotation_deg=0.0, spacing_mm=0.5):
    if shape not in SHAPES:
        raise PlanError("shape must be circle or arc")
    if not 1.0 <= diameter_mm <= 100.0:
        raise PlanError("diameter must be 1..100 mm")
    r = diameter_mm / 2.0
    span = math.radians(SHAPES[shape])
    n = max(24, int(math.ceil(span * r / spacing_mm)))
    closed = shape == "circle"
    a = math.radians(rotation_deg) + span * np.arange(n + (0 if closed else 1)) / n
    return np.column_stack([centre_xy[0] + r * np.cos(a), centre_xy[1] + r * np.sin(a)]), closed


def project_plan(plan_xy, closed, frame, region_mask, head, xy_cal, jump_mm=3.0, max_miss_mm=0.5):
    R, t = xy_cal["R"], xy_cal["t_mm"]
    raw, scale, intr = frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    status, pts = [], []
    for xy in plan_xy:
        cam = camera_xy_for_tool(xy, [head["x"], head["y"]], R, t)
        try:
            loc = find_pixel(frame, cam)
        except CalibrationError:
            status.append("missing_depth"); pts.append(None); continue
        u, v = loc["pixel_uv"]
        if loc["xy_miss_mm"] > max_miss_mm:
            status.append("missing_depth"); pts.append(None); continue
        if not region_mask[v, u]:
            status.append("outside_region"); pts.append(None); continue
        p = deproject_mm([u], [v], [raw[v, u] * scale], intr)[0]
        status.append("ok"); pts.append(p)
    order = list(range(len(plan_xy))) + ([0] if closed else [])
    segs, cur, breaks, prev = [], [], [], None
    for i in order:
        p = pts[i]
        if p is None:
            if len(cur) > 1: segs.append(cur)
            cur, prev = [], None; continue
        if prev is not None and abs(p[2] - prev[2]) > jump_mm:
            breaks.append(i)
            if len(cur) > 1: segs.append(cur)
            cur = []
        cur.append(p); prev = p
    if len(cur) > 1: segs.append(cur)
    segs = [np.array(s) for s in segs]
    kept = [predict(np.array([p[:2]]), [head["x"], head["y"]], np.asarray(R), np.asarray(t))[0]
            for p in pts if p is not None]
    miss = [float(np.hypot(*(k - q))) for k, q in zip(kept, [plan_xy[i] for i, p in enumerate(pts) if p is not None])]
    return {"segments_cam_xyz": segs, "status": status, "breaks": breaks,
            "stats": {"planned_points": len(plan_xy), "on_measured_depth": status.count("ok"),
                      "rejected_outside_region": status.count("outside_region"),
                      "rejected_missing_depth": status.count("missing_depth"),
                      "depth_jump_breaks": len(breaks), "segments": len(segs),
                      "max_plan_to_measured_xy_mm": round(max(miss), 3) if miss else None,
                      "points_interpolated": 0}}
