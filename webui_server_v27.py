"""Henna Studio v27 = v26 + travel clearance over the hand (user approval 2026-10-14).

On the ball-screw machine a palm-up prosthetic reached about 1 mm ABOVE the travel height Z -5 (job
20261014-104829-job), and the design check showed another problem instead. v27 checks, before every design check,
program and run of a prosthetic job:

    travel Z  >=  hand top + max(standoff, hop clearance 5 mm) + 3 mm

(henna_preview/travel_clearance.py) and refuses with the numbers otherwise. Each refusal is logged. Everything else
is v26 unchanged (page, calibration, machines).
"""
import json
import os

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v26 as v26
from henna_preview import travel_clearance as tc
from henna_preview.hop_path import HOP_CLEARANCE_MM

GUARDED = ("/api/studio/check_design", "/api/studio/program", "/api/machine/run")


def _paper_z(c, k):
    """Machine Z of the dot sheet the X/Y calibration was made on (it lies on the bed): head Z - (dot depth - k).
    For the message only; None when the calibration file has no dot-sheet inputs (e.g. the belt-drive's)."""
    try:
        with open(os.path.join(base.ROOT, c["xy_calibration"]), encoding="utf-8") as f:
            inp = json.load(f)["inputs"]
        depths = sorted(d["camera_mm"][2] for d in inp["dots"])
        # the dot sheet's head Z is in the numbers of the calibration's homing; when the Z home switch moved up since
        # (machine_z_home_moved.py, 2026-10-24) the same bed is that much lower in today's numbers
        moved = float(c.get("z_home_moved_mm") or 0.0)
        return float(inp["head_pose_at_capture"]["z"]) - (depths[len(depths) // 2] - k) - moved
    except (OSError, KeyError, TypeError, ValueError, IndexError):
        return None


def clearance(job, standoff_mm):
    """The clearance numbers for this job on the active machine (raises ClearanceError when too tall)."""
    hand = job["steps"].get("hand") or {}
    run = str(hand.get("run") or "")
    name = run.split(":", 1)[1] if run.startswith("web:") else run
    hand_dir = os.path.join(base.OUT_ROOT, name)
    if not (name and base.is_inside(hand_dir, base.OUT_ROOT) and os.path.isfile(os.path.join(hand_dir, "frame_bundle.npz"))):
        return None                                                  # no hand step yet: the step itself refuses
    c = v10.cal()
    with open(os.path.join(base.ROOT, c["z_calibration"]), encoding="utf-8") as f:
        k = float(json.load(f)["result"]["k_mm"])
    head_z = float(job["steps"]["scan"]["mpos"][2])
    top = tc.hand_top(hand_dir, head_z, k, tc.depth_fn_from(c, base.ROOT))
    bed = _paper_z(c, k)
    return tc.check(top["hand_top_contact_z"], c["travel_z"], standoff_mm, HOP_CLEARANCE_MM, bed_z=bed)


def guarded(path, fn):
    def run(params):
        job = v11.job_load((params or {}).get("job"))
        if job.get("subject") == "prosthetic":
            if path == "/api/studio/check_design":
                standoff = params.get("standoff_mm", 4.0)
            else:
                standoff = (job["steps"].get("design") or {}).get("standoff_mm", 4.0)
            try:
                clearance(job, float(standoff))
            except tc.ClearanceError as e:
                v10.log_row(v10.LOG_FILE, 0, "Studio v27: refused - hand too tall for the travel height", None, path,
                            "travel Z clears hand top + max(standoff, hop) + 3 mm", str(e), "refused",
                            (job["steps"].get("scan") or {}).get("name", ""), f"job {job['id']}")
                raise
        return fn(params)
    return run


class Handler(v26.Handler):
    server_version = "HennaStudio/0.27"
    POST_API = dict(v26.Handler.POST_API, **{p: (guarded(p, v26.Handler.POST_API[p][0]), v26.Handler.POST_API[p][1])
                                             for p in GUARDED})
    ERRORS = v26.Handler.ERRORS + (tc.ClearanceError,)


def main():
    v26.Handler = Handler                                            # v26.main serves this handler
    print("Henna Studio v27 - travel clearance over the hand checked before every check, program and run", flush=True)
    v26.main()


if __name__ == "__main__":
    main()
