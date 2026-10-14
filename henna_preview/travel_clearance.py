"""Travel clearance over the hand (Studio v27, user approval 2026-10-14).

Why: on the ball-screw machine the travel height Z -5 is only about 41 mm above the bed, and a prosthetic hand lying
palm-up reached about 42 mm (job 20261014-104829-job). Every sideways move at travel Z would then cross the hand.
The step 21-23 rule (travel Z >= hand top + standoff) only ran when a design had no other problem, and allows a
4 mm gap at travel speed. This rule, for every check, program and run:

    travel Z  >=  hand top + max(standoff, hop clearance) + MARGIN_MM

hand top = the highest measured skin inside the grown landmark hull of the hand-detection frame (the step 19
function, the same depth correction the planner uses). Refused otherwise, with the numbers.
"""
import json
import os

import numpy as np

MARGIN_MM = 3.0


class ClearanceError(ValueError):
    pass


def hand_top(hand_dir, head_z, k_mm, depth_fn):
    from step19_hand_path_avg import hand_top_from_raw
    with np.load(os.path.join(hand_dir, "frame_bundle.npz")) as b:
        raw, scale, lm = b["depth_raw_aligned"], float(b["depth_scale_m_per_unit"]), b["landmarks_px"]
    return hand_top_from_raw(raw, scale, lm, k_mm, head_z, depth_fn)


def check(top_z, travel_z, standoff_mm, hop_mm, bed_z=None):
    """-> dict with the numbers; raises ClearanceError when travel Z does not clear the hand."""
    need = round(top_z + max(float(standoff_mm), float(hop_mm)) + MARGIN_MM, 2)
    out = {"hand_top_z": round(top_z, 2), "travel_z": float(travel_z), "needed_travel_z": need,
           "gap_mm": round(float(travel_z) - top_z, 2), "margin_mm": MARGIN_MM,
           "hand_height_above_bed_mm": round(top_z - bed_z, 1) if bed_z is not None else None}
    if top_z > 0:                                     # higher than the nozzle tip itself at the top: not skin
        raise ClearanceError(
            f"Something is between the camera and the hand (it reads as Z {top_z:.1f}, higher than the nozzle tip can "
            "be) - most likely the cable. Move it out of the camera's view and scan again.")
    if float(travel_z) < need - 1e-9:
        above = f" (about {out['hand_height_above_bed_mm']:g} mm above the bed)" if bed_z is not None else ""
        raise ClearanceError(
            f"The hand is too tall for this machine: its highest point is at Z {top_z:.1f}{above}, and the travel "
            f"height Z {float(travel_z):g} must be at least {max(float(standoff_mm), float(hop_mm)) + MARGIN_MM:g} mm "
            f"above it (Z {need:g} needed). Lay the hand flatter (palm down) or give the machine more Z room.")
    return out


def depth_fn_from(cal, root):
    """The planner's depth correction for this calibration (identity when it names none)."""
    from henna_preview.depth_scale import correct_depth
    if not cal.get("depth_scale"):
        return lambda d: d
    with open(os.path.join(root, cal["depth_scale"]), encoding="utf-8") as f:
        ds = json.load(f)
    g, d_ref = float(ds["g"]), float(ds["d_ref_mm"])
    return lambda d: correct_depth(d, g, d_ref)
