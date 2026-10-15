"""Independent re-read of a paste program (Studio v29). Written apart from dispense.add and shares none of its code.

A paste program is accepted only when:
  1. taking out every paste line (M3 S<full>, M5, G4 P<s>) gives back the checked hover program, line for line;
  2. paste ON (M3) comes only directly after a drop - a G1 straight down from the G0 just before it - and that
     drop ends below travel Z;
  3. ON is followed by the start delay, OFF by the stop delay and then the lift (G0 Z);
  4. while ON there are only G1 drawing moves: never a G0 (travel, hop or lift) with paste ON;
  5. the program ends with paste OFF, and every stroke of the hover program has exactly one ON and one OFF.
Raises ValueError with the first problem; returns the counts.
"""
import re

_NUM = r"(-?\d+\.\d{3})"
G0XY = re.compile(rf"^G53 G0 X{_NUM} Y{_NUM}$")
G0Z = re.compile(rf"^G53 G0 Z{_NUM}$")
G1 = re.compile(rf"^G53 G1 X{_NUM} Y{_NUM} Z{_NUM} F(\d+(?:\.\d+)?)$")
ON = re.compile(r"^M3 S(\d+)$")
OFF = re.compile(r"^M5$")
DWELL = re.compile(r"^G4 P(\d+\.\d{3})$")


def check(paste_lines, hover_lines, cfg, travel_z, s_full):
    paste_lines, hover_lines = list(paste_lines), list(hover_lines)
    if any(ON.match(x) or OFF.match(x) or DWELL.match(x) for x in hover_lines):
        raise ValueError("the hover program already has paste commands")
    kept = [x for x in paste_lines if not (ON.match(x) or OFF.match(x) or DWELL.match(x))]
    if kept != hover_lines:
        raise ValueError("the paste program is not the checked hover program with paste lines added")
    start, stop = f"{float(cfg['start_s']):.3f}", f"{float(cfg['stop_s']):.3f}"
    on, ons, offs, last_g0xy = False, 0, 0, None
    for i, x in enumerate(paste_lines):
        prev = paste_lines[i - 1] if i else ""
        nxt = paste_lines[i + 1] if i + 1 < len(paste_lines) else ""
        m = ON.match(x)
        if m:
            if on:
                raise ValueError(f"line {i + 1}: paste ON while already ON")
            if int(m.group(1)) != int(s_full):
                raise ValueError(f"line {i + 1}: paste ON at S{m.group(1)}, not S{s_full}")
            drop = G1.match(prev)
            if not (drop and last_g0xy and i >= 2 and paste_lines[i - 2] == last_g0xy[0]):
                raise ValueError(f"line {i + 1}: paste ON not directly after a drop to the skin")
            if (drop.group(1), drop.group(2)) != last_g0xy[1]:
                raise ValueError(f"line {i + 1}: paste ON after a move that is not straight down")
            if not float(drop.group(3)) < float(travel_z) - 1.0:
                raise ValueError(f"line {i + 1}: paste ON at Z {drop.group(3)}, not below travel Z {travel_z}")
            if DWELL.match(nxt) is None or DWELL.match(nxt).group(1) != start:
                raise ValueError(f"line {i + 1}: paste ON is not followed by the {start} s start delay")
            on, ons = True, ons + 1
            continue
        if OFF.match(x):
            if not on:
                raise ValueError(f"line {i + 1}: paste OFF while not ON")
            if DWELL.match(nxt) is None or DWELL.match(nxt).group(1) != stop:
                raise ValueError(f"line {i + 1}: paste OFF is not followed by the {stop} s stop delay")
            after = paste_lines[i + 2] if i + 2 < len(paste_lines) else ""
            if not G0Z.match(after):
                raise ValueError(f"line {i + 1}: paste OFF is not followed by the lift")
            on, offs = False, offs + 1
            continue
        if DWELL.match(x):
            if not (ON.match(prev) or OFF.match(prev)):
                raise ValueError(f"line {i + 1}: a delay that does not belong to paste ON or OFF")
            continue
        if on and not G1.match(x):
            raise ValueError(f"line {i + 1}: '{x}' with paste ON - only drawing moves may run with paste ON")
        g = G0XY.match(x)
        if g:
            last_g0xy = (x, (g.group(1), g.group(2)))
    if on:
        raise ValueError("the program ends with paste ON")
    strokes = sum(1 for x in hover_lines if G0XY.match(x))
    if not (ons == offs == strokes) or strokes == 0:
        raise ValueError(f"{ons} paste ON and {offs} OFF for {strokes} strokes")
    return {"strokes": strokes, "paste_on": ons, "paste_off": offs, "start_s": float(start), "stop_s": float(stop),
            "problems": 0}
