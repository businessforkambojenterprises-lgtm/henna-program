"""NOZZLE SIMULATION toolpath for a hand: measured design points -> machine X/Y/Z.

  machine X/Y = head_XY + R @ camera_xy + t        (verified nozzle X/Y calibration)
  skin contact Z = head_Z - (camera_depth - k)      (nozzle Z calibration)
  drawing Z  = skin contact Z + standoff
  floor      = skin contact Z + floor_above_skin    (per point; output refused if any Z is below)

Simulation text only: header + deliberately invalid first command; no valve,
spindle, coolant or offset commands. Nothing is produced if any check fails.
"""
import re

import numpy as np

from henna_preview.calibration import predict

XY_LIMITS = {"x": (0.0, 225.0), "y": (0.0, 176.0)}
MAX_FEED = 2000.0
HEADER = [
    "(NOZZLE SIMULATION - NOT VALIDATED - DO NOT SEND)",
    "(motion_ready=false. Simulator output only. No dispensing commands.)",
    "$NOZZLE_SIM_ONLY_DO_NOT_SEND",
]
ALLOWED = re.compile(r"^(G21|G90|G53 G0 Z-?\d+\.\d{3}|G53 G0 X-?\d+\.\d{3} Y-?\d+\.\d{3}|"
                     r"G53 G1 X-?\d+\.\d{3} Y-?\d+\.\d{3} Z-?\d+\.\d{3} F\d+)$")


class NozzlePathError(ValueError):
    """Raised when a nozzle toolpath would break a limit. Nothing is produced."""


def check_params(p):
    for k in ("standoff_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min", "travel_z",
              "firmware_z_floor"):
        if not isinstance(p.get(k), (int, float)) or not np.isfinite(p[k]):
            raise NozzlePathError(f"{k} must be a number")
    if p["standoff_mm"] < p["floor_above_skin_mm"]:
        raise NozzlePathError("standoff is below the floor above skin")
    if p["floor_above_skin_mm"] <= 0:
        raise NozzlePathError("floor above skin must be > 0 mm")
    for k in ("draw_feed_mm_min", "travel_feed_mm_min"):
        if not 0 < p[k] <= MAX_FEED:
            raise NozzlePathError(f"{k} must be between 0 and {MAX_FEED}")
    if p["travel_z"] > 0:
        raise NozzlePathError("travel Z must be at or below Z home (0)")


def map_strokes(segments_cam_xyz, head, xy_cal, k_mm, p, depth_fn=None):
    """-> list of strokes, each (N, 5): x, y, skin_z, draw_z, floor_z.

    depth_fn (optional) corrects camera depth before conversion, e.g. the step-14 scale correction.
    """
    R, t = np.asarray(xy_cal["R"], float), np.asarray(xy_cal["t_mm"], float)
    strokes = []
    for seg in segments_cam_xyz:
        a = np.asarray(seg, float)
        if a.ndim != 2 or a.shape[1] != 3 or len(a) < 2:
            continue
        if not np.isfinite(a).all():
            raise NozzlePathError("non-measured point in a segment")
        xy = predict(a[:, :2], [head["x"], head["y"]], R, t)
        d = depth_fn(a[:, 2]) if depth_fn else a[:, 2]
        skin = head["z"] - (d - k_mm)
        strokes.append(np.column_stack([xy, skin, skin + p["standoff_mm"], skin + p["floor_above_skin_mm"]]))
    if not strokes:
        raise NozzlePathError("no drawable segments")
    for s in strokes:
        for name, i in (("x", 0), ("y", 1)):
            lo, hi = XY_LIMITS[name]
            if s[:, i].min() < lo or s[:, i].max() > hi:
                raise NozzlePathError(f"path leaves machine {name.upper()} travel {lo}..{hi}")
        if s[:, 3].min() < p["firmware_z_floor"]:
            raise NozzlePathError("drawing Z below the machine's Z travel")
        if s[:, 3].max() >= p["travel_z"]:
            raise NozzlePathError("travel Z is not above the drawing heights")
    return strokes


def gcode_lines(strokes, p):
    check_params(p)
    fd, ft = int(round(p["draw_feed_mm_min"])), int(round(p["travel_feed_mm_min"]))
    L = list(HEADER) + ["G21", "G90", f"G53 G0 Z{p['travel_z']:.3f}"]
    floors = []
    for s in strokes:
        L.append(f"G53 G0 X{s[0, 0]:.3f} Y{s[0, 1]:.3f}")
        for x, y, _, dz, fz in s:
            L.append(f"G53 G1 X{x:.3f} Y{y:.3f} Z{dz:.3f} F{fd}")
            floors.append(round(float(fz), 3))
        L.append(f"G53 G0 Z{p['travel_z']:.3f}")
    return L, floors


def independent_check(lines, floors, p):
    """Re-read every line separately: allowed commands only; each drawing Z >= its own floor."""
    if lines[:3] != HEADER:
        raise NozzlePathError("simulation header missing")
    i, z = 0, None
    for n, line in enumerate(lines[3:], start=4):
        if not ALLOWED.match(line):
            raise NozzlePathError(f"line {n} not allowed: {line}")
        vals = {k: float(v) for k, v in re.findall(r"([XYZF])(-?\d+(?:\.\d+)?)", line)}
        for a in "XY":
            if a in vals and not XY_LIMITS[a.lower()][0] <= vals[a] <= XY_LIMITS[a.lower()][1]:
                raise NozzlePathError(f"line {n}: {a} outside travel")
        if line.startswith("G53 G0 Z"):
            if vals["Z"] != p["travel_z"]:
                raise NozzlePathError(f"line {n}: rapid Z not at travel Z")
            z = vals["Z"]
        elif line.startswith("G53 G0 X"):
            if z != p["travel_z"]:
                raise NozzlePathError(f"line {n}: rapid X/Y move not at travel Z")
        elif line.startswith("G53 G1"):
            if i >= len(floors):
                raise NozzlePathError("more drawing moves than floors")
            if vals["Z"] < floors[i] - 1e-6:
                raise NozzlePathError(f"line {n}: Z {vals['Z']} below its floor {floors[i]}")
            if vals["Z"] < p["firmware_z_floor"] or not 0 < vals["F"] <= MAX_FEED:
                raise NozzlePathError(f"line {n}: Z or feed out of range")
            z = vals["Z"]
            i += 1
    if i != len(floors):
        raise NozzlePathError("drawing move count does not match floors")
    if z != p["travel_z"]:
        raise NozzlePathError("program does not end at travel Z")
    return i
