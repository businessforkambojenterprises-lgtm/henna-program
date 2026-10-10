"""Low hops between strokes ("Z like a laser"), user decision 2026-10-10.

Between two strokes the nozzle rises straight up to a hop height, crosses straight at that height and drops
onto the next stroke. Hop height = highest MEASURED skin under the crossing + clearance (5 mm), never below
either end's nozzle height + 0.5 mm. The crossing is sampled every 0.5 mm along the straight line and on
parallel lines up to 1.5 mm to each side (X/Y calibration margin and nozzle size). If any sample has no
measured skin or lies outside the accepted region, that hop goes to travel Z instead - never a guess.
`verify_hops` re-reads the hop moves from the PROGRAM TEXT and re-samples the skin under them (own code).
"""
import math
import re

import numpy as np

from henna_preview.smooth_path import measure_under_plan

HOP_CLEARANCE_MM = 5.0
BAND_MM = 1.5
STEP_MM = 0.5
MIN_RISE_MM = 0.5
MIN_GAP_TO_TRAVEL_MM = 1.0


class HopError(ValueError):
    """A hop is not clear of the measured skin, or the program text does not match its hops."""


def crossing_points(a_xy, b_xy, band=BAND_MM, step=STEP_MM):
    """Points on the straight crossing a->b and on parallel lines up to `band` mm to each side."""
    a, b = np.asarray(a_xy, float), np.asarray(b_xy, float)
    v = b - a
    length = float(np.hypot(*v))
    n = max(1, int(math.ceil(length / step)))
    centre = a + np.outer(np.linspace(0.0, 1.0, n + 1), v)
    perp = np.array([-v[1], v[0]]) / length if length > 1e-9 else np.array([1.0, 0.0])
    offsets = np.linspace(-band, band, int(round(2 * band / step)) + 1)
    pts = [centre + o * perp for o in offsets]
    if length > 1e-9:                     # also the band across each end, along the crossing direction
        along = v / length
        for end in (a, b):
            for o in offsets:
                pts.append(end + o * along + np.outer(offsets, perp))
    return np.vstack(pts)


def skin_max(points_xy, fr, mask, head, xy_cal, k, depth_fn):
    """Highest machine skin Z over the points, or None if any point has no measured skin inside the region."""
    zs = []
    for status, cam in measure_under_plan(points_xy, fr, mask, head, xy_cal):
        if status != "ok":
            return None
        d = depth_fn(cam[2]) if depth_fn else cam[2]
        zs.append(head["z"] - (d - k))
    return float(max(zs))


def plan_hops(paths, sample_fn, travel_z, clearance=HOP_CLEARANCE_MM):
    """One hop per gap between strokes. sample_fn(points_xy) -> highest skin Z or None."""
    hops = []
    for s0, s1 in zip(paths[:-1], paths[1:]):
        top = sample_fn(crossing_points(s0[-1, :2], s1[0, :2]))
        if top is None:
            hops.append({"z": float(travel_z), "low": False, "reason": "no measured skin along the crossing"})
            continue
        z = max(top + clearance, float(s0[-1, 3]) + MIN_RISE_MM, float(s1[0, 3]) + MIN_RISE_MM)
        z = math.ceil(z * 1000.0) / 1000.0
        if z > travel_z - MIN_GAP_TO_TRAVEL_MM:
            hops.append({"z": float(travel_z), "low": False, "reason": "hop would reach travel height"})
            continue
        hops.append({"z": z, "low": True, "skin_max_z": round(top, 3), "reason": "low hop"})
    return hops


_G0Z = re.compile(r"^G53 G0 Z(-?\d+\.\d{3})$")
_G0XY = re.compile(r"^G53 G0 X(\d+\.\d{3}) Y(\d+\.\d{3})$")
_G1 = re.compile(r"^G53 G1 X(\d+\.\d{3}) Y(\d+\.\d{3}) Z(-?\d+\.\d{3}) F\d+$")


def hop_moves_from_text(text, travel_z):
    """[(from_xy, to_xy, hop_z)] for every sideways move below travel Z, read from the program text."""
    moves, pos, z = [], None, None
    for line in text.splitlines():
        m = _G0Z.match(line)
        if m:
            z = float(m.group(1))
            continue
        m = _G0XY.match(line)
        if m:
            new = (float(m.group(1)), float(m.group(2)))
            if z is not None and z < travel_z:
                if pos is None:
                    raise HopError("low sideways move before any stroke")
                moves.append((pos, new, z))
            pos = new
            continue
        m = _G1.match(line)
        if m:
            pos, z = (float(m.group(1)), float(m.group(2))), float(m.group(3))
    return moves


def verify_hops(text, sample_fn, travel_z, clearance=HOP_CLEARANCE_MM):
    """Independent: every low crossing in the text clears the measured skin under it by `clearance`."""
    checked = []
    for a, b, z in hop_moves_from_text(text, travel_z):
        top = sample_fn(crossing_points(a, b))
        if top is None:
            raise HopError(f"low hop at Z {z} crosses points without measured skin")
        if z < top + clearance - 1e-6:
            raise HopError(f"low hop at Z {z} is only {z - top:.2f} mm above the skin (needs {clearance:g})")
        checked.append({"from": list(a), "to": list(b), "z": z, "skin_max_z": round(top, 3),
                        "clearance_mm": round(z - top, 3)})
    return checked
