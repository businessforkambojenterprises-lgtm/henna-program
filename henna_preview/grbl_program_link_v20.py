"""Studio v20 link - "Review fixes" (outside review by Codex, 2026-10-12): the v15 link with three safety fixes.

1. STOP always tries the controller reset, even when sending the feed hold fails; if either write fails the link
   reports `stop_unconfirmed` (the page tells the user to use the emergency stop) until the next Connect/Home.
2. Pause must be CONFIRMED: a fresh report saying Hold or Idle must arrive within `hold_timeout` of Pause (and again
   after any report of motion). Silence or stale reports count as unconfirmed, so a controller that ignores the hold
   and stops reporting is stopped (feed hold + reset) instead of staying "paused".
3. A finished run is recorded before the machine is released: the completion callback gets an immutable snapshot
   taken while the run still owns the machine, so a run started right afterwards cannot leak into the record.
v13 / v14 / v15 files are unchanged.
"""
import copy
import threading
import time

from henna_preview.grbl_program_link import MachineError  # noqa: F401 (re-export)
from henna_preview.grbl_program_link_v15 import MOVING_STATES, GrblProgramLinkV15

HELD_STATES = ("Hold", "Idle")


class GrblProgramLinkV20(GrblProgramLinkV15):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stop_unconfirmed = False

    def status(self):
        with self._lock:
            out = super().status()
            out["stop_unconfirmed"] = self.stop_unconfirmed
            return out

    # ---- 1. STOP: reset even if the feed hold could not be written ----
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
            failures = []
            try:
                try:
                    with self._lock:
                        self._write_locked(b"!", generation)
                except Exception as exc:          # keep going: the reset matters more
                    failures.append(f"feed hold not sent ({exc})")
                time.sleep(0.2)
                try:
                    with self._lock:
                        if self._ser is None or generation != self._generation:
                            raise MachineError("connection lost")
                        self._write_locked(b"\x18", generation)
                        self.state = self.mpos = self.line = None      # pre-reset status must not authorize homing
                        self.t_status = 0.0
                except Exception as exc:
                    failures.append(f"reset not sent ({exc})")
            finally:
                with self._lock:
                    if failures:
                        self.stop_unconfirmed = True
                        self.capture_error = ("STOP NOT CONFIRMED - use the emergency stop: " + "; ".join(failures))
                        self.messages.append(self.capture_error)
                    if self._busy_owner == stop_owner:
                        self.busy = self._busy_owner = None
                    self._changed.notify_all()
            if failures:
                raise MachineError("STOP could not be confirmed - use the emergency stop (" + "; ".join(failures) + ")")

    def connect(self, *args, **kwargs):
        super().connect(*args, **kwargs)
        with self._lock:
            self.stop_unconfirmed = False
        return self.status()

    def start_homing(self, on_done=None):
        def done(error):
            if error is None:
                with self._lock:
                    self.stop_unconfirmed = False
            if on_done:
                on_done(error)
        return super().start_homing(on_done=done)

    # ---- 2. Pause must be confirmed by a fresh Hold/Idle report ----
    def pause(self):
        with self._lock:
            if self.busy == "program" and self._run is not None and self._run["state"] == "running":
                self._run["need_hold_since"] = time.monotonic()
        return super().pause()

    def _hold_check_locked(self, owner):
        r = self._run
        if r is None or not r["paused"]:
            return
        now = time.monotonic()
        fresh = self._status_seq > r["pause_seq"] and now - self.t_status <= self._fresh_s
        if fresh and self.state in HELD_STATES:
            r["need_hold_since"] = None                         # confirmed held
            return
        if r.get("need_hold_since") is None:
            r["need_hold_since"] = now                          # moving again, or reports stopped / went stale
        if fresh and self.state in MOVING_STATES and now >= r["next_hold"]:
            self._write_locked(b"!", owner[0])
            r["next_hold"] = now + self._poll_s
            r["hold_resent"] += 1
        if now - r["need_hold_since"] > self._hold_timeout:
            raise MachineError("Pause was not confirmed by the controller (no fresh Hold report); run stopped")

    # ---- 3. Record the finished run before releasing the machine ----
    def start_program(self, name, lines, end_mpos, on_done=None):
        """on_done(error, snapshot): snapshot = status() taken while this run still owned the machine."""
        # v14's start_program hands its own run-finalizer to _start_worker; route it to the v20 worker for this call.
        self._start_worker = lambda owner, action, done: self._start_worker_v20(owner, action, done, on_done)
        try:
            return super().start_program(name, lines, end_mpos, on_done=None)
        finally:
            del self._start_worker

    def _start_worker_v20(self, owner, action, finalize, on_done):
        def run():
            error = None
            try:
                action(owner)
            except Exception as exc:
                error = str(exc)
                try:
                    self._perform_stop("Operation failed: " + error, required_owner=owner)
                except Exception as stop_exc:
                    error += f"; automatic stop could not be confirmed: {stop_exc}"
                try:
                    self._event("operation_failed", error=error)
                except Exception:
                    pass
            snapshot = None
            try:
                finalize(error)                                  # run state set while still owning the machine
                with self._lock:
                    snapshot = copy.deepcopy(self.status())
            finally:
                self._finish(owner, error)
            if on_done:
                try:
                    on_done(error, snapshot)
                except Exception as exc:
                    with self._lock:
                        self.messages.append(f"Completion callback failed: {exc}")
        threading.Thread(target=run, daemon=True).start()
        return self.status()
