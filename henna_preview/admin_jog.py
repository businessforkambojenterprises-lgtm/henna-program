"""Admin jog (Pi, user 2026-10-18: "i want z to move freely under admin panel", then "now x and y should aslo move no
restriction in admin panel. everyrthing should be open and accesible").

With a valid Admin session the Jog window's Z moves straight up or down by the slider's distance (0.1 to 100 mm),
anywhere between the top (Z 0) and the machine's Z floor (its registry's firmware_z_floor - Z -40 on the ball-screw
machine, the lowest the Z carriage goes) - not only down to travel height. Going UP is always allowed, also from below
the floor. X and Y move by the slider's distance at ANY height down to the floor (not only at travel height), inside
the machine's work area; below travel height at F300 (the calibration nudge's feed), at travel height or above at F1000.
Kept: the no-hand tick (the route), the work area, the Z floor (no X/Y while the head is below it - raise Z first).
Every move runs on the calibration jog's path of the v26 link (fresh Idle before, one $J line of the allowed form,
acknowledgement, fresh Idle at the target after) - only the distance and the floor rule differ.
"""
import re

from henna_preview import grbl_program_link as gpl
from henna_preview.grbl_program_link import MachineError
from henna_preview.grbl_program_link_v26 import CAL_JOG_RE

FAST_FROM_MM, FAST_FEED, SLOW_FEED = 5.0, 300, 100      # the calibration jog's feeds: 300 = the Z max rate
XY_LOW_FEED, XY_FEED = 300, 1000                        # below / at or above travel height
XY_JOG_RE = re.compile(r"^\$J=G21 G90 G53 [XY](\d+\.\d{3}) F(300|1000)$")


def admin_z_target(start, direction, step_mm, floor, step_ok):
    if type(direction) is not int or direction not in (1, -1) or isinstance(step_mm, bool) or not step_ok(step_mm):
        raise MachineError("Admin Z jog: direction +1 or -1, distance 0.1 to 100 mm in 0.1 mm steps")
    if floor is None:
        raise MachineError("This machine has no Z floor in its settings - Admin Z jog is off")
    z = round(start[2] + direction * float(step_mm), 3)
    if z > gpl.Z_TOP + 1e-9:
        raise MachineError("Jog refused: Z would go above the top (Z 0)")
    if direction < 0 and z < floor - 1e-9:
        raise MachineError(f"Jog refused: Z cannot go below the floor Z {floor:g} - use a smaller distance")
    return (start[0], start[1], z)


def admin_xy_target(start, axis, direction, step_mm, floor, x_max, y_max, step_ok):
    if axis not in ("X", "Y") or type(direction) is not int or direction not in (1, -1) or isinstance(step_mm, bool) \
            or not step_ok(step_mm):
        raise MachineError("Admin X/Y jog: axis X or Y, direction +1 or -1, distance 0.1 to 100 mm in 0.1 mm steps")
    if floor is None:
        raise MachineError("This machine has no Z floor in its settings - Admin X/Y jog is off")
    if start[2] < floor - 1e-9:
        raise MachineError(f"Jog refused: the head is below the floor Z {floor:g} - raise Z first")
    i = "XY".index(axis)
    v = round(start[i] + direction * float(step_mm), 3)
    if not 0.0 <= v <= (x_max, y_max)[i] + 1e-9:
        raise MachineError(f"Jog refused: {axis} {v:g} is outside the work area (0 to {(x_max, y_max)[i]:g})")
    t = list(start)
    t[i] = v
    return tuple(t)


def _start(link):
    link._config()
    with link._lock:
        if link.busy or link._capture_owner:
            raise MachineError(f"Machine is busy ({link.busy or 'recording'})")
        link._fresh_locked()
        if not link.homed:
            raise MachineError("Home with Z first and then all axes on this connection before jogging")
        return tuple(link.mpos)


def start_admin_xy(link, axis, direction, step_mm, floor, travel_z, x_max, y_max, step_ok, on_done=None):
    start = _start(link)
    target = admin_xy_target(start, axis, direction, step_mm, floor, x_max, y_max, step_ok)
    feed = XY_FEED if start[2] >= travel_z - 1e-9 else XY_LOW_FEED
    command = f"$J=G21 G90 G53 {axis}{target['XY'.index(axis)]:.3f} F{feed}"
    if not XY_JOG_RE.fullmatch(command):
        raise MachineError("Admin X/Y jog command outside the allowed form")
    link.cal_floor = floor
    return link._cal_execute(start, target, command, floor,
                             f"ADMIN X/Y jog ({axis} {direction * float(step_mm):+g} mm at Z {start[2]:g}, F{feed})",
                             on_done)


def start_admin_z(link, direction, step_mm, floor, step_ok, on_done=None):
    start = _start(link)
    target = admin_z_target(start, direction, step_mm, floor, step_ok)
    feed = FAST_FEED if float(step_mm) >= FAST_FROM_MM else SLOW_FEED
    command = f"$J=G21 G90 G53 Z{target[2]:.3f} F{feed}"
    if not CAL_JOG_RE.fullmatch(command):
        raise MachineError("Admin Z jog command outside the allowed form")
    link.cal_floor = floor                                   # _cal_execute re-checks it just before sending
    return link._cal_execute(start, target, command, floor,
                             f"ADMIN Z jog ({direction * float(step_mm):+g} mm, floor {floor:g}, F{feed})", on_done)
