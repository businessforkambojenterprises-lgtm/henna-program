"""Run step 22 or step 23 with the travel clearance over the NOZZLE'S PATH (Studio v29, user 2026-10-18: "do 2").

    python step_path_clearance.py step22 --config <cfg> [--write-gcode]
    python step_path_clearance.py step23 --config <cfg> --write-gcode

step22_design_hover_test.py and step23_design_hop_program.py are approved and stay unchanged. Both refuse when
"travel Z < hand top + standoff", with the hand top of the WHOLE hand (step19.hand_top_from_raw) - on a real person
usually the wrist or forearm, which the nozzle never crosses. Studio v29 measures the top of the surface under the
nozzle's own path (henna_preview/path_clearance.py, design region + way from the scan position, 8 mm margin) and passes
it in HENNA_PATH_TOP = {"recording": <file name>, "top_z": <Z>, "basis": <text>}. This runner loads the script, lets its
hand_top_from_raw return that top instead - only when the recording named in HENNA_PATH_TOP is the config's own; else
the whole-hand top as before - and keeps the whole-hand value in the report (whole_hand_top_contact_z).
Everything else in the scripts (planning, floors, hops, the re-read, the files they write) is unchanged.

ROUTE (user 2026-10-22, "build step 2"): Studio also passes the route of the first travel move in
HENNA_APPROACH = {"recording": <file name>, "target": [x, y], "waypoints": [[x, y], ...]} (henna_preview/travel_route.py).
For step 23, when the recording is the config's own and the target is the program's first stroke start, its
program_with_hops gets the waypoints written as "G53 G0 X Y" lines at travel Z right after the first rise - the same
legs Studio's path check measured. Anything else: the straight move as before (and Studio refuses a run whose route
differs from the one it checked).
JUMPS (2026-10-24, "make the pen's jumps go around the tall mound"): HENNA_APPROACH also carries "gaps" -
[{"from", "to", "waypoints"}] for the jumps between strokes that go round tall skin; gap_routes writes them at travel Z.
"""
import importlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
SCRIPTS = {"step22": "step22_design_hover_test", "step23": "step23_design_hop_program"}


def path_top(cfg, env=None):
    """The path top handed over by Studio for this config's recording, or None."""
    raw = (env if env is not None else os.environ).get("HENNA_PATH_TOP", "")
    try:
        info = json.loads(raw) if raw else {}
    except ValueError:
        return None
    rec = os.path.basename(str((cfg.get("hand_capture") or {}).get("recording", "")))
    if info.get("recording") and rec and os.path.basename(str(info["recording"])) == rec and info.get("top_z") is not None:
        return info
    return None


def wrap(original, info):
    def hand_top_from_raw(*args, **kwargs):
        top = original(*args, **kwargs)
        if info is None:
            return top
        return dict(top, whole_hand_top_contact_z=top["hand_top_contact_z"], hand_top_contact_z=float(info["top_z"]),
                    basis=info.get("basis", "nozzle travel path"))
    return hand_top_from_raw


def approach(cfg, env=None):
    """The route handed over by Studio for this config's recording, or None."""
    raw = (env if env is not None else os.environ).get("HENNA_APPROACH", "")
    try:
        info = json.loads(raw) if raw else {}
    except ValueError:
        return None
    rec = os.path.basename(str((cfg.get("hand_capture") or {}).get("recording", "")))
    if (info.get("recording") and rec and os.path.basename(str(info["recording"])) == rec
            and (info.get("waypoints") or info.get("gaps"))):
        return info
    return None


def _near(a, b, tol=0.01):
    return abs(float(a[0]) - float(b[0])) <= tol and abs(float(a[1]) - float(b[1])) <= tol


def gap_routes(lines, lifted, gaps, travel_z):
    """The jumps between strokes that Studio's path check routed round tall skin (2026-10-24, HENNA_APPROACH "gaps"):
    for the jump after stroke i whose ends are the checked ones, its low hop (or rise) becomes a rise to travel Z and
    the waypoints follow it as G53 G0 X Y lines at travel Z. A gap that does not match is left straight - Studio then
    refuses the run (its route check compares the program with what it checked)."""
    if not gaps:
        return lines
    rise = f"G53 G0 Z{float(travel_z):.3f}"
    out, stroke, last_g1 = [], -1, None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("G53 G1 "):
            last_g1 = ln
        if (ln.startswith("G53 G0 Z") and last_g1 is not None and i + 1 < len(lines)
                and lines[i + 1].startswith("G53 G0 X")):
            stroke += 1                                   # the jump after stroke number `stroke` (0-based)
            if stroke + 1 < len(lifted):
                end, start = lifted[stroke][-1], lifted[stroke + 1][0]
                g = next((g for g in gaps if _near(g["from"], end) and _near(g["to"], start)), None)
                if g:
                    out.append(rise)
                    out += [f"G53 G0 X{float(x):.3f} Y{float(y):.3f}" for x, y in g["waypoints"]]
                    i += 1
                    continue
        out.append(ln)
        i += 1
    return out


def wrap_program(original, info):
    """program_with_hops with the route's waypoints after the first rise to travel Z (first stroke start must match)
    and the routed jumps between strokes (gap_routes)."""
    def program_with_hops(lifted, hops, p, header):
        lines = original(lifted, hops, p, header)
        if info is None or not len(lifted):
            return lines
        first = lifted[0][0]
        tx, ty = info["target"]
        if abs(float(first[0]) - float(tx)) > 0.01 or abs(float(first[1]) - float(ty)) > 0.01:
            return lines                                   # not the design Studio checked: the straight move
        rise = f"G53 G0 Z{p['travel_z']:.3f}"
        i = lines.index(rise) + 1
        way = [f"G53 G0 X{float(x):.3f} Y{float(y):.3f}" for x, y in info["waypoints"]]
        return gap_routes(lines[:i] + way + lines[i:], lifted, info.get("gaps"), p["travel_z"])
    return program_with_hops


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in SCRIPTS:
        print(__doc__)
        return 2
    from henna_preview.machine_motion import apply_active_work_area
    apply_active_work_area(HERE)                          # the active machine's measured X/Y box (2026-10-24)
    mod = importlib.import_module(SCRIPTS[argv[0]])
    rest = argv[1:]
    cfg_path = rest[rest.index("--config") + 1] if "--config" in rest[:-1] else None
    cfg = {}
    if cfg_path:                                          # no config: the script's own argument check answers
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    mod.hand_top_from_raw = wrap(mod.hand_top_from_raw, path_top(cfg))
    if hasattr(mod, "program_with_hops"):
        mod.program_with_hops = wrap_program(mod.program_with_hops, approach(cfg))
    sys.argv = [mod.__file__] + rest
    mod.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
