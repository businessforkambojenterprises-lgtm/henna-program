"""Admin Z jog (Pi, user 2026-10-18: "i want z to move freely under admin panel").

With a valid Admin session the Jog window's Z moves straight up or down by the slider's distance (0.1 to 100 mm),
anywhere between the top (Z 0) and the machine's Z floor (its registry's firmware_z_floor - Z -40 on the ball-screw
machine, the lowest the Z carriage goes) - not only down to travel height. Going UP is always allowed, also from below
the floor. X and Y are not changed here: they still need travel height (the calibration X/Y nudge is for low moves).
The move runs on the calibration jog's path of the v26 link (fresh Idle before, one $J line of the allowed form,
acknowledgement, fresh Idle at the target after) - only the distance and the floor rule differ.
"""
from henna_preview import grbl_program_link as gpl
from henna_preview.grbl_program_link import MachineError
from henna_preview.grbl_program_link_v26 import CAL_JOG_RE

FAST_FROM_MM, FAST_FEED, SLOW_FEED = 5.0, 300, 100      # the calibration jog's feeds: 300 = the Z max rate


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


def start_admin_z(link, direction, step_mm, floor, step_ok, on_done=None):
    link._config()
    with link._lock:
        if link.busy or link._capture_owner:
            raise MachineError(f"Machine is busy ({link.busy or 'recording'})")
        link._fresh_locked()
        if not link.homed:
            raise MachineError("Home with Z first and then all axes on this connection before jogging")
        start = tuple(link.mpos)
    target = admin_z_target(start, direction, step_mm, floor, step_ok)
    feed = FAST_FEED if float(step_mm) >= FAST_FROM_MM else SLOW_FEED
    command = f"$J=G21 G90 G53 Z{target[2]:.3f} F{feed}"
    if not CAL_JOG_RE.fullmatch(command):
        raise MachineError("Admin Z jog command outside the allowed form")
    link.cal_floor = floor                                   # _cal_execute re-checks it just before sending
    return link._cal_execute(start, target, command, floor,
                             f"ADMIN Z jog ({direction * float(step_mm):+g} mm, floor {floor:g}, F{feed})", on_done)
