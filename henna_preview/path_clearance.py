"""Travel clearance over the nozzle tip's own path (Studio v29, user 2026-10-18: "do 2").

The v27 rule checks the highest measured point of the WHOLE hand (its grown landmark outline) - on a real person that
is usually the wrist or forearm (50-65 mm), taller than the machine's room under travel Z, even when the design and
every move are elsewhere. The nozzle tip is the lowest part of the head, so only the surface the TIP crosses at travel
height matters: the accepted design region (strokes and the hops between them) and the way from the scan position to
it and back. This module measures that corridor:

    corridor = convex hull( region skin points (machine X/Y)  +  scan position X/Y ),  grown by MARGIN_MM
    corridor top = TOP_PERCENTILE of the measured surface Z inside the corridor (same model as the planner and the
                   hand watch: tool_XY = head_XY + R @ camera_xy + t, Z = head_Z - (corrected depth - k))

and refuses - never guesses - when the corridor is not measured well enough:
  - fewer than MIN_POINTS measured points in it, or
  - more than MAX_BLIND_SHARE of its pixels without depth (placed at the median depth to find where they fall), or
  - one connected patch without depth larger than MAX_HOLE_MM2 in it - big enough to hide something.
Small scattered holes (plain paper, hand edges: 5-10 % on a normal scan, checked 2026-10-18) are normal.
HEAD_FOOTPRINT_MM around the scan position is left out (points and holes): there the camera sees the nozzle and the
dispenser body, and the surface under them is hidden - found on the first real scans. The tip already stands there at
travel height while the scan is taken, so anything taller in that circle would be touching the nozzle before any move.
The machine head's own pixels (head_mask) are left out, as everywhere; anything nearer than the tip plane that is not
the head stays IN (the caller's check refuses a top above the tip as "something between the camera and the hand").
"""
import numpy as np

MARGIN_MM = 8.0
TOP_PERCENTILE = 99.5
MIN_POINTS = 50
MAX_BLIND_SHARE = 0.25
MAX_HOLE_MM2 = 300.0                 # about 17 x 17 mm
HEAD_FOOTPRINT_MM = 15.0


class PathClearanceError(ValueError):
    pass


def surface(raw, scale, rays, head, R, t, k, depth_fn, keep):
    """Machine X/Y and surface Z of the measured pixels where keep is True, and X/Y of the unmeasured ones (at the
    median measured depth). raw, rays and keep have the same (sub-sampled) shape."""
    d = raw.astype(float) * float(scale) * 1000.0
    ok = keep & (raw > 0)
    blind = keep & (raw == 0)
    head = np.asarray(head, float)
    R, t = np.asarray(R, float), np.asarray(t, float)
    dm = d[ok]
    dc = np.asarray(depth_fn(dm), float)
    cam = np.column_stack([rays[0][ok] * dm, rays[1][ok] * dm])
    xy = head[:2] + cam @ R.T + t
    z = head[2] - (dc - k)
    med = float(np.median(dm)) if dm.size else 0.0
    camb = np.column_stack([rays[0][blind] * med, rays[1][blind] * med])
    xyb = head[:2] + camb @ R.T + t
    return xy, z, xyb, ok


def hull(points):
    from henna_preview.region import convex_hull
    return np.asarray(convex_hull([tuple(p) for p in np.asarray(points, float)]), float)


def in_corridor(xy, poly, margin):
    """Points inside the polygon or within margin of its edges."""
    from matplotlib.path import Path
    xy = np.asarray(xy, float)
    if not len(xy):
        return np.zeros(0, bool)
    inside = Path(poly).contains_points(xy) if len(poly) >= 3 else np.zeros(len(xy), bool)
    near = np.zeros(len(xy), bool)
    pts = list(poly) + [poly[0]] if len(poly) >= 2 else list(poly) * 2
    for a, b in zip(pts[:-1], pts[1:]):
        a, b = np.asarray(a, float), np.asarray(b, float)
        ab = b - a
        L2 = float(ab @ ab)
        u = np.zeros(len(xy)) if L2 == 0 else np.clip(((xy - a) @ ab) / L2, 0, 1)
        dist = np.hypot(*(xy - (a + u[:, None] * ab)).T)
        near |= dist <= margin
    return inside | near


def largest_hole_mm2(blind_mask, incb, mm_per_px):
    """Area of the largest connected patch of unmeasured pixels that lie in the corridor."""
    import cv2
    m = np.zeros(blind_mask.shape, np.uint8)
    m[blind_mask] = incb.astype(np.uint8)
    if not m.any():
        return 0.0
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    return float(stats[1:, cv2.CC_STAT_AREA].max()) * mm_per_px ** 2


def corridor_top(xy, z, xyb, region_ok, scan_xy, margin=MARGIN_MM, blind_mask=None, mm_per_px=None):
    """-> dict with the corridor's top Z and how well it was measured; raises PathClearanceError when it cannot tell.
    region_ok: for each measured point (same order as xy), whether its pixel lies in the accepted region.
    blind_mask (+ mm_per_px): the unmeasured pixels in image layout, for the largest-hole rule."""
    region_xy = xy[region_ok]
    if len(region_xy) < 3:
        raise PathClearanceError("the design region has too little measured depth to check the nozzle's path")
    poly = hull(np.vstack([region_xy, np.asarray(scan_xy, float)[None, :2]]))
    sxy = np.asarray(scan_xy, float)[:2]
    inc = in_corridor(xy, poly, margin) & (np.hypot(*(xy - sxy).T) > HEAD_FOOTPRINT_MM)
    incb = in_corridor(xyb, poly, margin) & (np.hypot(*(np.asarray(xyb, float).reshape(-1, 2) - sxy).T) > HEAD_FOOTPRINT_MM)
    n, nb = int(inc.sum()), int(incb.sum())
    if n < MIN_POINTS:
        raise PathClearanceError("too little of the nozzle's path is measured by the camera - scan again")
    blind = nb / float(n + nb)
    if blind > MAX_BLIND_SHARE:
        raise PathClearanceError(f"{100 * blind:.0f} % of the nozzle's path has no depth (more than "
                                 f"{100 * MAX_BLIND_SHARE:.0f} %) - scan again with even light, nothing hanging in view")
    hole = largest_hole_mm2(blind_mask, incb, mm_per_px) if blind_mask is not None and mm_per_px else None
    if hole is not None and hole > MAX_HOLE_MM2:
        raise PathClearanceError(f"a patch of about {hole:.0f} mm2 on the nozzle's path has no depth (more than "
                                 f"{MAX_HOLE_MM2:g} mm2 - it could hide something) - scan again with even light")
    zc = z[inc]
    top = float(np.percentile(zc, TOP_PERCENTILE))
    i = int(np.argmin(np.abs(zc - top)))
    return {"top_z": round(top, 2), "top_xy": [round(float(v), 1) for v in xy[inc][i]], "points": n,
            "blind_share": round(blind, 4), "largest_hole_mm2": None if hole is None else round(hole, 1),
            "margin_mm": margin, "corridor_xy": np.round(poly, 1).tolist(),
            "whole_view_top_z": round(float(np.percentile(z, TOP_PERCENTILE)), 2) if len(z) else None,
            "basis": f"nozzle travel path: design region + scan position, {margin:g} mm margin"}
