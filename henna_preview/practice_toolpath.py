"""PENCIL PRACTICE toolpath: camera-frame path -> machine X/Y via a verified pencil calibration.

Simulation only. The G-code text starts with a header and a deliberately invalid
line so GRBL rejects it if streamed by mistake (a speed bump, not a lock).
Z is NOT taken from the camera: every drawing move uses the user's fixed draw_z.
Nothing is produced if any value breaks the user's Z floor, travel limits or feeds.
No valve / dispense / spindle commands are ever written.
"""
import re

import numpy as np

from henna_preview.calibration import predict

MACHINE_XY_LIMITS = {"x": (0.0, 225.0), "y": (0.0, 176.0)}  # from $130/$131 (soft limits)
MAX_FEED = 2000.0  # from $110/$111
HEADER = [
    "(PENCIL PRACTICE - NOT VALIDATED - DO NOT SEND)",
    "(motion_ready=false. Simulator output only. Not for the nozzle or a hand.)",
    "$PRACTICE_ONLY_DO_NOT_SEND",
]
ALLOWED = re.compile(r"^(G21|G90|G53 G0 (X-?\d+\.\d{3} Y-?\d+\.\d{3}|Z-?\d+\.\d{3})|G53 G1 (X-?\d+\.\d{3} Y-?\d+\.\d{3}|Z-?\d+\.\d{3}) F\d+)$")


class PracticeError(ValueError):
    """Raised when a practice toolpath would break a limit. Nothing is produced."""


def check_params(p):
    for k in ("draw_z", "z_floor", "travel_z", "draw_feed_mm_min", "travel_feed_mm_min"):
        if not isinstance(p.get(k), (int, float)) or not np.isfinite(p[k]):
            raise PracticeError(f"{k} must be a number")
    if p["draw_z"] < p["z_floor"]:
        raise PracticeError(f"drawing Z {p['draw_z']} is below the Z floor {p['z_floor']}")
    if p["travel_z"] <= p["draw_z"]:
        raise PracticeError("travel Z must be above drawing Z")
    if p["travel_z"] > 0:
        raise PracticeError("travel Z must be at or below Z home (0)")
    for k in ("draw_feed_mm_min", "travel_feed_mm_min"):
        if not 0 < p[k] <= MAX_FEED:
            raise PracticeError(f"{k} must be between 0 and {MAX_FEED}")


def to_machine_strokes(segments_cam_mm, head_xy, R, t):
    """Each measured segment (camera mm) -> machine X/Y polyline."""
    out = []
    for seg in segments_cam_mm:
        pts = np.asarray(seg, float)
        if len(pts) < 2:
            continue
        out.append(predict(pts[:, :2], head_xy, R, t))
    if not out:
        raise PracticeError("no drawable segments")
    for s in out:
        for name, i in (("x", 0), ("y", 1)):
            lo, hi = MACHINE_XY_LIMITS[name]
            if s[:, i].min() < lo or s[:, i].max() > hi:
                raise PracticeError(f"path leaves machine {name.upper()} travel {lo}..{hi}")
    return out


def gcode_lines(strokes, p):
    check_params(p)
    f_draw, f_trav = int(round(p["draw_feed_mm_min"])), int(round(p["travel_feed_mm_min"]))
    L = list(HEADER) + ["G21", "G90", f"G53 G0 Z{p['travel_z']:.3f}"]
    for s in strokes:
        L.append(f"G53 G0 X{s[0, 0]:.3f} Y{s[0, 1]:.3f}")
        L.append(f"G53 G1 Z{p['draw_z']:.3f} F{f_draw}")
        for x, y in s[1:]:
            L.append(f"G53 G1 X{x:.3f} Y{y:.3f} F{f_draw}")
        L.append(f"G53 G0 Z{p['travel_z']:.3f}")
    return L


def independent_check(lines, p):
    """Re-read the text line by line, as a separate check, and simulate the moves."""
    if lines[:3] != HEADER:
        raise PracticeError("practice header missing")
    z, moves = None, []
    x = y = None
    for n, line in enumerate(lines[3:], start=4):
        if not ALLOWED.match(line):
            raise PracticeError(f"line {n} is not an allowed practice command: {line}")
        for key, val in re.findall(r"([XYZF])(-?\d+(?:\.\d+)?)", line):
            v = float(val)
            if key == "Z":
                if v < p["z_floor"]:
                    raise PracticeError(f"line {n}: Z {v} below floor {p['z_floor']}")
                z = v
            elif key == "F" and not 0 < v <= MAX_FEED:
                raise PracticeError(f"line {n}: feed {v} out of range")
            elif key in "XY":
                lo, hi = MACHINE_XY_LIMITS[key.lower()]
                if not lo <= v <= hi:
                    raise PracticeError(f"line {n}: {key} {v} outside {lo}..{hi}")
        if "X" in line:
            nx, ny = (float(v) for v in re.findall(r"[XY](-?\d+\.\d+)", line))
            if z is None:
                raise PracticeError(f"line {n}: X/Y move before any Z")
            if "G1" in line and z != p["draw_z"]:
                raise PracticeError(f"line {n}: drawing move not at draw Z")
            if "G0" in line and z != p["travel_z"]:
                raise PracticeError(f"line {n}: rapid move not at travel Z")
            moves.append({"from": [x, y, z], "to": [nx, ny, z], "kind": "draw" if "G1" in line else "travel"})
            x, y = nx, ny
        elif z is not None and x is not None:
            moves.append({"from": [x, y, None], "to": [x, y, z], "kind": "z"})
    if z != p["travel_z"]:
        raise PracticeError("program does not end at travel Z")
    return moves
