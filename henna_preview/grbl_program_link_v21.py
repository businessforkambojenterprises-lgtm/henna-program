"""Studio v21 link - "Second review fixes" (outside review by Codex, 2026-10-12): the v20 link with two fixes.

1. "STOP NOT CONFIRMED" stays until a successful Home. v20 cleared it on Connect, and Connect on the open
   connection only returns the status - so pressing Connect hid the warning while the machine could still be
   moving. Homing needs a fresh Idle/Alarm report and two acknowledged commands, so it is the verified recovery.
2. Each run's completion record is built only from that run's own data. v20 read the link status when the worker
   finished; after STOP (or a lost connection) the machine is released before that, so a delayed worker could
   report the NEXT run's status. Now every run keeps its own progress and its own last machine report (taken
   while it owned the machine, and again just before a STOP releases it), and the record uses only those.
v13 - v20 files are unchanged.
"""
import copy
import threading
import time

from henna_preview.grbl_program_link import MachineError  # noqa: F401 (re-export)
from henna_preview.grbl_program_link_v20 import GrblProgramLinkV20


class GrblProgramLinkV21(GrblProgramLinkV20):
    # ---- 1. the warning is cleared only by a successful Home (v20's start_homing) ----
    def connect(self, *args, **kwargs):
        super(GrblProgramLinkV20, self).connect(*args, **kwargs)    # v15/v13 connect, without v20's clearing
        return self.status()

    # ---- 2. each run's own record ----
    def _remember_locked(self, run):
        """Copy the current machine report into the run (only while the run owns the machine)."""
        st = super().status()
        run["machine"] = {k: copy.deepcopy(st[k]) for k in ("connected", "port", "state", "mpos", "line", "age_s",
                                                             "homed", "capture_error", "stop_unconfirmed")}
        run["machine_seq"] = self._status_seq

    def _owns_locked(self, run):
        return (run is not None and self._run is run and run.get("owner") is not None
                and self._busy_owner == run["owner"])

    def _tick_locked(self, owner):
        super()._tick_locked(owner)                          # raises if the run lost the machine
        r = self._run
        if self._owns_locked(r) and r.get("machine_seq") != self._status_seq:
            self._remember_locked(r)

    def _perform_stop(self, reason, required_owner=None):
        with self._lock:
            if self._owns_locked(self._run):
                self._remember_locked(self._run)             # last report before STOP releases the machine
        return super()._perform_stop(reason, required_owner=required_owner)

    @staticmethod
    def _program_view(run):
        end = run["t_end"] or time.monotonic()
        return {"name": run["name"], "total": run["total"], "done": run["acked"], "paused": run["paused"],
                "state": run["state"], "error": run["error"], "elapsed_s": round(end - run["t0"], 1)}

    def _run_record(self, run):
        with self._lock:
            if self._owns_locked(run):
                self._remember_locked(run)
            return copy.deepcopy(dict(run.get("machine") or {}, program=self._program_view(run)))

    def _start_worker_v20(self, owner, action, finalize, on_done):
        with self._lock:
            run = self._run                                  # set by v14's start_program just before this call
            run["owner"] = owner
            self._remember_locked(run)

        def work():
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
            record = None
            try:
                finalize(error)                              # sets this run's own state, end time and error
                record = self._run_record(run)
            finally:
                self._finish(owner, error)
            if on_done:
                try:
                    on_done(error, record)
                except Exception as exc:
                    with self._lock:
                        self.messages.append(f"Completion callback failed: {exc}")
        threading.Thread(target=work, daemon=True).start()
        return self.status()
