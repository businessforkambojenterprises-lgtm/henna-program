"""Independent re-read of a hover program file (written separately from henna_preview.dry_run).

Reads the text exactly as UGS will, line by line, and checks: only comments, G21, G90,
G53 G0 Z<travel>, G53 G0 X Y (only while at travel Z), G53 G1 X Y Z F; every G1 Z at or above
the global floor and below travel Z; draw feed not above the limit; X/Y inside the machine;
the program starts and ends at travel Z.
"""
import re

_COMMENT = re.compile(r"^\([^()$;]*\)$")
_G0Z = re.compile(r"^G53 G0 Z(-?\d+\.\d+)$")
_G0XY = re.compile(r"^G53 G0 X(\d+\.\d+) Y(\d+\.\d+)$")
_G1 = re.compile(r"^G53 G1 X(\d+\.\d+) Y(\d+\.\d+) Z(-?\d+\.\d+) F(\d+(?:\.\d+)?)$")
X_MAX, Y_MAX = 225.0, 176.0


class ProgramReadError(ValueError):
    """Raised at the first line that breaks a rule."""


def reread(text, travel_z, floor_z, max_draw_feed):
    z, strokes, draws, zmin, zmax = None, 0, 0, None, None
    lines = text.splitlines()
    for n, line in enumerate(lines, start=1):
        if not line or _COMMENT.match(line) or line in ("G21", "G90"):
            continue
        m = _G0Z.match(line)
        if m:
            if float(m.group(1)) != travel_z:
                raise ProgramReadError(f"line {n}: rapid Z {m.group(1)} is not travel Z {travel_z}")
            z = travel_z
            continue
        m = _G0XY.match(line)
        if m:
            if z != travel_z:
                raise ProgramReadError(f"line {n}: sideways rapid move while not at travel Z")
            x, y = float(m.group(1)), float(m.group(2))
            if not (0 <= x <= X_MAX and 0 <= y <= Y_MAX):
                raise ProgramReadError(f"line {n}: X/Y outside the machine")
            strokes += 1
            continue
        m = _G1.match(line)
        if m:
            x, y, zz, f = (float(v) for v in m.groups())
            if not (0 <= x <= X_MAX and 0 <= y <= Y_MAX):
                raise ProgramReadError(f"line {n}: X/Y outside the machine")
            if zz < floor_z - 1e-9:
                raise ProgramReadError(f"line {n}: Z {zz} below the floor {floor_z}")
            if zz >= travel_z:
                raise ProgramReadError(f"line {n}: drawing Z {zz} not below travel Z")
            if f > max_draw_feed:
                raise ProgramReadError(f"line {n}: feed {f} above {max_draw_feed}")
            z, draws = zz, draws + 1
            zmin = zz if zmin is None else min(zmin, zz)
            zmax = zz if zmax is None else max(zmax, zz)
            continue
        raise ProgramReadError(f"line {n}: not an allowed line: {line[:60]}")
    if z != travel_z:
        raise ProgramReadError("program does not end at travel Z")
    if not draws:
        raise ProgramReadError("program has no drawing moves")
    return {"lines": len(lines), "strokes": strokes, "draw_moves": draws, "z_min": zmin, "z_max": zmax,
            "problems": 0}
