"""Hardware PAUSE button on the Raspberry Pi (user 2026-10-15: "for pause and stop, I want individual push button").

Wiring: a momentary, normally-open push button between GPIO17 (header pin 11) and GND (pin 9). The Pi's internal
pull-up holds the pin high; a press pulls it low. No resistor.
The STOP button is NOT here: it is a latching emergency stop in the power supply's + wire to the controller, so it
cuts the motors whatever the software does (the DLC32 has no spare input for a controller-side pause button).

What a press does - the same calls as Studio's own buttons:
  a program running (not paused)  -> Pause: feed hold '!', the position is kept; Resume stays on the screen, so a
                                     real-hand run still re-checks the hand before it moves again
  anything else moving            -> STOP, as Studio's STOP button (feed hold + reset; home again afterwards)
  nothing moving                  -> nothing (logged)
It works only while Studio runs on the Pi; its state is in GET /api/pi/pause_button and the machine bar shows it.
"""
import threading
import time

PIN = 17
CONFIRM_S = 0.02                    # still pressed 20 ms later: a press, not a spike picked up by the wires
MOVING_STATES = ("Run", "Jog", "Home")


def press_action(status):
    """'pause' | 'stop' | None for one press, from machine.status()."""
    prog = status.get("program") or {}
    busy = status.get("busy")
    if busy == "program" and prog.get("state") == "running":
        return None if prog.get("paused") else "pause"     # already held: Resume or STOP on the screen
    if str(status.get("state") or "").split(":")[0] in MOVING_STATES or busy not in (None, "recording"):
        return "stop"                                      # a jog, homing, move to scan, calibration move
    return None


def _gpio_button():
    """gpiozero.Button on PIN. Studio's venv has no GPIO library; Raspberry Pi OS ships gpiozero + lgpio for the
    system Python, which is the same interpreter the venv was made from."""
    try:
        from gpiozero import Button
    except ImportError:
        import sys
        sys.path.append("/usr/lib/python3/dist-packages")
        from gpiozero import Button
    return Button(PIN, pull_up=True, bounce_time=0.05)


class PauseButton:
    def __init__(self, machine, log, make_button=_gpio_button):
        self.machine, self.log, self._make = machine, log, make_button
        self.lock = threading.Lock()
        self.state = {"ready": False, "error": None, "pin": PIN, "presses": 0, "last": None}
        self.button = None

    def start(self):
        try:
            self.button = self._make()
            self.button.when_pressed = self.pressed
            with self.lock:
                self.state.update(ready=True, error=None)
        except Exception as e:                             # no GPIO: Studio still runs, the bar says NOT WORKING
            with self.lock:
                self.state.update(ready=False, error=f"{type(e).__name__}: {e}")
        return self.view()

    def view(self, _=None):
        with self.lock:
            return dict(self.state)

    def pressed(self):
        time.sleep(CONFIRM_S)
        if self.button is not None and not self.button.is_pressed:
            return
        st = self.machine.status()
        action = press_action(st)
        result, error = "nothing moving - no command", None
        try:
            if action == "pause":
                self.machine.pause()
                result = "paused (feed hold)"
            elif action == "stop":
                self.machine.stop()
                result = "stopped (feed hold + reset) - home again"
        except Exception as e:
            error = str(e)
            result = f"{action} failed: {e}"
            if action == "pause":                          # a press must never leave the machine moving
                try:
                    self.machine.stop()
                    result += "; STOP sent instead (feed hold + reset) - home again"
                except Exception as e2:
                    result += f"; STOP failed too: {e2}"
        with self.lock:
            self.state["presses"] += 1
            self.state["last"] = {"t": time.strftime("%H:%M:%S"), "action": action, "result": result, "error": error}
        self.log("Pause button pressed", st.get("mpos"), {"pause": "! (feed hold)", "stop": "! then Ctrl-X"}.get(action, "-"),
                 "machine holds" if action else "no motion", f"state {st.get('state')}, busy {st.get('busy')}", result,
                 "", f"Pi pause button GPIO{PIN}")

    def close(self):
        if self.button is not None:
            try:
                self.button.close()
            except Exception:
                pass
