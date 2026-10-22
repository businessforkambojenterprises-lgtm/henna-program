"""Follow the hand between two scans (Studio v29 Quick re-scan, user 2026-10-22: "it takes too much time").

A real hand never lands in the same place twice. The Quick re-scan records a new scan and moves the accepted outline
and the design's placement WITH the hand, so the design stays on the same part of the hand: the 21 hand landmarks of
the old and the new scan give a rigid 2-D motion (rotation + shift, no scale, no mirror - Kabsch), fitted twice:
  in PIXELS            for the outline (it is drawn on the picture)
  in MACHINE X/Y (mm)  for the design's centre and rotation (they are machine values) - each landmark's machine X/Y
                       from its pixel, its measured depth and the calibration (tool_XY = head_XY + R @ cam_xy + t)
The heights still come only from the NEW scan: Studio checks the design and writes the program on it. When the hand
changed its pose (fingers spread or curled: the rigid fit leaves more than MAX_RMS_MM), moved more than
MAX_SHIFT_MM or turned more than MAX_TURN_DEG, the follow is refused - do the steps by hand then.
"""
import math

import numpy as np

MAX_RMS_MM = 6.0
MAX_SHIFT_MM = 40.0
MAX_TURN_DEG = 25.0
MIN_POINTS = 6


class FollowError(ValueError):
    pass


def fit(src, dst):
    """Rigid 2-D motion src -> dst (Kabsch, no scale, no reflection) -> {R, t, angle_deg, rms, n}."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if len(src) < 2 or len(src) != len(dst):
        raise FollowError("too few matching hand points")
    cs, cd = src.mean(0), dst.mean(0)
    H = (src - cs).T @ (dst - cd)
    U, _, Vt = np.linalg.svd(H)
    D = np.diag([1.0, np.sign(np.linalg.det(Vt.T @ U.T))])
    R = Vt.T @ D @ U.T
    t = cd - R @ cs
    res = np.hypot(*((src @ R.T + t) - dst).T)
    return {"R": R, "t": t, "angle_deg": math.degrees(math.atan2(R[1, 0], R[0, 0])),
            "rms": float(np.sqrt(np.mean(res ** 2))), "n": len(src)}


def apply(points, f):
    return np.asarray(points, float) @ f["R"].T + f["t"]


def landmarks(report, head, R, t):
    """{name: (pixel (u, v), machine (x, y))} for the landmarks of the first hand with a measured depth."""
    import pyrealsense2 as rs
    from henna_preview.region import to_rs_intrinsics
    intr = to_rs_intrinsics(report["colour_intrinsics"])
    hand = (report.get("hands") or [None])[0]
    if not hand:
        raise FollowError("no hand in the scan")
    out = {}
    R, t = np.asarray(R, float), np.asarray(t, float)
    for lm in hand["landmarks"]:
        d = lm.get("measured_depth_mm")
        if d is None:
            continue
        p = rs.rs2_deproject_pixel_to_point(intr, [float(lm["u_px"]), float(lm["v_px"])], float(d))
        xy = np.asarray(head[:2], float) + R @ np.array([p[0], p[1]]) + t
        out[lm["name"]] = ((float(lm["u_px"]), float(lm["v_px"])), (float(xy[0]), float(xy[1])))
    return out


def follow(old, new):
    """old / new: landmarks() dicts -> {"pixels": fit, "machine": fit}; FollowError when the hand cannot be followed."""
    names = [n for n in old if n in new]
    if len(names) < MIN_POINTS:
        raise FollowError(f"only {len(names)} hand points measured in both scans - do the steps by hand")
    px = fit([old[n][0] for n in names], [new[n][0] for n in names])
    mm = fit([old[n][1] for n in names], [new[n][1] for n in names])
    shift = float(np.hypot(*(apply([np.mean([old[n][1] for n in names], 0)], mm)[0]
                            - np.mean([old[n][1] for n in names], 0))))
    if mm["rms"] > MAX_RMS_MM:
        raise FollowError(f"the hand changed its shape between the scans (fingers moved: {mm['rms']:.1f} mm) - keep "
                          "it flat and still, or do the steps by hand")
    if shift > MAX_SHIFT_MM or abs(mm["angle_deg"]) > MAX_TURN_DEG:
        raise FollowError(f"the hand moved too far ({shift:.0f} mm, {mm['angle_deg']:+.0f} deg) - put it back "
                          "near its place, or do the steps by hand")
    return {"pixels": px, "machine": mm, "shift_mm": round(shift, 1), "turn_deg": round(mm["angle_deg"], 1)}
