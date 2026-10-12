"""Studio v22 link - "Third review fix" (outside review by Codex, 2026-10-12): the v21 link with one safety fix.

A successful Home cleared "STOP NOT CONFIRMED" in its completion callback, which runs AFTER the machine is
released. A late callback could therefore erase a newer warning: Home finishes -> a program starts -> its STOP
fails -> the old Home callback clears the new warning while the controller is still running.
v22 clears the warning inside the homing operation itself: under the lock, only if the homing token is still
valid, and before the machine is released - so it can never touch a failure that happened after it.
Homing is the same as v13 otherwise ($HZ, then $H, fresh Idle after each). v13 - v21 files are unchanged.
"""
from henna_preview.grbl_program_link import MachineError  # noqa: F401 (re-export)
from henna_preview.grbl_program_link_v21 import GrblProgramLinkV21


class GrblProgramLinkV22(GrblProgramLinkV21):
    def start_homing(self, on_done=None):
        """v13 homing; the warning is cleared atomically with 'homed', while this Home still owns the machine."""
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
                self.stop_unconfirmed = False
        return self._start_worker(owner, action, on_done)
