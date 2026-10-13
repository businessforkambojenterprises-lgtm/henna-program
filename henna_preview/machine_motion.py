"""Per-machine motion values for the machine link (Studio v25, user approval 2026-10-13).

Until v24 the belt-drive machine's values were fixed in the code: the scan position and its route
(grbl_scan_link.POSE / ROUTE / COMMANDS), travel Z (grbl_scan_link and grbl_program_link .TRAVEL_Z) and the
Jog / program box (grbl_program_link, program_reread, program_reread_v2 .X_MAX / .Y_MAX). Those modules read
them at call time, so apply() sets them from the ACTIVE machine's registry values - only while the machine is
disconnected (Studio enforces that). BELT_DRIVE holds the original values, so the belt-drive machine gets
exactly what it always had (tests compare every command byte for byte).
"""
from henna_preview import grbl_program_link as gpl
from henna_preview import grbl_scan_link as gsl
from henna_preview import program_reread as rr1
from henna_preview import program_reread_v2 as rr2

BELT_DRIVE = {"POSE": gsl.POSE, "TRAVEL_Z": gsl.TRAVEL_Z, "ROUTE": gsl.ROUTE, "COMMANDS": gsl.COMMANDS,
              "X_MAX": gpl.X_MAX, "Y_MAX": gpl.Y_MAX}


def route_for(pose, travel_z):
    """The fixed three-stage route, as v13 wrote it for the belt-drive machine, for any scan position."""
    return (("raise_to_travel", f"$J=G21 G90 G53 Z{travel_z:.3f} F300"),
            ("move_xy", f"$J=G21 G90 G53 X{pose[0]:.3f} Y{pose[1]:.3f} F1000"),
            ("lower_to_capture", f"$J=G21 G90 G53 Z{pose[2]:.3f} F300"))


def values_for(motion, work_area):
    pose = tuple(float(v) for v in motion["scan_pose_mpos"])
    travel = float(motion["travel_z"])
    if len(pose) != 3 or pose[2] > travel + 1e-9:
        raise ValueError("scan position must be at or below travel Z")
    if not (float(motion["firmware_z_floor"]) < pose[2]):
        raise ValueError("the Z floor must be below the scan position")
    route = route_for(pose, travel)
    return {"POSE": pose, "TRAVEL_Z": travel, "ROUTE": route,
            "COMMANDS": frozenset(["$HZ", "$H"] + [c for _, c in route]),
            "X_MAX": float(work_area[0]), "Y_MAX": float(work_area[1])}


def apply(values):
    gsl.POSE, gsl.TRAVEL_Z, gsl.ROUTE, gsl.COMMANDS = values["POSE"], values["TRAVEL_Z"], values["ROUTE"], values["COMMANDS"]
    gpl.TRAVEL_Z = values["TRAVEL_Z"]
    gpl.X_MAX = rr1.X_MAX = rr2.X_MAX = values["X_MAX"]
    gpl.Y_MAX = rr1.Y_MAX = rr2.Y_MAX = values["Y_MAX"]


def current():
    return {"POSE": gsl.POSE, "TRAVEL_Z": gsl.TRAVEL_Z, "ROUTE": gsl.ROUTE, "COMMANDS": gsl.COMMANDS,
            "X_MAX": gpl.X_MAX, "Y_MAX": gpl.Y_MAX}
