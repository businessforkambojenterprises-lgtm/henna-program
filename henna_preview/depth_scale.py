"""Camera depth scale from recordings at several head Z (same head X/Y, nothing moved).

The camera moves only vertically between recordings, so each physical spot keeps the
same camera X/Y. For a spot, true depth changes exactly with head Z (Z axis verified by
caliper). Model, fitted over all spots together (one shared slope, one intercept per spot):
    d_camera = a_spot + g * (Z_head - Z_ref)
g = 1 means camera depth changes match real movement; g < 1 means the camera reads short.
Only measured depth is used; a spot missing depth in any recording is dropped.

Correction (exact at d_ref, where the Z calibration k was measured):
    d_true = d_ref + (d_camera - d_ref) / g
"""
import numpy as np

from henna_preview.calibration import CalibrationError
from henna_preview.region import deproject_mm
from henna_preview.zcal import depth_at, find_pixel


def spot_grid(frame, step_px=40, margin_px=30):
    """Camera X/Y of a pixel grid of measured points in a reference frame."""
    raw, scale, intr = frame["depth_raw_aligned"], frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    h, w = raw.shape
    us, vs = np.meshgrid(np.arange(margin_px, w - margin_px, step_px), np.arange(margin_px, h - margin_px, step_px))
    us, vs = us.ravel(), vs.ravel()
    ok = raw[vs, us] > 0
    us, vs = us[ok], vs[ok]
    pts = deproject_mm(us, vs, raw[vs, us].astype(float) * scale, intr)
    return pts[:, :2]


def measure(frames, head_zs, spots_cam_xy, max_miss_mm=0.5):
    """Depth of each spot in each frame -> (n_spots_kept, n_frames) array plus kept spot indices."""
    D, kept = [], []
    for i, cam in enumerate(spots_cam_xy):
        row = []
        for f in frames:
            try:
                loc = find_pixel(f, cam)
                if loc["xy_miss_mm"] > max_miss_mm:
                    raise CalibrationError("miss")
                d, _ = depth_at(f, loc["pixel_uv"], window=1)
            except CalibrationError:
                row = None
                break
            row.append(d)
        if row is not None:
            D.append(row)
            kept.append(i)
    return np.array(D), kept


def fit_scale(D, head_zs, z_ref):
    """Shared slope g and per-spot intercepts; returns g, residuals and per-spot slopes."""
    D = np.asarray(D, float)
    if D.ndim != 2 or D.shape[0] < 3 or D.shape[1] < 3:
        raise CalibrationError("need at least 3 spots seen in at least 3 recordings")
    x = np.asarray(head_zs, float) - z_ref
    Dc = D - D.mean(1, keepdims=True)
    xc = x - x.mean()
    g = float((Dc * xc).sum() / (D.shape[0] * (xc ** 2).sum()))
    a = D.mean(1) - g * x.mean()
    res = D - (a[:, None] + g * x[None, :])
    per_spot = np.array([np.polyfit(x, row, 1)[0] for row in D])
    return {"g": g, "intercepts": a, "residuals": res, "rms_mm": float(np.sqrt((res ** 2).mean())),
            "per_spot_slope": per_spot}


def correct_depth(d_camera, g, d_ref):
    return d_ref + (np.asarray(d_camera, float) - d_ref) / g
