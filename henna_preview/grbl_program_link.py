"""Studio v14 machine link: v13 homing + fixed scan position, plus running ONE checked hover program.

Approved by the user on 2026-10-10 ("Studio runs it"; no UGS): the hover program that Studio wrote and
re-read is sent by Studio itself, one line at a time (send a line, wait for its ``ok``).

Only these line shapes are ever sent - the same ones the independent re-read accepts:
  G21 | G90 | G53 G0 Z<travel> | G53 G0 X Y (at travel Z) | G53 G1 X Y Z F
Comments are not sent. There are no $, M, S or T lines, and no method to send any other text.
  Pause  = feed hold ``!``      Resume = cycle start ``~`` (only while held)
  Stop   = feed hold + soft reset (inherited from v13) - home again afterwards
Jog (user approval 2026-10-10): ``$J=G21 G90 G53 <axis><target> F300|F1000`` in 0.1 / 1 / 10 mm steps, only
after homing and only at or above travel Z: X/Y need Z >= travel Z, Z may go up to 0 and down to travel Z.
Any ``error:``, ALARM, controller restart, lost connection, missing acknowledgment or an end
position other than the program's own stops the run (feed hold + reset) and requires homing.
No device is opened on import or construction.
"""
import re
import time

from henna_preview.grbl_scan_link import TOL_MM, TRAVEL_Z, GrblScanLink, MachineError  # noqa: F401 (re-export)

JOG_STEPS = (0.1, 1.0, 10.0)
JOG_RE = re.compile(r"^\$J=G21 G90 G53 ([XYZ])(-?\d+\.\d{3}) F(300|1000)$")
Z_TOP = 0.0
_G0Z = re.compile(r"^G53 G0 Z(-?\d+\.\d+)$")
_G0XY = re.compile(r"^G53 G0 X(\d+\.\d+) Y(\d+\.\d+)$")
_G1 = re.compile(r"^G53 G1 X(\d+\.\d+) Y(\d+\.\d+) Z(-?\d+\.\d+) F(\d+(?:\.\d+)?)$")
_COMMENT = re.compile(r"^\([^()$;]*\)$")
X_MAX, Y_MAX = 225.0, 176.0
MAX_LINES = 20000


def program_lines(text, travel_z, floor_z, max_feed, firmware_floor):
    """The lines to send (comments dropped) and the machine position the program must end at.

    A second, link-side allowlist (the server has already run the independent re-read).
    """
    send, z, x, y = [], None, None, None
    for n, line in enumerate(text.splitlines(), start=1):
        if not line or _COMMENT.match(line):
            continue
        if line in ("G21", "G90"):
            if z is not None:
                raise MachineError(f"line {n}: {line} after the first move")
            send.append(line)
            continue
        m = _G0Z.match(line)
        if m:
            if float(m.group(1)) != travel_z:
                raise MachineError(f"line {n}: rapid Z is not travel Z {travel_z}")
            z = travel_z
            send.append(line)
            continue
        m = _G0XY.match(line)
        if m:
            if z != travel_z:
                raise MachineError(f"line {n}: sideways rapid move below travel Z")
            x, y = float(m.group(1)), float(m.group(2))
            if not (0 <= x <= X_MAX and 0 <= y <= Y_MAX):
                raise MachineError(f"line {n}: X/Y outside the machine")
            send.append(line)
            continue
        m = _G1.match(line)
        if m:
            x, y, zz, f = (float(v) for v in m.groups())
            if z is None:
                raise MachineError(f"line {n}: drawing move before rising to travel Z")
            if not (0 <= x <= X_MAX and 0 <= y <= Y_MAX):
                raise MachineError(f"line {n}: X/Y outside the machine")
            if zz < max(floor_z, firmware_floor) - 1e-9 or zz >= travel_z:
                raise MachineError(f"line {n}: Z {zz} outside floor {floor_z} .. travel {travel_z}")
            if f > max_feed:
                raise MachineError(f"line {n}: feed {f} above {max_feed}")
            z = zz
            send.append(line)
            continue
        raise MachineError(f"line {n}: not an allowed line")
    if send[:2] != ["G21", "G90"] or len(send) < 4:
        raise MachineError("program must start with G21 and G90 and contain moves")
    if z != travel_z or x is None:
        raise MachineError("program does not end at travel Z")
    if len(send) > MAX_LINES:
        raise MachineError("program is too long")
    return send, (x, y, travel_z)


class GrblProgramLink(GrblScanLink):
    def __init__(self, *args, ack_timeout=60.0, end_timeout=180.0, idle_away_s=1.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._ack_timeout, self._end_timeout, self._idle_away_s = ack_timeout, end_timeout, idle_away_s
        self._run = None

    # ---- status ----
    def status(self):
        with self._lock:
            out = super().status()
            r = self._run
            if r is not None:
                end = r["t_end"] or time.monotonic()
                out["program"] = {"name": r["name"], "total": r["total"], "done": r["acked"],
                                  "paused": r["paused"], "state": r["state"], "error": r["error"],
                                  "elapsed_s": round(end - r["t0"], 1)}
            else:
                out["program"] = None
            return out

    # ---- run ----
    def start_program(self, name, lines, end_mpos, on_done=None):
        self._config()                       # the approved travel Z and feeds are unchanged
        lines = list(lines)
        if not lines or any(not isinstance(s, str) or "\n" in s or "\r" in s for s in lines):
            raise MachineError("Program lines are invalid")
        end_mpos = tuple(float(v) for v in end_mpos)
        owner = self._new_operation("program", require_homed=True)
        with self._lock:
            self.capture_ready = False
            self._run = {"name": str(name), "total": len(lines), "acked": 0, "paused": False, "state": "running",
                         "error": None, "stop_requested": False, "t0": time.monotonic(), "t_end": None}
            run = self._run

        def action(token):
            self._fresh_idle(token)
            self._event("program_start", token, command=str(name), detail=f"{len(lines)} lines; Studio run")
            for line in lines:
                self._send_line(token, line)
                with self._lock:
                    run["acked"] += 1
            verified = self._wait_end(token, end_mpos)
            self._event("program_complete", token, command=str(name), expected_mpos=list(end_mpos),
                        verified_line=verified, detail="program finished at its end position")

        def done(error):
            with self._lock:
                run["t_end"] = time.monotonic()
                run["paused"] = False
                run["error"] = error
                run["state"] = "finished" if error is None else "stopped" if run["stop_requested"] else "failed"
            if on_done:
                on_done(error)
        return self._start_worker(owner, action, done)

    def _held_locked(self):
        return self._run is not None and (self._run["paused"] or self.state == "Hold")

    def _send_line(self, owner, line):
        with self._lock:
            self._check_token_locked(owner)
            next_poll = time.monotonic()
            while self._run["paused"]:                 # while paused, queue nothing new
                self._check_token_locked(owner)
                if time.monotonic() >= next_poll:
                    self._write_locked(b"?", owner[0])
                    next_poll = time.monotonic() + self._poll_s
                self._changed.wait(0.05)
            if self.state not in ("Idle", "Run", "Hold", "Jog"):
                raise MachineError(f"Machine is {self.state}; run stopped")
            self._pending = {"owner": owner, "ack": None}
            self._write_locked((line + "\n").encode("ascii"), owner[0])
            deadline = time.monotonic() + self._ack_timeout
            next_poll = time.monotonic() + self._poll_s
            while True:
                self._check_token_locked(owner)
                if self._pending is not None and self._pending["ack"] is not None:
                    self._pending = None
                    return
                now = time.monotonic()
                if self._held_locked():
                    deadline = now + self._ack_timeout          # a pause never times out
                if now >= deadline:
                    raise MachineError(f"No acknowledgment for '{line}'; run stopped")
                if now >= next_poll:
                    self._write_locked(b"?", owner[0])
                    next_poll = now + self._poll_s
                self._changed.wait(min(0.05, max(0.0, deadline - now)))

    def _wait_end(self, owner, end_mpos):
        """Fresh Idle at the program's own end position, requested after the last acknowledgment."""
        with self._lock:
            self._check_token_locked(owner)
            after = self._status_seq
            self._write_locked(b"?", owner[0])
            deadline = time.monotonic() + self._end_timeout
            next_poll = time.monotonic() + self._poll_s
            idle_away_since = None
            while True:
                self._check_token_locked(owner)
                now = time.monotonic()
                fresh = self._status_seq > after and now - self.t_status <= self._fresh_s
                if fresh and self.state == "Idle":
                    if self._at(self.mpos, end_mpos):
                        return self.line
                    idle_away_since = idle_away_since or now
                    if now - idle_away_since > self._idle_away_s:
                        raise MachineError(f"Machine stopped at {self.mpos}, not at the program end {list(end_mpos)}")
                else:
                    idle_away_since = None
                if self._held_locked():
                    deadline = now + self._end_timeout
                if now >= deadline:
                    raise MachineError("Program did not finish in time; run stopped")
                if now >= next_poll:
                    self._write_locked(b"?", owner[0])
                    next_poll = now + self._poll_s
                self._changed.wait(min(0.05, max(0.0, deadline - now)))

    # ---- jog (always at or above travel Z) ----
    @staticmethod
    def jog_target(start, axis, direction, step_mm):
        if axis not in ("X", "Y", "Z") or direction not in (1, -1) or step_mm not in JOG_STEPS:
            raise MachineError("Jog refused: axis X, Y or Z; direction +1 or -1; step 0.1, 1 or 10 mm")
        i = "XYZ".index(axis)
        target = list(start)
        target[i] = round(start[i] + direction * step_mm, 3)
        if axis == "Z":
            if target[2] > Z_TOP + 1e-9:
                raise MachineError("Jog refused: Z would go above the top (Z 0)")
            if direction < 0 and target[2] < TRAVEL_Z - 1e-9:
                raise MachineError(f"Jog refused: Z cannot go below travel height Z {TRAVEL_Z:g} from the page "
                                   "(use a smaller step, or let a checked program lower the nozzle)")
        else:
            if start[2] < TRAVEL_Z - TOL_MM:
                raise MachineError(f"Raise Z to travel height (Z {TRAVEL_Z:g} or higher) before moving X or Y")
            if not 0.0 <= target[i] <= (X_MAX if axis == "X" else Y_MAX):
                raise MachineError("Jog refused: outside the machine")
        return tuple(target)

    def start_jog(self, axis, direction, step_mm, on_done=None):
        self._config()
        with self._lock:
            if self.busy or self._capture_owner:
                raise MachineError(f"Machine is busy ({self.busy or 'recording'})")
            self._fresh_locked()
            if not self.homed:
                raise MachineError("Home with Z first and then all axes on this connection before jogging")
            start = tuple(self.mpos)
        target = self.jog_target(start, axis, direction, step_mm)
        i = "XYZ".index(axis)
        command = f"$J=G21 G90 G53 {axis}{target[i]:.3f} F{300 if axis == 'Z' else 1000}"
        if not JOG_RE.fullmatch(command):
            raise MachineError("Jog command outside the allowed form")
        owner = self._new_operation("jog", require_homed=True)

        def action(token):
            with self._lock:
                self._check_token_locked(token)
                self._fresh_locked()
                if not self._at(self.mpos, start):
                    raise MachineError("Machine moved before the jog; Stop and home again")
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
                        detail="jog from Studio (at or above travel Z)")
        return self._start_worker(owner, action, on_done)

    # ---- pause / resume / stop ----
    def pause(self):
        with self._lock:
            if self.busy != "program" or self._run is None or self._run["state"] != "running":
                raise MachineError("No program is running")
            self._write_locked(b"!", self._generation)
            self._run["paused"] = True
            self._changed.notify_all()
        self._event("program_pause", command="! (feed hold)")
        return self.status()

    def resume(self):
        with self._lock:
            if self.busy != "program" or self._run is None or not self._run["paused"]:
                raise MachineError("The program is not paused")
            self._fresh_locked(("Hold", "Idle"))
            self._write_locked(b"~", self._generation)
            self._run["paused"] = False
            self._changed.notify_all()
        self._event("program_resume", command="~ (cycle start)")
        return self.status()

    def stop(self):
        with self._lock:
            if self._run is not None and self._run["state"] == "running":
                self._run["stop_requested"] = True
        return super().stop()
