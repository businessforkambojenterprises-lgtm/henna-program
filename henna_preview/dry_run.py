"""DRY RUN program: a checked nozzle path lifted into the air (no hand, no dispensing).

Unlike the simulation text, this file is meant to be run by the user on an empty bed.
It contains ONLY: G21, G90, G53 G0 Z (travel), G53 G0 X Y (at travel Z), G53 G1 X Y Z F.
No spindle, coolant, valve, offset, probing, homing or $ commands.
Every check below must pass or nothing is produced; the text is then re-read independently.
"""
import re

import numpy as np

XY_LIMITS = {"x": (0.0, 225.0), "y": (0.0, 176.0)}
FIRMWARE_Z_FLOOR = -97.0
MAX_FEED = 2000.0
ALLOWED = re.compile(r"^(\([^()$;]*\)|G21|G90|G53 G0 Z-?\d+\.\d{3}|G53 G0 X\d+\.\d{3} Y\d+\.\d{3}|"
                     r"G53 G1 X\d+\.\d{3} Y\d+\.\d{3} Z-?\d+\.\d{3} F\d+)$")


class DryRunError(ValueError):
    """Raised when the dry run would break a limit. Nothing is produced."""


def check(p):
    for k in ("lift_mm", "travel_z", "floor_z", "draw_feed_mm_min", "travel_feed_mm_min"):
        if not isinstance(p.get(k), (int, float)) or not np.isfinite(p[k]):
            raise DryRunError(f"{k} must be a number")
    if p["lift_mm"] <= 0:
        raise DryRunError("lift must be positive")
    if not FIRMWARE_Z_FLOOR < p["floor_z"] < p["travel_z"] <= 0:
        raise DryRunError("need firmware floor < floor Z < travel Z <= 0")
    for k in ("draw_feed_mm_min", "travel_feed_mm_min"):
        if not 0 < p[k] <= MAX_FEED:
            raise DryRunError(f"{k} out of range")


def lift_strokes(strokes_xyz, p):
    """strokes: list of (N, >=4) arrays with x, y, (skin), draw_z in column 3. Returns lifted (N, 3)."""
    check(p)
    out = []
    for s in strokes_xyz:
        a = np.asarray(s, float)
        out.append(np.column_stack([a[:, 0], a[:, 1], a[:, 3] + p["lift_mm"]]))
    allz = np.concatenate([o[:, 2] for o in out])
    if allz.min() < p["floor_z"]:
        raise DryRunError(f"lifted path goes down to {allz.min():.2f}, below the floor {p['floor_z']}")
    if allz.max() >= p["travel_z"]:
        raise DryRunError(f"lifted path rises to {allz.max():.2f}, not below travel Z {p['travel_z']}")
    for o in out:
        for name, i in (("x", 0), ("y", 1)):
            lo, hi = XY_LIMITS[name]
            if o[:, i].min() < lo or o[:, i].max() > hi:
                raise DryRunError(f"path leaves machine {name.upper()} travel")
    return out


def program(lifted, p, header):
    fd, ft = int(round(p["draw_feed_mm_min"])), int(round(p["travel_feed_mm_min"]))
    L = [f"({h})" for h in header] + ["G21", "G90", f"G53 G0 Z{p['travel_z']:.3f}"]
    for s in lifted:
        L.append(f"G53 G0 X{s[0, 0]:.3f} Y{s[0, 1]:.3f}")
        for x, y, z in s:
            L.append(f"G53 G1 X{x:.3f} Y{y:.3f} Z{z:.3f} F{fd}")
        L.append(f"G53 G0 Z{p['travel_z']:.3f}")
    return L


def independent_check(lines, p):
    """Re-read the program as text; simulate positions; enforce every limit again."""
    z = None
    moves = []
    pos = [None, None, None]
    for n, line in enumerate(lines, start=1):
        if not ALLOWED.match(line):
            raise DryRunError(f"line {n} not allowed: {line}")
        if line.startswith("("):
            continue
        if re.search(r"\b(M\d|S\d|T\d|G92|G10|G38|G28|G30|\$)", line):
            raise DryRunError(f"line {n}: forbidden command")
        v = {k: float(x) for k, x in re.findall(r"([XYZF])(-?\d+(?:\.\d+)?)", line)}
        if "Z" in v:
            if v["Z"] < p["floor_z"] - 1e-9:
                raise DryRunError(f"line {n}: Z {v['Z']} below floor {p['floor_z']}")
            if v["Z"] > 0:
                raise DryRunError(f"line {n}: Z above home")
        for a in "XY":
            if a in v and not XY_LIMITS[a.lower()][0] <= v[a] <= XY_LIMITS[a.lower()][1]:
                raise DryRunError(f"line {n}: {a} outside travel")
        if "F" in v and not 0 < v["F"] <= MAX_FEED:
            raise DryRunError(f"line {n}: feed out of range")
        if line.startswith("G53 G0 X") and z != p["travel_z"]:
            raise DryRunError(f"line {n}: sideways rapid move not at travel Z")
        if line.startswith("G53 G0 Z") and v["Z"] != p["travel_z"]:
            raise DryRunError(f"line {n}: rapid Z move not to travel Z")
        new = [v.get("X", pos[0]), v.get("Y", pos[1]), v.get("Z", pos[2])]
        if pos[2] is not None or new[2] is not None:
            moves.append({"from": list(pos), "to": new, "kind": "draw" if "G1" in line else "rapid"})
        pos = new
        z = pos[2]
    if z != p["travel_z"]:
        raise DryRunError("program does not end at travel Z")
    return moves
