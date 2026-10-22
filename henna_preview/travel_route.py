"""Route the first travel move around tall skin (Studio v29, user 2026-10-22: "build step 2").

The program's first sideways move goes at travel Z from the scan position to the first stroke's start. On a real hand
that straight line often crosses the thumb mound or the heel of the palm, too tall for the travel height. The same
move can go round them: an L-shaped route, first along X then Y or first along Y then X. Of these candidates the one
whose corridor (the legs grown by the path check's margin) has the LOWEST measured top is taken - but the straight
line stays unless a route round is at least MIN_GAIN_MM lower, so nothing changes where it was already fine.
The route is a list of waypoints at travel Z; the program writes them as consecutive "G53 G0 X Y" lines at travel Z
(henna_preview/program_reread_v2.py allows that only at travel Z), and the path check measures exactly these legs.
Pure functions; the surface comes from the caller.
"""
import numpy as np

TOP_PERCENTILE = 99.5
MIN_GAIN_MM = 1.0


def candidates(start, target):
    """Waypoint lists from start to target: straight, X first then Y, Y first then X (duplicates dropped)."""
    sx, sy = float(start[0]), float(start[1])
    tx, ty = float(target[0]), float(target[1])
    out = [[]]
    for w in ((tx, sy), (sx, ty)):
        if np.hypot(w[0] - sx, w[1] - sy) > 0.5 and np.hypot(w[0] - tx, w[1] - ty) > 0.5:
            out.append([w])
    return out


def legs(start, waypoints, target):
    pts = [tuple(map(float, start[:2]))] + [tuple(map(float, w)) for w in waypoints] + [tuple(map(float, target[:2]))]
    return list(zip(pts[:-1], pts[1:]))


def top_under(xy, z, segments, margin, keep=None):
    """TOP_PERCENTILE of the measured Z within margin of the segments (None when no point is there)."""
    from henna_preview.path_clearance import near_segments
    m = near_segments(xy, segments, margin)
    if keep is not None:
        m &= keep
    return float(np.percentile(z[m], TOP_PERCENTILE)) if m.any() else None


def choose(start, target, xy, z, margin, keep=None):
    """-> (waypoints, top_z, [(waypoints, top_z) for every candidate]). A candidate whose corridor has no measured
    point at all is never preferred over one that has (an unmeasured route is refused by the path check anyway)."""
    rated = []
    for w in candidates(start, target):
        rated.append((w, top_under(xy, z, legs(start, w, target), margin, keep)))
    measured = [r for r in rated if r[1] is not None]
    if not measured:
        return [], None, rated
    best = min(measured, key=lambda r: r[1])              # first of equals = straight, then X-first, Y-first
    straight = rated[0]
    if straight[1] is not None and best[1] > straight[1] - MIN_GAIN_MM:
        best = straight                                   # a longer move only when it is clearly lower
    return best[0], best[1], rated


def program_approach(lines, travel_z):
    """The waypoints a program text uses for its first move: the consecutive G0 X Y lines right after its first
    rise to travel Z, without the last one (the first stroke's start)."""
    import re
    g0z = re.compile(r"^G53 G0 Z(-?\d+\.\d+)$")
    g0xy = re.compile(r"^G53 G0 X(\d+\.\d+) Y(\d+\.\d+)$")
    seen_rise, chain = False, []
    for ln in lines:
        if not seen_rise:
            m = g0z.match(ln)
            if m and abs(float(m.group(1)) - float(travel_z)) < 1e-6:
                seen_rise = True
            continue
        m = g0xy.match(ln)
        if m:
            chain.append((float(m.group(1)), float(m.group(2))))
            continue
        break
    return chain[:-1]
