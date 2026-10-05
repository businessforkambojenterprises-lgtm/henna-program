"""Measured depth -> camera-frame millimetre points.

Frame: RealSense SDK camera frame, as produced by pinhole deprojection
(x right, y down, z forward away from the lens), units millimetres.
This is NOT the RealSense Viewer PLY frame and NOT machine coordinates.

Missing or rejected depth stays NaN. Nothing here fills, smooths or
interpolates holes.
"""
from dataclasses import dataclass, asdict
import math

import numpy as np

# Per-pixel status codes
VALID = 0
MISSING = 1        # no depth measurement at all (NaN or <= 0)
UNSTABLE = 2       # measured in too few of the captured frames
NOISY = 3          # frame-to-frame spread too large

STATUS_NAMES = {VALID: "valid", MISSING: "missing", UNSTABLE: "unstable", NOISY: "noisy"}


class MissingCalibrationError(ValueError):
    """Raised when camera intrinsics are absent or unusable."""


@dataclass(frozen=True)
class Intrinsics:
    width: int
    height: int
    fx: float
    fy: float
    ppx: float
    ppy: float
    distortion: str  # description only; no correction is applied here
    source: str

    def to_dict(self):
        return asdict(self)


def require_intrinsics(width, height, fx, fy, ppx, ppy, distortion, source):
    """Build Intrinsics, refusing missing, non-finite or non-positive values."""
    values = {"width": width, "height": height, "fx": fx, "fy": fy, "ppx": ppx, "ppy": ppy}
    for name, v in values.items():
        if v is None:
            raise MissingCalibrationError(f"intrinsic '{name}' is missing")
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise MissingCalibrationError(f"intrinsic '{name}' is not a number: {v!r}")
        if not math.isfinite(v):
            raise MissingCalibrationError(f"intrinsic '{name}' is not finite")
    for name in ("width", "height", "fx", "fy"):
        if float(values[name]) <= 0:
            raise MissingCalibrationError(f"intrinsic '{name}' must be > 0")
    return Intrinsics(int(width), int(height), float(fx), float(fy),
                      float(ppx), float(ppy), str(distortion), str(source))


def raw_to_mm(raw, depth_scale_m):
    """Convert raw sensor units to millimetres using the device depth scale (metres/unit)."""
    if depth_scale_m is None or not math.isfinite(depth_scale_m) or depth_scale_m <= 0:
        raise MissingCalibrationError("depth scale is missing or invalid")
    return np.asarray(raw, dtype=np.float64) * depth_scale_m * 1000.0


def classify_depth(depth_mm, valid_frac=None, mad_mm=None,
                   min_valid_frac=None, max_mad_mm=None):
    """Return a uint8 status map. Checks are only applied when both data and threshold are given."""
    depth_mm = np.asarray(depth_mm, dtype=np.float64)
    status = np.full(depth_mm.shape, VALID, dtype=np.uint8)
    if valid_frac is not None and min_valid_frac is not None:
        status[np.asarray(valid_frac, dtype=np.float64) < min_valid_frac] = UNSTABLE
    if mad_mm is not None and max_mad_mm is not None:
        mad = np.asarray(mad_mm, dtype=np.float64)
        status[(status == VALID) & (mad > max_mad_mm)] = NOISY
    # Missing wins over everything else.
    status[~np.isfinite(depth_mm) | (depth_mm <= 0)] = MISSING
    return status


def deproject(depth_mm, intr):
    """Deproject every pixel to camera-frame mm. Returns (H, W, 3); NaN where depth is missing."""
    depth_mm = np.asarray(depth_mm, dtype=np.float64)
    if depth_mm.shape != (intr.height, intr.width):
        raise MissingCalibrationError(
            f"depth shape {depth_mm.shape} does not match intrinsics {intr.height}x{intr.width}")
    z = np.where(np.isfinite(depth_mm) & (depth_mm > 0), depth_mm, np.nan)
    v, u = np.indices(z.shape, dtype=np.float64)
    x = (u - intr.ppx) / intr.fx * z
    y = (v - intr.ppy) / intr.fy * z
    return np.stack([x, y, z], axis=-1)


def status_counts(status):
    return {STATUS_NAMES[k]: int((status == k).sum()) for k in STATUS_NAMES}
