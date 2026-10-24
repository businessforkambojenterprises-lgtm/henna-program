"""Live height following on a real hand (user 2026-10-24: "Option B - the nozzle follows the skin live" -> "build this",
with the live gap pause removed first).

The checked program stays the plan. Just before each DRAWING line (G53 G1 X Y Z F) goes to the controller, the
newest camera picture (the hand watch's live machine points, at most MAX_AGE_S old) is compared with the scan at that
line's own X/Y - ahead of the tip, not under it, so the nozzle does not hide it and the camera's delay is covered by
the time until the tip gets there. The skin's change since the scan (median of the live points within RADIUS_MM minus
the scanned skin there) moves THIS line's Z by the same amount, inside a SAFETY BAND around the checked Z:
  at most DOWN_MM lower and UP_MM higher than the checked line, and never more than STEP_MM from the previous line's
  correction (no jumping on depth noise); changes under DEADBAND_MM are not followed; never below the controller's
  floor, never at or above travel Z.
X, Y and the feed never change. No fresh picture, too few points there, or a line that is not a drawing move: the
line goes as checked (and the correction relaxes back to 0 by STEP_MM per line). The link checks every changed line
again before sending (grbl_program_link_v29 _follow_ok). Real-hand runs with the camera watching only - never an
air run (no skin) and never a prosthetic job (no watch).
"""
import re
import time

import numpy as np

DOWN_MM = 1.0
UP_MM = 2.0
STEP_MM = 0.3
DEADBAND_MM = 0.6                   # smaller differences are measuring noise: with the hand unchanged, the live spot
                                    # around a line and the scan's 1 mm grid differ by up to about +-0.6 mm on sloped
                                    # skin (real program 2026-10-24: -0.54 .. +0.66, mean 0.00) - not followed
RADIUS_MM = 1.5
MIN_POINTS = 5
MAX_AGE_S = 0.8
G1_RE = re.compile(r"^G53 G1 X(\d+\.\d{3}) Y(\d+\.\d{3}) Z(-?\d+\.\d{3}) F(\d+(?:\.\d+)?)$")


def parse_g1(line):
    m = G1_RE.fullmatch(line or "")
    return None if m is None else (float(m.group(1)), float(m.group(2)), float(m.group(3)), m.group(4))


def skin_change(ref, pts, x, y, radius=RADIUS_MM):
    """Live skin minus scanned skin at (x, y), or None (not seen / not scanned there)."""
    if pts is None or not len(pts):
        return None
    near = pts[np.hypot(pts[:, 0] - x, pts[:, 1] - y) <= radius]
    if len(near) < MIN_POINTS:
        return None
    zr, _ = ref.lookup(np.array([[x, y]]))
    if not np.isfinite(zr[0]):
        return None
    return float(np.median(near[:, 2]) - zr[0])


class Follower:
    """adjust(line) -> the line to send. live() -> (time.monotonic() of the picture, (N, 3) machine points) or None."""

    def __init__(self, ref, live, floor_z, travel_z, clock=time.monotonic):
        self.ref, self.live, self.clock = ref, live, clock
        self.floor_z, self.travel_z = float(floor_z), float(travel_z)
        self.dz = 0.0
        self.stats = {"lines": 0, "followed": 0, "min_dz": None, "max_dz": None, "unseen": 0}

    def _measured(self, x, y):
        got = self.live()
        if not got:
            return None
        t, pts = got
        if self.clock() - float(t) > MAX_AGE_S:
            return None
        return skin_change(self.ref, pts, x, y)

    def adjust(self, line):
        g = parse_g1(line)
        if g is None:
            return line
        x, y, z, f = g
        self.stats["lines"] += 1
        want = self._measured(x, y)
        if want is None:
            self.stats["unseen"] += 1
            want = 0.0                                      # not seen: drift back to the plan
        if abs(want) < DEADBAND_MM:
            want = 0.0
        want = min(UP_MM, max(-DOWN_MM, want))
        self.dz = min(self.dz + STEP_MM, max(self.dz - STEP_MM, want))
        self.dz = round(min(UP_MM, max(-DOWN_MM, self.dz)), 3)
        new_z = round(z + self.dz, 3)
        if new_z < self.floor_z or new_z >= self.travel_z:
            self.dz = 0.0
            return line
        if abs(self.dz) > 1e-9:
            self.stats["followed"] += 1
            s = self.stats
            s["min_dz"] = self.dz if s["min_dz"] is None else min(s["min_dz"], self.dz)
            s["max_dz"] = self.dz if s["max_dz"] is None else max(s["max_dz"], self.dz)
        return f"G53 G1 X{x:.3f} Y{y:.3f} Z{new_z:.3f} F{f}"


def line_ok(orig, new, floor_z, travel_z):
    """The link's own check of a changed line: the same X, Y and feed, Z within the band around the checked Z, above
    the floor and below travel Z. Anything else is sent as checked."""
    if new == orig:
        return True
    a, b = parse_g1(orig), parse_g1(new)
    if a is None or b is None or a[0] != b[0] or a[1] != b[1] or a[3] != b[3]:
        return False
    dz = b[2] - a[2]
    return -DOWN_MM - 1e-6 <= dz <= UP_MM + 1e-6 and b[2] >= float(floor_z) - 1e-9 and b[2] < float(travel_z)
