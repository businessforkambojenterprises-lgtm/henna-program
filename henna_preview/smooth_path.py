"""Smooth nozzle path (step 16): exact planned X/Y + smoothed measured skin Z, floor checked on RAW skin.

Why: snapping every point to a camera pixel made a 0.4 mm staircase with ~30 deg corners
every 0.5 mm, so GRBL slowed at each corner (stutter) and the shape looked rough.

Rules:
  * X/Y are the PLANNED design points (exact curve). Each must have measured skin within
    max_miss_mm of it, inside the accepted region, or the path breaks there (never bridged).
  * skin Z per point comes from the measured pixel under it (with the depth-scale correction).
  * nozzle Z = max(smoothed_skin + standoff, raw_skin + floor_margin)  -> never below the floor.
  * points are thinned to ~out_spacing_mm only where the straight line between kept points
    stays >= raw_skin + floor_margin at every dropped point.
"""
import numpy as np

from henna_preview.calibration import CalibrationError
from henna_preview.region import deproject_mm
from henna_preview.zcal import camera_xy_for_tool, find_pixel


def measure_under_plan(plan_xy, frame, region_mask, head, xy_cal, max_miss_mm=0.5):
    """Per planned point: ('ok', camera xyz) or (reason, None). Only measured depth is used."""
    R, t = xy_cal["R"], xy_cal["t_mm"]
    raw, scale, intr = frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    out = []
    for xy in plan_xy:
        cam = camera_xy_for_tool(xy, [head["x"], head["y"]], R, t)
        try:
            loc = find_pixel(frame, cam)
        except CalibrationError:
            out.append(("missing_depth", None)); continue
        u, v = loc["pixel_uv"]
        if loc["xy_miss_mm"] > max_miss_mm:
            out.append(("missing_depth", None)); continue
        if not region_mask[v, u]:
            out.append(("outside_region", None)); continue
        out.append(("ok", deproject_mm([u], [v], [raw[v, u] * scale], intr)[0]))
    return out


FOOTPRINT_MM = 2.5
FOOTPRINT_HEAD_MM = 25.0       # nearer than the point's own skin by this much = the head / nozzle, not skin
FOOTPRINT_PERCENTILE = 10      # of the depth round the point (the highest single pixel lifted ~1.4 mm on noise)
FOOTPRINT_MIN_PX = 8


def measure_with_footprint(plan_xy, frame, region_mask, head, xy_cal, max_miss_mm=0.5, radius_mm=FOOTPRINT_MM):
    """measure_under_plan, but each 'ok' point's depth is the NEAREST measured skin (the highest) within radius_mm
    of it in camera X/Y - inside the region or not (user 2026-10-27, video: curled fingertips rising right beside the
    tilted nozzle touched it at 3 mm; "build 3"). The point's status and X/Y stay those of the point itself, so what
    is drawable does not change - only how high the nozzle stays above it."""
    from henna_preview.zcal import _cloud
    out = measure_under_plan(plan_xy, frame, region_mask, head, xy_cal, max_miss_mm)
    raw, scale, intr = frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    cloud = _cloud(raw, scale, intr)
    h, w = raw.shape
    R, t = xy_cal["R"], xy_cal["t_mm"]
    for i, (status, p) in enumerate(out):
        if status != "ok":
            continue
        cam = camera_xy_for_tool(plan_xy[i], [head["x"], head["y"]], R, t)
        z = float(p[2])
        u = int(round(intr["fx"] * cam[0] / z + intr["ppx"]))
        v = int(round(intr["fy"] * cam[1] / z + intr["ppy"]))
        r_px = int(np.ceil(radius_mm * max(intr["fx"], intr["fy"]) / z)) + 1
        win = cloud[max(0, v - r_px):min(h, v + r_px + 1), max(0, u - r_px):min(w, u + r_px + 1)].reshape(-1, 3)
        win = win[np.isfinite(win).all(axis=1)]
        near = win[(np.hypot(win[:, 0] - cam[0], win[:, 1] - cam[1]) <= radius_mm)
                   & (win[:, 2] > z - FOOTPRINT_HEAD_MM)]
        if len(near) >= FOOTPRINT_MIN_PX:                   # the high skin there, not one noisy pixel: the
            high = float(np.percentile(near[:, 2], FOOTPRINT_PERCENTILE))   # nearest 10 % of the measured depth
            out[i] = ("ok", np.array([p[0], p[1], min(z, high)]))
    return out


def runs(status, closed):
    """Index runs of consecutive 'ok' points; a fully-ok closed path is one wrapped run."""
    n = len(status)
    ok = [s == "ok" for s in status]
    if closed and all(ok):
        return [list(range(n)) + [0]], True
    out, cur = [], []
    for i in range(n):
        if ok[i]:
            cur.append(i)
        else:
            if len(cur) > 1: out.append(cur)
            cur = []
    if len(cur) > 1: out.append(cur)
    return out, False


def smooth(values, window_pts, wrap):
    v = np.asarray(values, float)
    if window_pts <= 1 or len(v) < 3:
        return v.copy()
    h = window_pts // 2
    if wrap:
        ext = np.concatenate([v[-h:], v, v[:h]])
        k = np.ones(2 * h + 1) / (2 * h + 1)
        return np.convolve(ext, k, mode="valid")
    out = np.empty_like(v)
    for i in range(len(v)):
        a, b = max(0, i - h), min(len(v), i + h + 1)
        out[i] = v[a:b].mean()
    return out


def thin(xy, z, floor, keep_every):
    """Keep every Nth point, but re-insert any point where the straight segment would dip below its floor."""
    n = len(z)
    keep = sorted(set(list(range(0, n, keep_every)) + [n - 1]))
    changed = True
    while changed:
        changed = False
        new = [keep[0]]
        for a, b in zip(keep[:-1], keep[1:]):
            bad = None
            for j in range(a + 1, b):
                f = (j - a) / (b - a)
                if z[a] + f * (z[b] - z[a]) < floor[j] - 1e-9:
                    bad = j
                    break
            if bad is not None:
                new.append(bad)
                changed = True
            new.append(b)
        keep = sorted(set(new))
    return keep


def build_smooth_strokes(plan_xy, closed, measured, head, k_mm, depth_fn, standoff, floor_margin,
                         window_mm=3.0, spacing_mm=0.5, out_spacing_mm=1.0, jump_mm=3.0):
    """-> strokes (N, 5): x, y, raw_skin_z, nozzle_z, floor_z; plus stats."""
    status = [m[0] for m in measured]
    skin = np.full(len(plan_xy), np.nan)
    for i, (s, p) in enumerate(measured):
        if s == "ok":
            d = depth_fn(p[2]) if depth_fn else p[2]
            skin[i] = head["z"] - (d - k_mm)
    rs, wrapped = runs(status, closed)
    # split runs at raw depth jumps (finger gaps / edges)
    pieces, breaks = [], 0
    for r in rs:
        cur = [r[0]]
        for i in r[1:]:
            if abs(skin[i] - skin[cur[-1]]) > jump_mm:
                breaks += 1
                if len(cur) > 1: pieces.append(cur)
                cur = []
            cur.append(i)
        if len(cur) > 1: pieces.append(cur)
    whole_loop = wrapped and breaks == 0
    w = max(1, int(round(window_mm / spacing_mm)) | 1)
    strokes, max_lift = [], 0.0
    for idx in pieces:
        core = idx[:-1] if whole_loop else idx
        raw = skin[core]
        sm = smooth(raw, w, whole_loop)
        nozzle = np.maximum(sm + standoff, raw + floor_margin)
        max_lift = max(max_lift, float((nozzle - (raw + standoff)).max()))
        floor = raw + floor_margin
        xy = plan_xy[core]
        if whole_loop:  # close the loop
            xy = np.vstack([xy, xy[:1]]); raw = np.append(raw, raw[0]); nozzle = np.append(nozzle, nozzle[0])
            floor = np.append(floor, floor[0])
        keep = thin(xy, nozzle, floor, max(1, int(round(out_spacing_mm / spacing_mm))))
        strokes.append(np.column_stack([xy[keep], raw[keep], nozzle[keep], floor[keep]]))
    if not strokes:
        raise CalibrationError("no drawable segment with measured skin")
    stats = {"planned_points": len(plan_xy), "on_measured_skin": status.count("ok"),
             "rejected_outside_region": status.count("outside_region"),
             "rejected_missing_depth": status.count("missing_depth"), "depth_jump_breaks": breaks,
             "strokes": len(strokes), "output_points": int(sum(len(s) for s in strokes)),
             "smoothing_window_mm": window_mm, "max_raise_above_standoff_due_to_floor_mm": round(max_lift, 3)}
    return strokes, stats


def corner_stats(strokes):
    angs, steps = [], []
    for s in strokes:
        d = np.diff(s[:, :2], axis=0)
        u = d / np.maximum(np.linalg.norm(d, axis=1, keepdims=True), 1e-12)
        angs.extend(np.degrees(np.arccos(np.clip((u[1:] * u[:-1]).sum(1), -1, 1))))
        steps.extend(np.abs(np.diff(s[:, 3])))
    angs, steps = np.array(angs), np.array(steps)
    return {"direction_change_deg_median": round(float(np.median(angs)), 2),
            "direction_change_deg_max": round(float(angs.max()), 2),
            "nozzle_z_step_mm_max": round(float(steps.max()), 3)}
