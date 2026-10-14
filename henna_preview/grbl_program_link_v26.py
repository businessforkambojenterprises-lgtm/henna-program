"""Studio v26 link: the v22 link + a CALIBRATION JOG (user approval 2026-10-13).

The calibration jog moves Z ONLY, straight up or down, in 10, 1 or 0.1 mm steps (10 mm at F300, the controller's Z max rate; 1 and 0.1 mm at F100), and may go below travel Z -
down to the machine's Z floor (cal_floor, from its registry: 4 mm above the bed on the ball-screw machine). X and Y
jogs stay refused below travel Z (v14 rule, unchanged), so the nozzle can only come down vertically over a spot chosen
at travel height. 10 mm steps down stop 10 mm above the floor (user request 2026-10-13: fewer
presses on the way down); the last part is 1 / 0.1 mm only. Studio offers it only on a machine in calibration mode, with its own confirmation (only paper or a
block on the bed - no hand, no prosthetic, no person). cal_floor None = calibration jog off.
Same execution path as the v14 jog: fresh Idle before, the exact allowed command form, acknowledgement, fresh Idle at
the target after. v13 - v22 files are unchanged.
X/Y NUDGE (user approval 2026-10-13, after the touchscreen was used for the last 6.8 mm over D1): in calibration mode
only, X or Y in 1 or 0.1 mm steps at F300 at ANY height down to the floor, inside the work area - so the tip can be
centred over a dot while it is low, without the touchscreen. Normal Studio X/Y jogs keep the travel-Z rule.
Going UP is always allowed, also from below the floor (a head that the touchscreen put lower can be raised).
"""
import re
import time

from henna_preview import grbl_program_link as gpl
from henna_preview.grbl_program_link import MachineError  # noqa: F401 (re-export)
from henna_preview.grbl_program_link_v22 import GrblProgramLinkV22

CAL_STEPS = (0.1, 1.0, 10.0)
COARSE_STEP_MM, COARSE_STOP_ABOVE_FLOOR_MM = 10.0, 10.0
CAL_FEED, COARSE_FEED = 100, 300            # Z max rate $112 = 300 mm/min (read 2026-10-13); user: Z too slow
CAL_JOG_RE = re.compile(r"^\$J=G21 G90 G53 Z(-?\d+\.\d{3}) F(100|300)$")
NUDGE_STEPS, NUDGE_FEED = (0.1, 1.0), 300
NUDGE_RE = re.compile(r"^\$J=G21 G90 G53 [XY](\d+\.\d{3}) F300$")


def cal_jog_target(start, direction, step_mm, floor):
    if direction not in (1, -1) or step_mm not in CAL_STEPS:
        raise MachineError("Calibration jog: Z only, direction +1 or -1, step 10, 1 or 0.1 mm")
    if floor is None:
        raise MachineError("Calibration jog is off for this machine")
    z = round(start[2] + direction * step_mm, 3)
    if z > gpl.Z_TOP + 1e-9:
        raise MachineError("Calibration jog refused: Z would go above the top (Z 0)")
    if direction < 0 and z < floor - 1e-9:
        raise MachineError(f"Calibration jog refused: Z cannot go below the floor Z {floor:g} (4 mm above the bed)")
    if direction < 0 and step_mm == COARSE_STEP_MM and z < floor + COARSE_STOP_ABOVE_FLOOR_MM - 1e-9:
        raise MachineError(f"10 mm steps stop at Z {floor + COARSE_STOP_ABOVE_FLOOR_MM:g} (10 mm above the floor) - "
                           "go on with 1 mm and 0.1 mm")
    return (start[0], start[1], z)


def nudge_target(start, axis, direction, step_mm, floor, x_max, y_max):
    if axis not in ("X", "Y") or direction not in (1, -1) or step_mm not in NUDGE_STEPS:
        raise MachineError("X/Y nudge: axis X or Y, direction +1 or -1, step 1 or 0.1 mm")
    if floor is None:
        raise MachineError("X/Y nudge is off for this machine (calibration mode only)")
    if start[2] < floor - 1e-9:
        raise MachineError(f"X/Y nudge refused: Z {start[2]:g} is below the floor Z {floor:g} - raise Z first")
    i = "XY".index(axis)
    v = round(start[i] + direction * step_mm, 3)
    if not 0.0 <= v <= (x_max, y_max)[i] + 1e-9:
        raise MachineError(f"X/Y nudge refused: {axis} {v:g} is outside the work area")
    t = list(start)
    t[i] = v
    return tuple(t)


class GrblProgramLinkV26(GrblProgramLinkV22):
    cal_floor = None

    def start_cal_jog(self, direction, step_mm, on_done=None):
        self._config()
        floor = self.cal_floor
        with self._lock:
            if self.busy or self._capture_owner:
                raise MachineError(f"Machine is busy ({self.busy or 'recording'})")
            self._fresh_locked()
            if not self.homed:
                raise MachineError("Home with Z first and then all axes on this connection before jogging")
            start = tuple(self.mpos)
        target = cal_jog_target(start, direction, step_mm, floor)
        feed = COARSE_FEED if step_mm == COARSE_STEP_MM else CAL_FEED
        command = f"$J=G21 G90 G53 Z{target[2]:.3f} F{feed}"
        if not CAL_JOG_RE.fullmatch(command):
            raise MachineError("Calibration jog command outside the allowed form")
        return self._cal_execute(start, target, command, floor,
                                 f"CALIBRATION jog (Z only, floor {floor:g}, F{feed})", on_done)

    def start_cal_nudge(self, axis, direction, step_mm, on_done=None):
        self._config()
        floor = self.cal_floor
        with self._lock:
            if self.busy or self._capture_owner:
                raise MachineError(f"Machine is busy ({self.busy or 'recording'})")
            self._fresh_locked()
            if not self.homed:
                raise MachineError("Home with Z first and then all axes on this connection before jogging")
            start = tuple(self.mpos)
        target = nudge_target(start, axis, direction, step_mm, floor, gpl.X_MAX, gpl.Y_MAX)
        command = f"$J=G21 G90 G53 {axis}{target['XY'.index(axis)]:.3f} F{NUDGE_FEED}"
        if not NUDGE_RE.fullmatch(command):
            raise MachineError("X/Y nudge command outside the allowed form")
        return self._cal_execute(start, target, command, floor,
                                 f"CALIBRATION X/Y nudge ({axis} {direction * step_mm:+g} at Z {start[2]:g}, floor {floor:g})",
                                 on_done)

    def _cal_execute(self, start, target, command, floor, detail, on_done):
        owner = self._new_operation("jog", require_homed=True)

        def action(token):
            with self._lock:
                self._check_token_locked(token)
                self._fresh_locked()
                if not self._at(self.mpos, start):
                    raise MachineError("Machine moved before the jog; Stop and home again")
                if self.cal_floor is None or self.cal_floor != floor:
                    raise MachineError("Calibration jog was switched off; nothing sent")
                self._pending = {"owner": token, "ack": None}
                self._write_locked((command + "\n").encode("ascii"), token[0])
                deadline = time.monotonic() + self._move_timeout
                next_poll = time.monotonic() + self._poll_s
                while self._pending is None or self._pending["ack"] is None:
                    self._check_token_locked(token)
                    now = time.monotonic()
                    if now >= deadline:
                        raise MachineError("No acknowledgment for the jog; Stop and home again")
                    if now >= next_poll:
                        self._write_locked(b"?", token[0])
                        next_poll = now + self._poll_s
                    self._changed.wait(min(0.05, max(0.0, deadline - now)))
                self._pending = None
            line = self._fresh_idle(token, target, self._move_timeout)
            self._event("jog", token, command=command, expected_mpos=list(target), verified_line=line,
                        detail=detail)
        return self._start_worker(owner, action, on_done)
