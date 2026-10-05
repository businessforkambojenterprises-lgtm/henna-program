"""Camera-to-tool X/Y calibration from clicked target dots (practice run: pencil).

Model, X/Y only:   tool_XY_at_dot = head_XY_at_capture + R @ cam_xy_mm + t
  cam_xy_mm : the dot in colour-camera millimetres (SDK deprojection of its
              measured depth). Only measured depth is used; a dot with no
              measured depth nearby is rejected.
  R         : 2x2 rotation, or rotation with a mirror flip if the camera's
              axes are flipped relative to the machine (both are tried; the
              better fit is reported).
  t         : the X/Y offset this calibration measures, in mm.

Valid only for the tool, head height and setup it was measured with.
Never machine-ready on its own.
"""
import numpy as np

from henna_preview.region import deproject_mm


class CalibrationError(ValueError):
    """Raised for unusable calibration inputs."""


def dot_camera_mm(raw, depth_scale, intr, uv, window=2):
    """Camera-frame mm of a clicked dot. Depth = median of MEASURED pixels within +/-window px."""
    h, w = raw.shape
    u, v = int(round(uv[0])), int(round(uv[1]))
    if not (0 <= u < w and 0 <= v < h):
        raise CalibrationError("clicked point is outside the image")
    patch = raw[max(0, v - window):v + window + 1, max(0, u - window):u + window + 1]
    vals = patch[patch > 0]
    if len(vals) == 0:
        return None, 0
    z_m = float(np.median(vals)) * depth_scale
    return deproject_mm([uv[0]], [uv[1]], [z_m], intr)[0], int(len(vals))


def _fit(a, b, reflect):
    """Least-squares R (rotation, optionally mirrored) and t with b ~ R a + t."""
    ca, cb = a.mean(0), b.mean(0)
    A, B = a - ca, b - cb
    if reflect:
        A = A * np.array([1.0, -1.0])
    U, _, Vt = np.linalg.svd(A.T @ B)
    d = np.sign(np.linalg.det(Vt.T @ U.T)) or 1.0
    R = Vt.T @ np.diag([1.0, d]) @ U.T
    if reflect:
        R = R @ np.diag([1.0, -1.0])
    t = cb - R @ ca
    return R, t


def solve(cam_xy, machine_xy, head_xy):
    """Fit tool_XY - head_XY = R cam_xy + t. Returns the better of plain / mirrored fits."""
    a = np.asarray(cam_xy, float)
    m = np.asarray(machine_xy, float)
    if a.shape != m.shape or a.ndim != 2 or a.shape[1] != 2:
        raise CalibrationError("camera and machine points must be matching (N, 2) arrays")
    if len(a) < 3:
        raise CalibrationError("need at least 3 dots with measured depth")
    if not (np.isfinite(a).all() and np.isfinite(m).all() and np.isfinite(head_xy).all()):
        raise CalibrationError("non-finite input")
    b = m - np.asarray(head_xy, float)
    best = None
    for reflect in (False, True):
        R, t = _fit(a, b, reflect)
        res = b - (a @ R.T + t)
        rms = float(np.sqrt((res ** 2).sum(1).mean()))
        if best is None or rms < best["rms_mm"]:
            best = {"R": R, "t": t, "mirrored": reflect, "residuals": res, "rms_mm": rms}
    # Scale check (not used in the model): ratio of spreads should be ~1 if units agree.
    sa = np.sqrt(((a - a.mean(0)) ** 2).sum(1).mean())
    sb = np.sqrt(((b - b.mean(0)) ** 2).sum(1).mean())
    best["scale_check"] = float(sb / sa) if sa > 0 else float("nan")
    best["rotation_deg"] = float(np.degrees(np.arctan2(best["R"][1, 0], best["R"][0, 0])))
    best["max_residual_mm"] = float(np.sqrt((best["residuals"] ** 2).sum(1)).max())
    return best


def predict(cam_xy, head_xy, R, t):
    """Tool X/Y that would sit over a camera point, for a given head position."""
    return np.asarray(head_xy, float) + np.asarray(cam_xy, float) @ np.asarray(R).T + np.asarray(t)
