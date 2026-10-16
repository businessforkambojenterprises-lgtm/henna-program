"""Studio v29 link: the v26 link + paste dispensing (user approval 2026-10-15: "go A").

- read_settings(): sends $$ (read only) while the machine is idle and keeps the $n=value replies, so a paste run
  can check laser mode ($32=0) and the full output ($30) right before it starts.
- A paste run (arm_dispense() just before start_program): every Pause - Studio's button, the push button, the hand
  watch - also stops the paste. grbl keeps the spindle output on in a feed hold, so once the controller reports Hold,
  the link sends grbl's spindle-stop override (0x9E). Resume first restores the paste (0x9E again, still in Hold),
  waits the program's start delay so the paste reaches the nozzle, and only then sends cycle start (~). If that
  resume is refused, the paste is stopped again.
  STOP, errors, a lost connection and closing Studio end in a controller reset, and a reset switches the output off.
- start_dispense_test(): M3 S1000, G4 P<s>, M5 while idle - the dispenser runs for 0.2 to 3 s, the machine never
  moves. Any failure ends in the usual automatic stop (feed hold + reset, output off).

PASTE OUTPUT ON THE PI (user 2026-10-15: "B" - the SSR on a Raspberry Pi GPIO pin): set link.paste_output to an
object with on(), off() and name. The program file is the same; the link does not send M3 / M5 but
  "G4 P0" (its ok comes only after every earlier move has finished) and then switches the pin itself,
so the paste still switches exactly at the drop's end and the stroke's end; the start and stop delays (G4) go to the
controller as before. Pause switches the pin OFF at once; Resume switches it ON, waits the start delay, then sends
cycle start. The pin goes OFF at the end of every operation, on every error, stop, reset and disconnect.
WHICH OUTPUT A RUN USES (user 2026-10-16: "when using pneumatic valve, it giving command through gpio pin instead of
dlc32?"): the paste settings armed for the run say it - cfg["output"] "pi" = the Pi output above, "controller" = the
DLC32's TTL with M3 / M5. Studio v29 sets it from the Paste choice (Pneumatic -> controller, Servo -> the Pi's servo).
Settings without "output" keep the earlier rule: the Pi output whenever one is set.
"""
import re
import threading
import time

from henna_preview import dispense
from henna_preview.grbl_program_link import MachineError
from henna_preview.grbl_program_link_v26 import GrblProgramLinkV26

SETTING_RE = re.compile(r"^\$(\d+)=(\S+)$")
SPINDLE_STOP = b"\x9e"
HOLD_WAIT_S = 3.0
SYNC = "G4 P0"                    # grbl answers ok only once every earlier move has finished
TEST_MIN_S, TEST_MAX_S = 0.2, 3.0


class GrblProgramLinkV29(GrblProgramLinkV26):
    paste_output = None           # None = the controller's spindle output (M3/M5); else a Pi pin (on/off/name)

    def _pin_off(self):
        out = self.paste_output
        if out is not None:
            try:
                out.off()
            except Exception as e:                       # noqa: BLE001 - reported, the reset path continues
                self.messages.append(f"Paste output could not be switched off: {e}")

    def _invalidate_locked(self, reason):
        self._pin_off()
        return super()._invalidate_locked(reason)

    def _finish(self, owner, error):
        self._pin_off()                                  # every operation ends with the paste OFF
        return super()._finish(owner, error)

    def stop(self):
        self._pin_off()
        return super().stop()

    def disconnect(self):
        self._pin_off()
        return super().disconnect()

    def _run_pin_locked(self):
        """The Pi output this run's paste uses, or None for the controller's output."""
        return self._pin_for(self._paste_cfg_locked())

    def _pin_for(self, cfg):
        if not cfg or self.paste_output is None:
            return None
        return self.paste_output if cfg.get("output", "pi") == "pi" else None

    def _send_line(self, owner, line):
        with self._lock:
            out = self._run_pin_locked()
        if out is None or line not in (dispense.PASTE_ON, dispense.PASTE_OFF):
            return super()._send_line(owner, line)
        super()._send_line(owner, SYNC)                  # every earlier move has finished
        on = line == dispense.PASTE_ON
        with self._lock:
            while self._run["paused"]:                   # paused right at the stroke start: wait for Resume
                self._check_token_locked(owner)
                self._changed.wait(0.05)
            self._check_token_locked(owner)
            (out.on if on else out.off)()
            self._run["paste_on"] = on
        self._event("paste_on" if on else "paste_off", owner, command=f"{SYNC}; {out.name} {'ON' if on else 'OFF'}",
                    detail="after every earlier move finished")

    # ---- $$ (read only) ----
    def _handle(self, text, generation):
        m = SETTING_RE.fullmatch(text or "")
        if m is None:
            return super()._handle(text, generation)
        with self._lock:
            if generation != self._generation or self._ser is None:
                return
            self._rx_seq += 1
            if getattr(self, "_settings_seen", None) is not None:
                self._settings_seen[int(m.group(1))] = m.group(2)
            self._changed.notify_all()

    def read_settings(self, timeout=5.0):
        owner = self._new_operation("settings")
        try:
            with self._lock:
                self._settings_seen = {}
                self._pending = {"owner": owner, "ack": None}
                self._write_locked(b"$$\n", owner[0])
                deadline = time.monotonic() + timeout
                while self._pending is None or self._pending["ack"] is None:
                    self._check_token_locked(owner)
                    if time.monotonic() >= deadline:
                        raise MachineError("The controller did not answer $$ (settings)")
                    self._changed.wait(0.05)
                found, self._settings_seen = dict(self._settings_seen), None
            self._event("settings_read", command="$$", detail=f"{len(found)} settings")
            return found
        finally:
            self._finish(owner, None)

    # ---- paste runs ----
    def arm_dispense(self, cfg):
        """The paste settings of the program that start_program() starts next (None = a hover run)."""
        with self._lock:
            self._dispense_next = dict(cfg) if cfg else None

    def start_program(self, name, lines, end_mpos, on_done=None):
        self._pin_off()                                  # a Pi output left at a test angle starts every run closed
        with self._lock:
            cfg, self._dispense_next = getattr(self, "_dispense_next", None), None
            self._dispense_starting = cfg
        try:
            return super().start_program(name, lines, end_mpos, on_done=on_done)
        finally:
            with self._lock:
                if self._run is not None:
                    self._run["dispense"] = cfg
                    self._run["paste_stopped"] = False
                self._dispense_starting = None

    def _paste_cfg_locked(self):
        r = self._run
        if r is None:
            return None
        return r.get("dispense") or getattr(self, "_dispense_starting", None)

    def status(self):
        out = super().status()
        with self._lock:
            cfg = self._paste_cfg_locked()
            if out.get("program") is not None and cfg:
                out["program"]["paste"] = {"dispenser": cfg["dispenser"], "label": cfg["label"],
                                           "stopped_in_pause": bool(self._run.get("paste_stopped")),
                                           "output": (self._run_pin_locked().name if self._run_pin_locked()
                                                      else "controller")}
        return out

    def pause(self):
        out = super().pause()
        with self._lock:
            cfg, run, generation = self._paste_cfg_locked(), self._run, self._generation
            if cfg and self._pin_for(cfg) is not None:
                self._pin_off()
                run["paste_stopped"] = bool(run.get("paste_on"))
                cfg = None                               # nothing to send to the controller
        if cfg:
            threading.Thread(target=self._stop_paste_in_hold, args=(run, generation), daemon=True).start()
        return out

    def _stop_paste_in_hold(self, run, generation):
        deadline = time.monotonic() + HOLD_WAIT_S
        with self._lock:
            while True:
                if self._run is not run or self._generation != generation or not run["paused"]:
                    return                                   # resumed, stopped or reconnected meanwhile
                if self.state == "Hold":
                    break
                if time.monotonic() >= deadline:
                    break
                self._write_locked(b"?", generation)
                self._changed.wait(0.1)
            held = self.state == "Hold"
            if held:
                self._write_locked(SPINDLE_STOP, generation)
                run["paste_stopped"] = True
        if held:
            self._event("paste_stop", command="0x9E (spindle stop in Hold)", detail="paste OFF during the pause")
        else:                                                # never left with the paste on and no confirmed Hold
            self._perform_stop("Pause was not confirmed while dispensing; paste stopped by a reset")

    def resume(self):
        with self._lock:
            cfg, run = self._paste_cfg_locked(), self._run
            pin = self._pin_for(cfg)
            restore = bool(cfg and run is not None and run.get("paused") and run.get("paste_stopped"))
            if restore:
                self._fresh_locked(("Hold",))
                if pin is not None:
                    pin.on()
                else:
                    self._write_locked(SPINDLE_STOP, self._generation)  # 0x9E again in Hold = restore
                run["paste_stopped"] = False
        if restore:
            self._event("paste_restore", command="0x9E (restore in Hold)", detail=f"paste ON, {cfg['start_s']:g} s "
                        "before the machine moves on")
            time.sleep(float(cfg["start_s"]))
        try:
            return super().resume()
        except Exception:
            if restore:
                with self._lock:
                    if pin is not None:
                        self._pin_off()
                        run["paste_stopped"] = True
                    elif self._run is run and run.get("paused") and self.state == "Hold":
                        self._write_locked(SPINDLE_STOP, self._generation)
                        run["paste_stopped"] = True
            raise

    # ---- dispenser test (machine still) ----
    def start_dispense_test(self, seconds, on_done=None, output=None):
        """output "pi" / "controller"; None = the Pi output when one is set (the earlier rule)."""
        seconds = float(seconds)
        if not TEST_MIN_S <= seconds <= TEST_MAX_S:
            raise MachineError(f"Dispenser test: {TEST_MIN_S:g} to {TEST_MAX_S:g} s")
        owner = self._new_operation("dispense_test")
        lines = (dispense.PASTE_ON, dispense.dwell(seconds), dispense.PASTE_OFF)
        pin = self.paste_output if output in (None, "pi") else None

        def pin_action(token):
            with self._lock:
                self._check_token_locked(token)
                self._fresh_locked()
                pin.on()
            try:
                time.sleep(seconds)
            finally:
                self._pin_off()
            self._event("dispense_test", token, command=f"{pin.name} ON {seconds:g} s",
                        detail=f"dispenser ran {seconds:g} s on the Pi pin, machine still")

        if pin is not None:
            return self._start_worker(owner, pin_action, on_done)

        def action(token):
            with self._lock:
                self._check_token_locked(token)
                self._fresh_locked()
                start = tuple(self.mpos)
            for line in lines:
                with self._lock:
                    self._check_token_locked(token)
                    self._pending = {"owner": token, "ack": None}
                    self._write_locked((line + "\n").encode("ascii"), token[0])
                    deadline = time.monotonic() + seconds + 5.0
                    while self._pending is None or self._pending["ack"] is None:
                        self._check_token_locked(token)
                        if time.monotonic() >= deadline:
                            raise MachineError(f"No acknowledgment for '{line}'; paste stopped by a reset")
                        self._changed.wait(0.05)
                    self._pending = None
            line = self._fresh_idle(token, start)
            self._event("dispense_test", token, command=" / ".join(lines), expected_mpos=list(start),
                        verified_line=line, detail=f"dispenser ran {seconds:g} s, machine did not move")
        return self._start_worker(owner, action, on_done)
