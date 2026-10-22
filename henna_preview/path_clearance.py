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
  - a patch without depth on it that has no measured edge, or is larger than MAX_HOLE_MM2.
A patch without depth TAKES THE HEIGHT OF ITS EDGE (user 2026-10-18 scan: a 561 mm2 hole on plain bed refused a good
design): anything standing in a hole shows at the hole's rim - its sides have texture - so each hole on the path
counts as high as the 95th percentile of the measured ring around it. Plain bed around a hole = bed height.
Small scattered holes (plain paper, hand edges: 5-10 % on a normal scan, checked 2026-10-18) are normal.
HEAD_FOOTPRINT_MM around the scan position is left out (points and holes): there the camera sees the nozzle and the
dispenser body, and the surface under them is hidden - found on the first real scans. The tip already stands there at
travel height while the scan is taken, so anything taller in that circle would be touching the nozzle before any move.
The machine head's own pixels (head_mask) are left out, as everywhere; anything nearer than the tip plane that is not
the head stays IN (the caller's check refuses a top above the tip as "something between the camera and the hand").

TRAVEL LINES (user 2026-10-22, "do b"): when the design's strokes are known, the corridor is the nozzle's actual moves
at travel height instead of the convex hull - the straight line from where the run starts (the scan position, and the
head's current position when it is known) to the first stroke's start, and from each stroke's end to the next stroke's
start (a hop goes to travel height when its skin is not measured) - each grown by MARGIN_MM. The strokes themselves
are drawn at skin + nozzle height and checked point by point by the design check; after the last stroke the nozzle
only rises. A tall wrist or thumb BESIDE those lines no longer refuses the design; on them it still does.
"""
import numpy as np

MARGIN_MM = 8.0
TOP_PERCENTILE = 99.5
MIN_POINTS = 50
MAX_BLIND_SHARE = 0.25
MAX_HOLE_MM2 = 5000.0                # about 70 x 70 mm - larger is refused
MIN_HOLE_MM2 = 25.0                  # smaller holes: the 99.5th percentile of the measured points covers them
RIM_PX, MIN_RIM_POINTS = 3, 8
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


def _near_segment(xy, a, b, margin):
    a, b = np.asarray(a, float), np.asarray(b, float)
    ab = b - a
    L2 = float(ab @ ab)
    u = np.zeros(len(xy)) if L2 == 0 else np.clip(((xy - a) @ ab) / L2, 0, 1)
    return np.hypot(*(xy - (a + u[:, None] * ab)).T) <= margin


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
        near |= _near_segment(xy, a, b, margin)
    return inside | near


def travel_segments(starts, strokes, approach=()):
    """The nozzle's moves at travel height, in program order: from each possible start XY through the approach
    waypoints (henna_preview/travel_route.py) to the first stroke's start, then from each stroke's end to the next
    stroke's start. strokes: [(start_xy, end_xy)]."""
    if not strokes:
        return []
    way = [tuple(map(float, w)) for w in approach] + [tuple(map(float, strokes[0][0]))]
    segs = [(tuple(map(float, s[:2])), way[0]) for s in starts]
    segs += list(zip(way[:-1], way[1:]))
    segs += [(tuple(map(float, strokes[i][1])), tuple(map(float, strokes[i + 1][0]))) for i in range(len(strokes) - 1)]
    return segs


def near_segments(xy, segments, margin):
    """Points within margin of any of the segments."""
    xy = np.asarray(xy, float).reshape(-1, 2)
    out = np.zeros(len(xy), bool)
    for a, b in segments:
        out |= _near_segment(xy, a, b, margin)
    return out


def hole_heights(blind_mask, incb, ok_mask, z, mm_per_px):
    """[(area_mm2, rim_z, rim_points)] for every patch of unmeasured corridor pixels of MIN_HOLE_MM2 or more:
    rim_z = 95th percentile Z of the measured pixels in a RIM_PX ring around it (None when the ring has fewer than
    MIN_RIM_POINTS). z is given for the measured pixels, in the order of ok_mask."""
    import cv2
    m = np.zeros(blind_mask.shape, np.uint8)
    m[blind_mask] = incb.astype(np.uint8)
    if not m.any():
        return []
    zgrid = np.full(blind_mask.shape, np.nan)
    zgrid[ok_mask] = z
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    kern = np.ones((2 * RIM_PX + 1, 2 * RIM_PX + 1), np.uint8)
    out = []
    for i in range(1, n):
        area = float(stats[i, cv2.CC_STAT_AREA]) * mm_per_px ** 2
        if area < MIN_HOLE_MM2:
            continue
        comp = (lab == i).astype(np.uint8)
        ring = (cv2.dilate(comp, kern) > 0) & (comp == 0) & ok_mask
        zr = zgrid[ring]
        zr = zr[np.isfinite(zr)]
        out.append((area, float(np.percentile(zr, 95)) if zr.size >= MIN_RIM_POINTS else None, int(zr.size)))
    return out


def corridor_top(xy, z, xyb, region_ok, scan_xy, margin=MARGIN_MM, blind_mask=None, mm_per_px=None, ok_mask=None,
                 segments=None):
    """-> dict with the corridor's top Z and how well it was measured; raises PathClearanceError when it cannot tell.
    region_ok: for each measured point (same order as xy), whether its pixel lies in the accepted region.
    blind_mask, ok_mask (+ mm_per_px): unmeasured / measured pixels in image layout, for the hole rule.
    segments: the travel lines (travel_segments) - the corridor is then those lines + margin, not the hull."""
    region_xy = xy[region_ok]
    if len(region_xy) < 3:
        raise PathClearanceError("the design region has too little measured depth to check the nozzle's path")
    sxy = np.asarray(scan_xy, float)[:2]
    xyb = np.asarray(xyb, float).reshape(-1, 2)
    if segments:
        poly = None
        inc = near_segments(xy, segments, margin)
        incb = near_segments(xyb, segments, margin)
    else:
        poly = hull(np.vstack([region_xy, sxy[None, :]]))
        inc = in_corridor(xy, poly, margin)
        incb = in_corridor(xyb, poly, margin)
    inc &= np.hypot(*(xy - sxy).T) > HEAD_FOOTPRINT_MM
    incb &= np.hypot(*(xyb - sxy).T) > HEAD_FOOTPRINT_MM
    n, nb = int(inc.sum()), int(incb.sum())
    if n < MIN_POINTS:
        raise PathClearanceError("too little of the nozzle's path is measured by the camera - scan again")
    blind = nb / float(n + nb)
    if blind > MAX_BLIND_SHARE:
        raise PathClearanceError(f"{100 * blind:.0f} % of the nozzle's path has no depth (more than "
                                 f"{100 * MAX_BLIND_SHARE:.0f} %) - scan again with even light, nothing hanging in view")
    holes = []
    if blind_mask is not None and ok_mask is not None and mm_per_px:
        holes = hole_heights(blind_mask, incb, ok_mask, z, mm_per_px)
    for area, rim_z, _ in holes:
        if area > MAX_HOLE_MM2:
            raise PathClearanceError(f"a patch of about {area:.0f} mm2 on the nozzle's path has no depth (more than "
                                     f"{MAX_HOLE_MM2:g} mm2) - scan again with even light")
        if rim_z is None:
            raise PathClearanceError(f"a patch of about {area:.0f} mm2 on the nozzle's path has no depth and no "
                                     "measured edge - scan again with even light")
    zc = z[inc]
    top = float(np.percentile(zc, TOP_PERCENTILE))
    i = int(np.argmin(np.abs(zc - top)))
    top_xy = [round(float(v), 1) for v in xy[inc][i]]
    hole_top = max((h[1] for h in holes), default=None)
    if hole_top is not None and hole_top > top:
        top, top_xy = hole_top, None                     # a hole's edge is the highest: no single point to name
    largest = max((h[0] for h in holes), default=0.0)
    return {"top_z": round(top, 2), "top_xy": top_xy, "points": n,
            "blind_share": round(blind, 4), "largest_hole_mm2": round(largest, 1), "holes": len(holes),
            "hole_top_z": None if hole_top is None else round(hole_top, 2),
            "margin_mm": margin, "corridor_xy": None if poly is None else np.round(poly, 1).tolist(),
            "segments": [[list(map(lambda v: round(v, 1), a)), list(map(lambda v: round(v, 1), b))] for a, b in segments]
            if segments else None,
            "whole_view_top_z": round(float(np.percentile(z, TOP_PERCENTILE)), 2) if len(z) else None,
            "basis": (f"nozzle travel lines: start -> first stroke and between strokes ({len(segments)} lines), "
                      f"{margin:g} mm margin") if segments else
                     f"nozzle travel path: design region + scan position, {margin:g} mm margin"}
