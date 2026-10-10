"""Parse a GRBL '?' status line pasted by the user (the web page never talks to the controller).

Example: <Idle|MPos:145.800,42.599,-40.000|FS:0,0|Ov:100,100,100>
Only machine positions (MPos) are accepted; the machine must be Idle (not moving, not in alarm).
"""
import re

POSE_1 = (145.8, 42.6, -40.0)   # the capture pose every calibration and test used
POSE_TOL_MM = 0.05

_LINE = re.compile(r"<\s*([A-Za-z]+)(?::\d+)?\s*\|\s*MPos:\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?),\s*(-?\d+(?:\.\d+)?)")


class StatusLineError(ValueError):
    """Raised for a missing, malformed or unusable status line."""


def parse_status(text):
    """-> {'state': 'Idle', 'mpos': [x, y, z], 'line': '<...>'} from the first status line in text."""
    if not isinstance(text, str) or not text.strip():
        raise StatusLineError("paste the line that starts with <Idle|MPos: ...")
    m = _LINE.search(text)
    if not m:
        if "WPos" in text:
            raise StatusLineError("this status line shows WPos; machine positions (MPos) are needed")
        raise StatusLineError("no <State|MPos:x,y,z ...> status line found")
    state = m.group(1)
    if state != "Idle":
        raise StatusLineError(f"the machine was '{state}', not Idle - wait until it stops, then send ? again")
    end = text.find(">", m.start())
    return {"state": state, "mpos": [float(m.group(i)) for i in (2, 3, 4)],
            "line": text[m.start():end + 1] if end > 0 else text[m.start():].strip()}


def check_pose(mpos, expected=POSE_1, tol=POSE_TOL_MM):
    """Raise unless mpos is within tol (mm) of the expected pose on every axis."""
    bad = [f"{a} {v:.3f} (expected {e:.3f})" for a, v, e in zip("XYZ", mpos, expected) if abs(v - e) > tol]
    if bad:
        raise StatusLineError("head is not at the capture pose: " + ", ".join(bad))
    return True


def check_xy(mpos, x, y, tol=POSE_TOL_MM):
    """Raise unless the reading was taken over the given X/Y (the paper-feeler point)."""
    if abs(mpos[0] - x) > tol or abs(mpos[1] - y) > tol:
        raise StatusLineError(f"this reading is at X{mpos[0]:.3f} Y{mpos[1]:.3f}, not over the point X{x:.3f} Y{y:.3f}")
    return True
