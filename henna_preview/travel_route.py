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


# ---- jumps between strokes (user 2026-10-24: "make the pen's jumps go around the tall mound instead of straight over
# it" -> "build the detour now"). A jump that crosses skin too tall for the travel height (the heel of the palm, the
# thumb mound) goes round it at travel Z: the L-shapes above, or a corner beside the straight line (round either side of
# a mound in the middle of the jump), or a jump beside it parallel to it. Only a jump that is too tall changes - every
# other jump stays straight (and keeps its low hop). The path check measures the chosen legs, as for the first move.
SIDE_OFFSETS_MM = (8.0, 15.0, 25.0, 40.0)


def around_candidates(start, target, bounds=None):
    """candidates() plus routes past either side of the straight line: one corner beside its middle, or two corners
    beside its ends (a parallel leg). bounds = (x_max, y_max): waypoints outside 0..max are dropped."""
    out = candidates(start, target)
    s, t = np.asarray(start[:2], float), np.asarray(target[:2], float)
    v = t - s
    length = float(np.hypot(*v))
    if length < 1.0:
        return out
    n = np.array([-v[1], v[0]]) / length
    mid = (s + t) / 2.0
    for d in SIDE_OFFSETS_MM:
        for sign in (1.0, -1.0):
            off = sign * d * n
            out.append([tuple(mid + off)])
            out.append([tuple(s + off), tuple(t + off)])
    if bounds is not None:
        xm, ym = float(bounds[0]), float(bounds[1])
        out = [w for w in out if all(0.0 <= p[0] <= xm and 0.0 <= p[1] <= ym for p in w)]
    return [[(round(float(p[0]), 3), round(float(p[1]), 3)) for p in w] for w in out]


def route_length(start, waypoints, target):
    return float(sum(np.hypot(b[0] - a[0], b[1] - a[1]) for a, b in legs(start, waypoints, target)))


def choose_gap(start, target, xy, z, margin, top_limit, keep=None, bounds=None):
    """-> (waypoints, top_z) for one jump between strokes. Straight ([]) while its corridor top is at or below
    top_limit (or unmeasured: the path check decides). Too tall: the SHORTEST candidate whose top is at or below the
    limit; if none is, the lowest one when it is at least MIN_GAIN_MM lower than straight (the check then refuses with
    the smaller number), else straight."""
    straight = top_under(xy, z, legs(start, [], target), margin, keep)
    if straight is None or straight <= top_limit:
        return [], straight
    rated = []
    for w in around_candidates(start, target, bounds)[1:]:
        t = top_under(xy, z, legs(start, w, target), margin, keep)
        if t is not None:
            rated.append((w, t, route_length(start, w, target)))
    fits = [r for r in rated if r[1] <= top_limit]
    if fits:
        best = min(fits, key=lambda r: r[2])
        return best[0], best[1]
    if rated:
        best = min(rated, key=lambda r: (r[1], r[2]))
        if best[1] <= straight - MIN_GAIN_MM:
            return best[0], best[1]
    return [], straight


def program_gaps(lines, travel_z):
    """The jumps between strokes a program text routes round tall skin: [(from_xy, [waypoints], to_xy)] for every rise
    to travel Z after a drawing move that is followed by two or more G0 X Y lines (the last one is the next stroke's
    start). The first move (program_approach) and plain jumps are not listed."""
    import re
    g0z = re.compile(r"^G53 G0 Z(-?\d+\.\d+)$")
    g0xy = re.compile(r"^G53 G0 X(\d+\.\d+) Y(\d+\.\d+)$")
    g1 = re.compile(r"^G53 G1 X(\d+\.\d+) Y(\d+\.\d+) Z")
    out, last, i = [], None, 0
    while i < len(lines):
        m = g1.match(lines[i])
        if m:
            last = (float(m.group(1)), float(m.group(2)))
            i += 1
            continue
        m = g0z.match(lines[i])
        if m and last is not None and abs(float(m.group(1)) - float(travel_z)) < 1e-6:
            chain, j = [], i + 1
            while j < len(lines):
                mm = g0xy.match(lines[j])
                if not mm:
                    break
                chain.append((float(mm.group(1)), float(mm.group(2))))
                j += 1
            if len(chain) >= 2:
                out.append((last, chain[:-1], chain[-1]))
            i = j
            continue
        i += 1
    return out


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
