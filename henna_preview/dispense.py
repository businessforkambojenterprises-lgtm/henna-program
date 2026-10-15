"""Paste dispensing (Studio v29, user approval 2026-10-15: "go A").

Two dispensers, both switched by the controller's spindle/laser output through a relay (the user's choice A):
  pneumatic  - a solenoid air valve
  servo_ssr  - the servo dispenser's own driver, started and stopped by an SSR
For the controller both are the same: M3 S<full> = paste ON, M5 = paste OFF. With laser mode off ($32=0) grbl runs
M3, M5 and G4 only after every earlier move has finished, so the paste switches exactly where the program says.

Each stroke of the checked hover program
    G53 G0 X Y          over the stroke start, at travel or hop height
    G53 G1 X Y Z F      the drop to the skin
    G53 G1 ... F        drawing
    G53 G0 Z            the lift
becomes
    G53 G0 X Y
    G53 G1 X Y Z F
    M3 S1000            paste ON
    G4 P0.200           start delay: the paste reaches the nozzle before it moves
    G53 G1 ... F
    M5                  paste OFF
    G4 P0.100           stop delay: the flow stops before the lift
    G53 G0 Z
Nothing else changes: taking these lines out gives back the checked hover program exactly (dispense_check, at
writing and again before every run).
"""
import re

DISPENSERS = {
    "pneumatic": {"label": "Pneumatic (air valve)", "start_s": 0.2, "stop_s": 0.1},
    "servo_ssr": {"label": "Servo dispenser (SSR)", "start_s": 0.5, "stop_s": 0.3},
}
DELAY_MIN_S, DELAY_MAX_S = 0.0, 3.0
S_FULL = 1000                     # = $30 on the controller: full output (no PWM), the relay is simply on
PASTE_ON, PASTE_OFF = f"M3 S{S_FULL}", "M5"
LASER_MODE_SETTING, MAX_SPINDLE_SETTING = 32, 30

_G0XY = re.compile(r"^G53 G0 X-?\d+\.\d{3} Y-?\d+\.\d{3}$")
_G0Z = re.compile(r"^G53 G0 Z-?\d+\.\d{3}$")
_G1 = re.compile(r"^G53 G1 X-?\d+\.\d{3} Y-?\d+\.\d{3} Z-?\d+\.\d{3} F\d+(?:\.\d+)?$")


class DispenseError(ValueError):
    pass


def dwell(seconds):
    return f"G4 P{float(seconds):.3f}"


def settings(dispenser, start_s=None, stop_s=None):
    """The chosen dispenser and its delays, checked; None for no paste (a hover program, as before v29)."""
    if dispenser in (None, "", "none"):
        return None
    if dispenser not in DISPENSERS:
        raise DispenseError(f"unknown dispenser {dispenser!r}")
    d = DISPENSERS[dispenser]
    out = {"dispenser": dispenser, "label": d["label"]}
    for key, value, name in (("start_s", start_s, "start"), ("stop_s", stop_s, "stop")):
        try:
            v = d[key] if value in (None, "") else float(value)
        except (TypeError, ValueError):
            raise DispenseError(f"the {name} delay must be a number of seconds") from None
        if not DELAY_MIN_S <= v <= DELAY_MAX_S:
            raise DispenseError(f"the {name} delay must be {DELAY_MIN_S:g} to {DELAY_MAX_S:g} s")
        out[key] = round(v, 3)
    return out


def add(lines, cfg):
    """Checked hover command lines -> the same lines with paste ON after every drop and OFF before every lift."""
    out, on, prev = [], False, None
    for ln in lines:
        if on and not _G1.match(ln):
            if not _G0Z.match(ln):
                raise DispenseError(f"program structure: '{ln}' inside a stroke")
            out += [PASTE_OFF, dwell(cfg["stop_s"])]
            on = False
        out.append(ln)
        if prev is not None and _G0XY.match(prev) and _G1.match(ln):
            out += [PASTE_ON, dwell(cfg["start_s"])]
            on = True
        prev = ln
    if on:
        raise DispenseError("program ends inside a stroke")
    if PASTE_ON not in out:
        raise DispenseError("program has no strokes")
    return out


def added_time_s(cfg, strokes):
    return round(strokes * (cfg["start_s"] + cfg["stop_s"]), 1)


def controller_ok(found):
    """Refusal text unless the controller switches the paste as planned ($32=0, $30=S_FULL), else None."""
    laser, smax = found.get(LASER_MODE_SETTING), found.get(MAX_SPINDLE_SETTING)
    if laser is None or smax is None:
        return "the controller did not report $32 and $30 - connect again and retry"
    try:
        laser, smax = float(laser), float(smax)
    except ValueError:
        return f"the controller reported $32={laser} and $30={smax}, which Studio cannot read"
    if laser != 0:
        return ("the controller is in laser mode ($32=1): it would switch the paste on its own during moves and "
                "stops. Set normal spindle mode once with machine_laser_mode_set.py (--value 0), then retry")
    if smax != S_FULL:
        return (f"the controller's full output is $30={smax:g}, but paste programs switch it with S{S_FULL}: "
                f"set $30={S_FULL} first")
    return None
