"""Z calibration: camera depth -> machine Z where the tool tip touches that surface.

The camera rides on the head, so for a surface point measured at camera depth d
(mm, along the camera's optical axis) while the head is at Z_cap:
    Z_contact = Z_cap - (d - k)
k (mm) is the camera-to-tool-tip height this calibration measures.
Each touch gives k = d - (Z_cap - Z_contact). Spread between touches shows reliability.

Touch spots are found automatically: the verified X/Y calibration maps each touch's
machine X/Y back into the camera view; only MEASURED depth is used there.
"""
import numpy as np

from henna_preview.calibration import CalibrationError, dot_camera_mm
from henna_preview.region import deproject_mm


def camera_xy_for_tool(tool_xy, head_xy, R, t):
    """Inverse of tool = head + R cam + t (R is orthogonal, possibly mirrored)."""
    R = np.asarray(R, float)
    return np.linalg.solve(R, np.asarray(tool_xy, float) - np.asarray(head_xy, float) - np.asarray(t, float))


def find_pixel(frame, cam_xy, search=8, start_z=200.0):
    """Pixel whose measured 3D point lies closest (in camera X/Y) to cam_xy."""
    raw, scale, intr = frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    h, w = raw.shape
    z = start_z
    for _ in range(4):  # pinhole guess, refined with measured depth near the guess
        u = intr["fx"] * cam_xy[0] / z + intr["ppx"]
        v = intr["fy"] * cam_xy[1] / z + intr["ppy"]
        p, n = dot_camera_mm(raw, scale, intr, (u, v), window=3)
        if p is None:
            raise CalibrationError("no measured depth where the touch spot should be")
        z = p[2]
    u0, v0 = int(round(u)), int(round(v))
    vs, us = np.mgrid[max(0, v0 - search):min(h, v0 + search + 1), max(0, u0 - search):min(w, u0 + search + 1)]
    us, vs = us.ravel(), vs.ravel()
    ok = raw[vs, us] > 0
    us, vs = us[ok], vs[ok]
    if not len(us):
        raise CalibrationError("no measured depth near the touch spot")
    pts = deproject_mm(us, vs, raw[vs, us].astype(float) * scale, intr)
    d = np.hypot(pts[:, 0] - cam_xy[0], pts[:, 1] - cam_xy[1])
    i = int(np.argmin(d))
    return {"pixel_uv": [int(us[i]), int(vs[i])], "xy_miss_mm": round(float(d[i]), 2)}


def depth_at(frame, uv, window=2):
    p, n = dot_camera_mm(frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"],
                         uv, window=window)
    if p is None:
        raise CalibrationError("no measured depth at the touch spot")
    return float(p[2]), n


def solve_k(touches, frame_a, head_a, xy_cal, frame_b=None, head_b=None):
    """touches: [{'label','x','y','z_contact'}]; head_*: {'x','y','z'}; xy_cal: {'R','t_mm'}."""
    if len(touches) < 2:
        raise CalibrationError("need at least 2 touches")
    R, t = xy_cal["R"], xy_cal["t_mm"]
    rows = []
    for tc in touches:
        cam = camera_xy_for_tool([tc["x"], tc["y"]], [head_a["x"], head_a["y"]], R, t)
        loc = find_pixel(frame_a, cam)
        d, n = depth_at(frame_a, loc["pixel_uv"])
        k = d - (head_a["z"] - tc["z_contact"])
        row = {"label": tc["label"], "machine_xy": [tc["x"], tc["y"]], "z_contact": tc["z_contact"],
               "camera_xy_mm": [round(float(c), 2) for c in cam], **loc, "depth_a_mm": round(d, 2),
               "depth_pixels_used": n, "k_mm": round(k, 2)}
        if frame_b is not None:
            camb = camera_xy_for_tool([tc["x"], tc["y"]], [head_b["x"], head_b["y"]], R, t)
            locb = find_pixel(frame_b, camb)
            db, _ = depth_at(frame_b, locb["pixel_uv"])
            row.update({"pixel_uv_b": locb["pixel_uv"], "depth_b_mm": round(db, 2),
                        "depth_change_mm": round(db - d, 2)})
        rows.append(row)
    ks = np.array([r["k_mm"] for r in rows])
    k_mean = float(ks.mean())
    for r in rows:
        r["predicted_z_contact"] = round(head_a["z"] - (r["depth_a_mm"] - k_mean), 2)
        r["z_residual_mm"] = round(r["predicted_z_contact"] - r["z_contact"], 2)
    out = {"touches": rows, "k_mm": round(k_mean, 2), "k_spread_mm": round(float(ks.max() - ks.min()), 2),
           "z_rms_mm": round(float(np.sqrt(np.mean([r["z_residual_mm"] ** 2 for r in rows]))), 2)}
    if frame_b is not None:
        dz = head_b["z"] - head_a["z"]
        changes = np.array([r["depth_change_mm"] for r in rows])
        out["head_move_mm"] = dz
        out["mean_depth_change_mm"] = round(float(changes.mean()), 2)
        out["z_scale_check"] = round(float(changes.mean() / dz), 4) if dz else None
    return out


def z_contact_for_depth(depth_mm, head_z, k_mm):
    """Machine Z where the tool tip would touch a surface seen at depth_mm from head Z head_z."""
    return head_z - (depth_mm - k_mm)
