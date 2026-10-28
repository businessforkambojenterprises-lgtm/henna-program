"""Narrow GRBL link for v13: observed homing and one fixed, staged scan pose.

No device is opened on import/construction. There is no public arbitrary-command
API. A command's ``ok`` is acceptance only; every stage also requires a newly
requested, post-ACK Idle report at its intended machine position.
"""
import collections
from contextlib import contextmanager
import math
import re
import threading
import time


BAUD = 115200
POSE = (145.8, 42.6, -40.0)
TRAVEL_Z = -20.0
TOL_MM = 0.05
ROUTE = (
    ("raise_to_travel", "$J=G21 G90 G53 Z-20.000 F300"),
    ("move_xy", "$J=G21 G90 G53 X145.800 Y42.600 F1000"),
    ("lower_to_capture", "$J=G21 G90 G53 Z-40.000 F300"),
)
# Park at the home corner after a finished run (user 2026-10-28: "after every work finishing machine should move to
# home position"): up to travel Z, then X0 Y0 - where homing leaves the head - at travel height. No homing cycle.
PARK_XY = (0.0, 0.0)
PARK_COMMAND = "$J=G21 G90 G53 X0.000 Y0.000 F1000"
COMMANDS = frozenset(["$HZ", "$H", PARK_COMMAND] + [command for _, command in ROUTE])
STATUS_RE = re.compile(r"^<([A-Za-z]+)(?::\d+)?\|MPos:(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)(?:\|[^<>]*)?>$")


class MachineError(RuntimeError):
    pass


def _open_serial(port):
    import serial
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout, ser.write_timeout = port, BAUD, 0.1, 1.0
    ser.dtr = ser.rts = False
    try:
        ser.open()
    except serial.SerialException as exc:
        raise MachineError(f"Could not open {port}: {exc}. Disconnect UGS first.") from exc
    return ser


class GrblScanLink:
    def __init__(self, calibration_provider, opener=_open_serial, on_event=None,
                 poll_s=0.25, fresh_s=1.5, homing_timeout=150.0,
                 move_timeout=60.0, status_timeout=3.0):
        self._calibration = calibration_provider
        self._open = opener
        self._on_event = on_event
        self._poll_s, self._fresh_s = poll_s, fresh_s
        self._homing_timeout, self._move_timeout = homing_timeout, move_timeout
        self._status_timeout = status_timeout
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._connect_lock = threading.RLock()
        self._stop_call_lock = threading.Lock()
        self._event_lock = threading.RLock()
        self._ser = None
        self._generation = self._token = self._rx_seq = self._status_seq = 0
        self._pending = None
        self._busy_owner = None
        self._capture_owner = None
        self._stop_event = threading.Event()
        self.port = self.state = self.mpos = self.line = None
        self.t_status = 0.0
        self.busy = None
        self.homed = self.capture_ready = self.restarted = False
        self.capture_error = None
        self.messages = collections.deque(maxlen=30)

    def _config(self):
        cfg = self._calibration()
        try:
            pose = tuple(float(x) for x in cfg["capture_pose_mpos"])
            values = (float(cfg["travel_z"]), float(cfg["draw_feed_mm_min"]),
                      float(cfg["travel_feed_mm_min"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise MachineError("Approved capture calibration is missing or invalid") from exc
        if pose != POSE or values != (TRAVEL_Z, 300.0, 1000.0):
            raise MachineError("Capture route refused: approved pose, travel Z or feeds changed")
        return pose

    def _invalidate_locked(self, reason):
        self._token += 1
        self.homed = self.capture_ready = False
        self.capture_error = str(reason)
        self.messages.append(str(reason))
        self._changed.notify_all()

    def _fresh_locked(self, states=("Idle",)):
        if self._ser is None:
            raise MachineError("Machine not connected")
        if not self.line or time.monotonic() - self.t_status > self._fresh_s:
            raise MachineError("No fresh machine-position status; wait for a new report")
        if self.state not in states:
            raise MachineError(f"Machine is {self.state}; expected {' or '.join(states)}")

    def _check_token_locked(self, owner):
        if self._ser is None or owner != (self._generation, self._token):
            raise MachineError(self.capture_error or "Operation interrupted; home again")

    @staticmethod
    def _at(actual, target):
        return actual is not None and all(abs(a - b) <= TOL_MM + 1e-9 for a, b in zip(actual, target))

    def status(self):
        with self._lock:
            age = time.monotonic() - self.t_status if self.t_status else None
            ready = (self.capture_ready and self.homed and self._ser is not None and
                     self.state == "Idle" and age is not None and age <= self._fresh_s and self._at(self.mpos, POSE))
            return {"connected": self._ser is not None, "port": self.port, "state": self.state,
                    "mpos": list(self.mpos) if self.mpos is not None else None, "line": self.line,
                    "age_s": round(age, 2) if age is not None else None,
                    "busy": self.busy or ("recording" if self._capture_owner else None),
                    "homed": self.homed, "capture_ready": bool(ready), "capture_error": self.capture_error,
                    "restarted": self.restarted, "messages": list(self.messages)[-8:]}

    def status_line(self):
        with self._lock:
            self._fresh_locked()
            return self.line

    def _event(self, event, owner=None, **extra):
        with self._lock:
            if owner is not None:
                self._check_token_locked(owner)
            data = {"event": event, "connection_id": self._generation, **self.status(), **extra}
        if self._on_event:
            with self._event_lock:
                self._on_event(data)
        if owner is not None:
            with self._lock:
                self._check_token_locked(owner)

    def _write_locked(self, data, generation):
        if self._ser is None or generation != self._generation:
            raise MachineError("Connection changed; command cancelled")
        try:
            written = self._ser.write(data)
            if written != len(data):
                raise MachineError("Incomplete serial write")
        except Exception as exc:
            self._invalidate_locked(f"Serial write failed: {exc}")
            raise MachineError(f"Serial write failed: {exc}") from exc

    def connect(self, port="COM7", wait_s=3.0):
        with self._connect_lock:
            with self._lock:
                if self._ser is not None:
                    if port != self.port:
                        raise MachineError("Disconnect the current port before choosing another")
                    return self.status()
                if self._capture_owner:
                    raise MachineError("Wait for recording to end before reconnecting")
            ser = self._open(port)
            with self._lock:
                self._generation += 1
                self._token += 1
                generation = self._generation
                self._ser, self.port = ser, port
                self._stop_event = stop_event = threading.Event()
                self.state = self.mpos = self.line = None
                self.t_status = 0.0
                self._pending = None
                self.busy = self._busy_owner = None
                self.homed = self.capture_ready = self.restarted = False
                self.capture_error = None
                threading.Thread(target=self._reader, args=(ser, generation, stop_event), daemon=True).start()
                threading.Thread(target=self._poller, args=(generation, stop_event), daemon=True).start()
                deadline = time.monotonic() + wait_s
                while self._ser is ser and self.line is None and time.monotonic() < deadline:
                    self._changed.wait(min(0.1, max(0, deadline - time.monotonic())))
                if self._ser is ser and self.line:
                    return self.status()
            self.disconnect()
            raise MachineError(f"No valid machine-position response from {port}")

    def disconnect(self):
        with self._connect_lock:
            with self._lock:
                if self.busy or self._capture_owner:
                    raise MachineError(f"Cannot disconnect while {self.busy or 'recording'}; press Stop and wait for recording to end")
                ser, self._ser = self._ser, None
                self._stop_event.set()
                self._generation += 1
                self._invalidate_locked("Disconnected; home again after reconnecting")
                self.state = self.mpos = self.line = None
                self.t_status = 0.0
                self._pending = None
            if ser is not None:
                try:
                    ser.close()
                except Exception:
                    pass
        return self.status()

    def _poller(self, generation, stop_event):
        while not stop_event.is_set():
            with self._lock:
                if generation != self._generation or self._ser is None:
                    return
                if self.busy in (None, "recording"):
                    if self._capture_owner and time.monotonic() - self.t_status > self._fresh_s:
                        self._invalidate_locked("Position status became stale during recording")
                    try:
                        self._write_locked(b"?", generation)
                    except MachineError:
                        return
            stop_event.wait(self._poll_s)

    def _reader(self, ser, generation, stop_event):
        buf = b""
        while not stop_event.is_set():
            try:
                chunk = ser.read(256)
            except Exception as exc:
                with self._lock:
                    if generation == self._generation and self._ser is ser:
                        self._ser = None
                        self._generation += 1
                        stop_event.set()
                        self._invalidate_locked(f"Serial read failed: {exc}; reconnect and home again")
                        self.busy = self._busy_owner = None
                try:
                    ser.close()
                except Exception:
                    pass
                return
            if not chunk:
                continue
            buf += chunk
            if len(buf) > 8192:
                buf = b""
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._handle(raw.decode("ascii", "replace").strip(), generation)

    def _handle(self, text, generation):
        with self._lock:
            if generation != self._generation or self._ser is None or not text:
                return
            self._rx_seq += 1
            match = STATUS_RE.fullmatch(text)
            if match:
                previous_state = self.state
                self.state = match.group(1)
                self.mpos = [float(match.group(i)) for i in (2, 3, 4)]
                if not all(math.isfinite(v) for v in self.mpos):
                    self._invalidate_locked("Invalid machine position")
                    return
                self.line, self.t_status = text, time.monotonic()
                self._status_seq = self._rx_seq
                if self.state == "Alarm":
                    # A queued startup-Alarm report may arrive just after the
                    # user starts homing. Explicit ALARM errors or a transition
                    # back into Alarm still invalidate the operation.
                    if not (self.busy == "homing" and previous_state == "Alarm"):
                        self._invalidate_locked("Controller Alarm; home again")
                elif self._capture_owner and (self.state != "Idle" or not self._at(self.mpos, POSE)):
                    self._invalidate_locked("Machine left the verified capture pose during recording")
                elif self.capture_ready and (self.state != "Idle" or not self._at(self.mpos, POSE)):
                    self.capture_ready = False
                    self.capture_error = "Machine left the capture pose; move to scan position again"
            elif text == "ok":
                if self._pending is not None and self._pending["owner"] == (self._generation, self._token):
                    self._pending["ack"] = self._rx_seq
            elif text.lower().startswith("error") or text.upper().startswith("ALARM"):
                self._invalidate_locked(f"Controller reported {text}; home again")
            elif text.lower().startswith("grbl"):
                self.restarted = True
                self.state = self.mpos = self.line = None
                self.t_status = 0.0
                self._invalidate_locked("Controller restarted; home again")
            else:
                self.messages.append(text)
            self._changed.notify_all()

    def _new_operation(self, kind, states=("Idle",), require_homed=False):
        with self._lock:
            if self.busy or self._capture_owner:
                raise MachineError(f"Machine is busy ({self.busy or 'recording'})")
            self._fresh_locked(states)
            if require_homed and not self.homed:
                raise MachineError("Home with Z first and then all axes on this connection before moving")
            self._token += 1
            owner = (self._generation, self._token)
            self.busy, self._busy_owner = kind, owner
            self.capture_error = None
            return owner

    def _fresh_idle(self, owner, target=None, timeout=None):
        timeout = self._status_timeout if timeout is None else timeout
        with self._lock:
            self._check_token_locked(owner)
            after = self._status_seq
            self._write_locked(b"?", owner[0])
            deadline = time.monotonic() + timeout
            next_poll = time.monotonic() + self._poll_s
            while True:
                self._check_token_locked(owner)
                now = time.monotonic()
                if (self._status_seq > after and now - self.t_status <= self._fresh_s and
                        self.state == "Idle" and (target is None or self._at(self.mpos, target))):
                    return self.line
                if now >= deadline:
                    expected = "Idle" if target is None else f"Idle at {list(target)}"
                    raise MachineError(f"No fresh {expected} confirmation; Stop and home again")
                if now >= next_poll:
                    self._write_locked(b"?", owner[0])
                    next_poll = now + self._poll_s
                self._changed.wait(min(0.05, deadline - now))

    def _command(self, command, owner, stage, target, timeout, required_start=None):
        if command not in COMMANDS:
            raise MachineError("Command is outside the fixed allowlist")
        self._event("stage_start", owner, stage=stage, command=command, expected_mpos=target)
        with self._lock:
            self._check_token_locked(owner)
            self._fresh_locked(("Idle", "Alarm") if command == "$HZ" else ("Idle",))
            if required_start is not None and not self._at(self.mpos, required_start):
                raise MachineError("Machine left the previously verified stage position; Stop and home again")
            self._pending = {"owner": owner, "ack": None}
            self._write_locked((command + "\n").encode("ascii"), owner[0])
        self._event("command_sent", owner, stage=stage, command=command, expected_mpos=target)
        with self._lock:
            deadline = time.monotonic() + timeout
            next_poll = time.monotonic()
            while True:
                self._check_token_locked(owner)
                if self._pending is not None and self._pending["ack"] is not None:
                    break
                now = time.monotonic()
                if now >= deadline:
                    raise MachineError(f"No acknowledgment for {stage}; Stop and home again")
                if now >= next_poll:
                    self._write_locked(b"?", owner[0])
                    next_poll = now + self._poll_s
                self._changed.wait(min(0.05, deadline - now))
            self._pending = None
        # Deliberately ignore all reports delivered before this post-ACK query.
        line = self._fresh_idle(owner, target, timeout)
        self._event("stage_complete", owner, stage=stage, command=command, expected_mpos=target, verified_line=line)
        return line

    def _finish(self, owner, error):
        with self._lock:
            if self._capture_owner == owner:
                self._capture_owner = None
            if self._busy_owner == owner:
                if error is not None and owner == (self._generation, self._token):
                    self._invalidate_locked(error)
                self.busy = self._busy_owner = None
                self._pending = None
                self._changed.notify_all()

    def _start_worker(self, owner, action, on_done):
        def run():
            error = None
            try:
                action(owner)
            except Exception as exc:
                error = str(exc)
                # A failed ACK, wrong pose or failed log can leave the current
                # move active. Halt that exact connection before releasing busy.
                try:
                    self._perform_stop("Operation failed: " + error, required_owner=owner)
                except Exception as stop_exc:
                    error += f"; automatic stop could not be confirmed: {stop_exc}"
                try:
                    self._event("operation_failed", error=error)
                except Exception:
                    pass  # Do not obscure a motion failure with a logging failure.
            finally:
                self._finish(owner, error)
            if on_done:
                try:
                    on_done(error)
                except Exception as exc:
                    with self._lock:
                        self.messages.append(f"Completion callback failed: {exc}")
        threading.Thread(target=run, daemon=True).start()
        return self.status()

    def start_homing(self, on_done=None):
        owner = self._new_operation("homing", states=("Idle", "Alarm"))
        with self._lock:
            self.homed = self.capture_ready = False

        def action(token):
            self._command("$HZ", token, "home_z", None, self._homing_timeout)
            self._command("$H", token, "home_all", None, self._homing_timeout)
            with self._lock:
                self._check_token_locked(token)
                self.homed = True
                self.restarted = False
                self.capture_error = None
        return self._start_worker(owner, action, on_done)

    def start_scan_position(self, on_done=None):
        self._config()
        owner = self._new_operation("scan_position", require_homed=True)
        with self._lock:
            self.capture_ready = False

        def action(token):
            self._fresh_idle(token)
            with self._lock:
                self._check_token_locked(token)
                start = tuple(self.mpos)
            targets = ((start[0], start[1], TRAVEL_Z), (POSE[0], POSE[1], TRAVEL_Z), POSE)
            previous = start
            for (stage, command), target in zip(ROUTE, targets):
                self._config()
                self._command(command, token, stage, target, self._move_timeout, required_start=previous)
                previous = target
            with self._lock:
                self._check_token_locked(token)
                self.capture_ready = True
                self.capture_error = None
        return self._start_worker(owner, action, on_done)

    def start_park(self, on_done=None):
        """Up to travel Z where the head is, then across to X0 Y0 at travel Z (the home corner). Each stage verified
        like Move to scan position; the head is no longer at the scan position afterwards."""
        self._config()
        owner = self._new_operation("park", require_homed=True)
        with self._lock:
            self.capture_ready = False

        def action(token):
            self._fresh_idle(token)
            with self._lock:
                self._check_token_locked(token)
                start = tuple(self.mpos)
            lift = (start[0], start[1], TRAVEL_Z)
            self._command(ROUTE[0][1], token, "raise_to_travel", lift, self._move_timeout, required_start=start)
            self._command(PARK_COMMAND, token, "park_xy", (PARK_XY[0], PARK_XY[1], TRAVEL_Z), self._move_timeout,
                          required_start=lift)
        return self._start_worker(owner, action, on_done)

    @contextmanager
    def capture_guard(self):
        self._config()
        with self._lock:
            if not self.capture_ready:
                raise MachineError("Move to scan position and verify it before recording")
            owner = self._new_operation("recording", require_homed=True)
            self._capture_owner = owner
        error = None
        try:
            line = self._fresh_idle(owner, POSE)
            self._event("capture_start", owner, verified_line=line)
            yield line
            line = self._fresh_idle(owner, POSE)
            self._event("capture_complete", owner, verified_line=line)
        except Exception as exc:
            error = str(exc)
            raise
        finally:
            self._finish(owner, error)

    def _perform_stop(self, reason, required_owner=None):
        with self._stop_call_lock:
            with self._lock:
                if required_owner is not None and (self._busy_owner != required_owner or
                                                    self._generation != required_owner[0] or self._ser is None):
                    return
                if self._ser is None:
                    raise MachineError("Machine not connected")
                generation = self._generation
                self._invalidate_locked(reason + "; home again before moving or recording")
                stop_owner = (generation, self._token)
                self.busy, self._busy_owner = "stopping", stop_owner
                self._pending = None
            try:
                with self._lock:
                    self._write_locked(b"!", generation)
                time.sleep(0.2)
                with self._lock:
                    if self._ser is not None and generation == self._generation:
                        self._write_locked(b"\x18", generation)
                        # Status from before reset must not authorize new homing.
                        self.state = self.mpos = self.line = None
                        self.t_status = 0.0
            finally:
                with self._lock:
                    if self._busy_owner == stop_owner:
                        self.busy = self._busy_owner = None
                    self._changed.notify_all()

    def stop(self):
        self._perform_stop("STOP requested")
        self._event("stop", command="! then Ctrl-X")
        return self.status()
