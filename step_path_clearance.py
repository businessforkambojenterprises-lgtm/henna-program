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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in SCRIPTS:
        print(__doc__)
        return 2
    mod = importlib.import_module(SCRIPTS[argv[0]])
    rest = argv[1:]
    cfg_path = rest[rest.index("--config") + 1] if "--config" in rest[:-1] else None
    cfg = {}
    if cfg_path:                                          # no config: the script's own argument check answers
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    mod.hand_top_from_raw = wrap(mod.hand_top_from_raw, path_top(cfg))
    sys.argv = [mod.__file__] + rest
    mod.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
