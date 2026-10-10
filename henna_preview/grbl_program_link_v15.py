"""Studio v15 program link: the v14 link with two fixes found in the first real run (2026-10-10).

1. Fresh position during a run. v14 asked for the status ('?') only while one acknowledgment took longer
   than the poll interval, so during smooth drawing the shown/logged position could be about 1 s old.
   v15 polls on a fixed schedule for the whole run, whether or not the controller answers quickly.
2. Pause is verified. After Pause, a fresh report that still says Run/Jog gets the feed hold sent again;
   if the machine is still moving `hold_timeout` seconds after Pause, the run is stopped (hold + reset).
Everything else (allowlist, ok per line, nothing new sent while paused, end-position check) is v14.
"""
import time

from henna_preview.grbl_program_link import GrblProgramLink, MachineError  # noqa: F401 (re-export)

MOVING_STATES = ("Run", "Jog")


class GrblProgramLinkV15(GrblProgramLink):
    def __init__(self, *args, hold_timeout=1.5, **kwargs):
        super().__init__(*args, **kwargs)
        self._hold_timeout = hold_timeout
        self._next_poll = 0.0

    # ---- shared checks, called with the lock held inside every run loop ----
    def _poll_due_locked(self, owner):
        now = time.monotonic()
        if now >= self._next_poll:
            self._write_locked(b"?", owner[0])
            self._next_poll = now + self._poll_s

    def _hold_check_locked(self, owner):
        r = self._run
        if r is None or not r["paused"] or self._status_seq <= r["pause_seq"]:
            return
        if self.state not in MOVING_STATES:
            return
        now = time.monotonic()
        if now - r["t_pause"] > self._hold_timeout:
            raise MachineError("Pause did not hold the machine; run stopped")
        if now >= r["next_hold"]:
            self._write_locked(b"!", owner[0])
            r["next_hold"] = now + self._poll_s
            r["hold_resent"] += 1

    def _tick_locked(self, owner):
        self._check_token_locked(owner)
        self._poll_due_locked(owner)
        self._hold_check_locked(owner)

    # ---- run loops (v14 logic, with the shared checks) ----
    def _send_line(self, owner, line):
        with self._lock:
            self._tick_locked(owner)
            while self._run["paused"]:                 # while paused, queue nothing new
                self._tick_locked(owner)
                self._changed.wait(0.05)
            if self.state not in ("Idle", "Run", "Hold", "Jog"):
                raise MachineError(f"Machine is {self.state}; run stopped")
            self._pending = {"owner": owner, "ack": None}
            self._write_locked((line + "\n").encode("ascii"), owner[0])
            deadline = time.monotonic() + self._ack_timeout
            while True:
                self._tick_locked(owner)
                if self._pending is not None and self._pending["ack"] is not None:
                    self._pending = None
                    return
                now = time.monotonic()
                if self._held_locked():
                    deadline = now + self._ack_timeout          # a pause never times out
                if now >= deadline:
                    raise MachineError(f"No acknowledgment for '{line}'; run stopped")
                self._changed.wait(min(0.05, max(0.0, deadline - now)))

    def _wait_end(self, owner, end_mpos):
        with self._lock:
            self._check_token_locked(owner)
            after = self._status_seq
            self._write_locked(b"?", owner[0])
            self._next_poll = time.monotonic() + self._poll_s
            deadline = time.monotonic() + self._end_timeout
            idle_away_since = None
            while True:
                self._tick_locked(owner)
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
                self._changed.wait(min(0.05, max(0.0, deadline - now)))

    def pause(self):
        with self._lock:
            if self.busy == "program" and self._run is not None and self._run["state"] == "running":
                self._run.update(t_pause=time.monotonic(), pause_seq=self._status_seq,
                                 next_hold=time.monotonic() + self._poll_s, hold_resent=0)
                self._next_poll = 0.0                          # ask for a status right away
        return super().pause()
