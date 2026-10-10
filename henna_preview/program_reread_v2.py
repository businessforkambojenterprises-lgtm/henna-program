"""Independent re-read for hover programs WITH low hops between strokes (Studio v17).

Same rules as program_reread.reread, plus the hop line shape:
  G53 G0 Z<h>   with h = travel Z, or floor < h < travel Z (a low hop) - and never lower than where the nozzle is
  G53 G0 X Y    only right after reaching travel Z or a hop height (straight crossing at that height)
  G53 G1 X Y Z F  drawing: floor <= Z < travel Z, F <= limit
The program must start by rising to travel Z, the first sideways move must be at travel Z, and it must end at
travel Z. `program_lines_v2` is the run-time allowlist: the lines to send (comments dropped) + end position.
"""
import re

_COMMENT = re.compile(r"^\([^()$;]*\)$")
_G0Z = re.compile(r"^G53 G0 Z(-?\d+\.\d+)$")
_G0XY = re.compile(r"^G53 G0 X(\d+\.\d+) Y(\d+\.\d+)$")
_G1 = re.compile(r"^G53 G1 X(\d+\.\d+) Y(\d+\.\d+) Z(-?\d+\.\d+) F(\d+(?:\.\d+)?)$")
X_MAX, Y_MAX = 225.0, 176.0
MAX_LINES = 20000


class ProgramReadError(ValueError):
    """Raised at the first line that breaks a rule."""


def _walk(text, travel_z, floor_z, max_draw_feed, firmware_floor=None):
    floor = max(floor_z, firmware_floor) if firmware_floor is not None else floor_z
    z, x, y, started = None, None, None, False
    send, strokes, hops, draws, zmin, zmax, hop_zs = [], 0, 0, 0, None, None, []
    lines = text.splitlines()
    for n, line in enumerate(lines, start=1):
        if not line or _COMMENT.match(line):
            continue
        if line in ("G21", "G90"):
            if z is not None:
                raise ProgramReadError(f"line {n}: {line} after the first move")
            send.append(line)
            continue
        m = _G0Z.match(line)
        if m:
            h = float(m.group(1))
            if z is None and h != travel_z:
                raise ProgramReadError(f"line {n}: the program must first rise to travel Z {travel_z}")
            if h != travel_z:
                if not floor < h < travel_z:
                    raise ProgramReadError(f"line {n}: hop Z {h} outside floor {floor} .. travel {travel_z}")
                if z is not None and h < z - 1e-9:
                    raise ProgramReadError(f"line {n}: hop Z {h} is below the nozzle (Z {z}) - a hop only rises")
                hops += 1
                hop_zs.append(h)
            z, started = h, True
            send.append(line)
            continue
        m = _G0XY.match(line)
        if m:
            prev = send[-1] if send else ""
            if not (z is not None and _G0Z.match(prev)):
                raise ProgramReadError(f"line {n}: sideways rapid move not right after rising to travel Z or a hop")
            if x is None and z != travel_z:
                raise ProgramReadError(f"line {n}: the first sideways move must be at travel Z")
            x, y = float(m.group(1)), float(m.group(2))
            if not (0 <= x <= X_MAX and 0 <= y <= Y_MAX):
                raise ProgramReadError(f"line {n}: X/Y outside the machine")
            strokes += 1
            send.append(line)
            continue
        m = _G1.match(line)
        if m:
            x, y, zz, f = (float(v) for v in m.groups())
            if not started:
                raise ProgramReadError(f"line {n}: drawing move before rising to travel Z")
            if not (0 <= x <= X_MAX and 0 <= y <= Y_MAX):
                raise ProgramReadError(f"line {n}: X/Y outside the machine")
            if zz < floor - 1e-9:
                raise ProgramReadError(f"line {n}: Z {zz} below the floor {floor}")
            if zz >= travel_z:
                raise ProgramReadError(f"line {n}: drawing Z {zz} not below travel Z")
            if f > max_draw_feed:
                raise ProgramReadError(f"line {n}: feed {f} above {max_draw_feed}")
            z, draws = zz, draws + 1
            zmin = zz if zmin is None else min(zmin, zz)
            zmax = zz if zmax is None else max(zmax, zz)
            send.append(line)
            continue
        raise ProgramReadError(f"line {n}: not an allowed line: {line[:60]}")
    if z != travel_z or x is None:
        raise ProgramReadError("program does not end at travel Z")
    if not draws:
        raise ProgramReadError("program has no drawing moves")
    if send[:2] != ["G21", "G90"]:
        raise ProgramReadError("program must start with G21 and G90")
    if len(send) > MAX_LINES:
        raise ProgramReadError("program is too long")
    summary = {"lines": len(lines), "strokes": strokes, "draw_moves": draws, "z_min": zmin, "z_max": zmax,
               "low_hops": hops, "hop_z_range": [min(hop_zs), max(hop_zs)] if hop_zs else None, "problems": 0}
    return summary, send, (x, y, travel_z)


def reread_v2(text, travel_z, floor_z, max_draw_feed):
    return _walk(text, travel_z, floor_z, max_draw_feed)[0]


def program_lines_v2(text, travel_z, floor_z, max_feed, firmware_floor):
    _, send, end = _walk(text, travel_z, floor_z, max_feed, firmware_floor)
    return send, end
