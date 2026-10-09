"""Measured surface offset: raise the measured surface by what nozzle touches showed (prosthetic only).

Some surfaces (translucent silicone, skin) read deeper than they are: light enters the material.
Touching the nozzle to the surface at a few program points measures how much higher the real
surface is than the camera said. The offset is then applied to the depth, so smoothing, floors,
the hand top and every check see the corrected surface.

Rules:
  * at least 3 touches, all on the SAME recording the program is planned from (hand not moved);
  * offset per touch = touch Z - camera skin Z (positive = real surface higher than measured);
  * the touches must agree within max_spread_mm (a constant offset, not a shape error);
  * applied offset = the SMALLEST per-touch offset, never below 0: it can only raise the nozzle,
    and no touched point ends up closer than the planned standoff;
  * an offset larger than the standoff is refused (the uncorrected path would have hit: re-measure).
Touching is for the prosthetic hand only - never a person.
"""
import json
import math

MAX_SPREAD_MM = 0.5


class SurfaceOffsetError(ValueError):
    """Raised when the touches cannot give a trustworthy offset."""


def offset_from_touches(touches, standoff_mm, max_spread_mm=MAX_SPREAD_MM):
    if len(touches) < 3:
        raise SurfaceOffsetError("need at least 3 touches for a surface offset")
    per = []
    for t in touches:
        a, b = float(t["touch_z"]), float(t["camera_skin_z"])
        if not (math.isfinite(a) and math.isfinite(b)):
            raise SurfaceOffsetError("touch or camera height is not a number")
        per.append(round(a - b, 3))
    spread = max(per) - min(per)
    if spread > max_spread_mm:
        raise SurfaceOffsetError(f"touch offsets disagree by {spread:.2f} mm (limit {max_spread_mm} mm): "
                                 "not a constant offset - do not correct")
    applied = max(0.0, min(per))
    if applied > standoff_mm:
        raise SurfaceOffsetError(f"offset {applied:.2f} mm is larger than the standoff {standoff_mm} mm: "
                                 "the hand may have moved - re-measure")
    return {"per_touch_mm": per, "spread_mm": round(spread, 3), "mean_mm": round(sum(per) / len(per), 3),
            "applied_mm": round(applied, 3), "rule": "smallest per-touch offset, never below 0"}


def load_offset(path, recording, standoff_mm):
    """Read a surface-offset record and check it belongs to this recording and a prosthetic subject."""
    with open(path, encoding="utf-8") as f:
        rec = json.load(f)
    if "prosthetic" not in str(rec.get("subject", "")).lower():
        raise SurfaceOffsetError("surface offsets from touches are for a prosthetic hand only")
    if rec.get("recording") != recording:
        raise SurfaceOffsetError(f"offset was measured on {rec.get('recording')}, not on {recording}")
    return {"file": path, **offset_from_touches(rec["touches"], standoff_mm, rec.get("max_spread_mm", MAX_SPREAD_MM))}


def shifted_depth_fn(depth_fn, applied_mm):
    """Depth (mm) -> depth reduced by the offset, so machine surface Z comes out applied_mm higher."""
    if applied_mm < 0:
        raise SurfaceOffsetError("offset must not be negative")
    return lambda d: depth_fn(d) - applied_mm
