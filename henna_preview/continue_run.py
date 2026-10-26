"""Where a stopped run stopped, and the rest of its design (Studio "Re-scan and continue", user 2026-10-26: "whole
process is too slow for someone to rest hand, thats why hand moves" -> "build 1 and 2").

A program's strokes are its runs of drawing lines ("G53 G1 ...", the drop and the drawing moves); travel and hop
moves ("G53 G0 ...") separate them. Paste lines (M3 / G4 / M5) inside a stroke do not split it. The program's strokes
are the design's placed strokes in the same order (step21 plan_design -> the hop program).

stopped_stroke(lines, last_mpos, last_acked): the head's last reported position while the run was running or held
(grbl_program_link_v29 notes it with the acknowledged line count) -> the line being executed then = the line whose
move passes nearest to that position among the acknowledged ones (a few planner blocks behind) -> its stroke.
That stroke is drawn again from its start (it may be half drawn); a stop between strokes continues with the next one.

rest_svg(original_svg_text, from_stroke): the same SVG with FROM_STROKE_ATTR on its root - read_svg / place() keep
the first strokes for the size and the centre and leave them out, so the rest lands exactly where it was planned.
Pure functions (no machine, no files) - Studio v29 does the checks, the scan and the run.
"""
import re

import numpy as np

from henna_preview.svg_design import FROM_STROKE_ATTR, ORIGINAL_ATTR

AXIS_RE = re.compile(r"([XYZ])(-?\d+(?:\.\d+)?)")
NEAR_MM = 1.0                 # the last position must lie this close to a line of the program
BACK_LINES = 120              # look this many acknowledged lines back (the planner queue, with room)


class ContinueError(ValueError):
    pass


def _kind(line):
    if line.startswith("G53 G1 "):
        return "draw"
    if line.startswith("G53 G0 "):
        return "travel"
    return "other"


def strokes_of(lines):
    """-> [(first line index, last line index)] of each stroke (its drawing lines) in program order."""
    out, start, last = [], None, None
    for i, ln in enumerate(lines):
        k = _kind(ln)
        if k == "draw":
            if start is None:
                start = i
            last = i
        elif k == "travel" and start is not None:
            out.append((start, last))
            start = None
    if start is not None:
        out.append((start, last))
    return out


def positions(lines, start=None):
    """-> (N, 3) the head's target after each line (from start, else NaN until X, Y and Z are known); non-moves
    repeat."""
    cur = [float(v) for v in start] if start is not None else [np.nan, np.nan, np.nan]
    out = np.full((len(lines), 3), np.nan)
    for i, ln in enumerate(lines):
        if _kind(ln) != "other":
            for ax, v in AXIS_RE.findall(ln):
                cur["XYZ".index(ax)] = float(v)
        out[i] = cur
    return out


def _seg_dist(p, a, b):
    ab = b - a
    n = float(ab @ ab)
    t = 0.0 if n <= 1e-12 else min(1.0, max(0.0, float((p - a) @ ab) / n))
    return float(np.linalg.norm(p - (a + t * ab)))


def executing_line(lines, last_mpos, last_acked, start=None):
    """-> index of the line being executed at last_mpos (the latest of equally near ones). start: where the run
    began (the first move goes from there)."""
    if last_mpos is None or last_acked is None:
        raise ContinueError("the run did not report where it stopped - start the design again instead")
    pos = np.vstack([np.asarray(start if start is not None else [np.nan] * 3, float)[None], positions(lines, start)])
    p = np.asarray(last_mpos, float)
    hi = min(int(last_acked), len(lines))
    best, best_d = None, None
    for i in range(max(0, hi - BACK_LINES), hi):              # pos[i] = before line i, pos[i + 1] = after it
        if _kind(lines[i]) == "other" or not (np.isfinite(pos[i]).all() and np.isfinite(pos[i + 1]).all()):
            continue
        d = _seg_dist(p, pos[i], pos[i + 1])
        if best_d is None or d <= best_d + 1e-6:
            best, best_d = i, d
    if best is None or best_d > NEAR_MM:
        raise ContinueError("could not tell where in the program the run stopped (the head was "
                            f"{'far from' if best is not None else 'not on'} its moves) - start the design again instead")
    return best


def stopped_stroke(lines, last_mpos, last_acked, start=None):
    """-> (stroke index to continue from, number of strokes, 'redo' | 'next' | 'done')."""
    st = strokes_of(lines)
    if not st:
        raise ContinueError("the program has no strokes")
    e = executing_line(lines, last_mpos, last_acked, start)
    pos = positions(lines, start)
    for n, (a, b) in enumerate(st):
        if e < a:
            return n, len(st), "next"                   # on the way to stroke n: nothing of it drawn yet
        if a <= e <= b:
            if e == b and np.linalg.norm(pos[b] - np.asarray(last_mpos, float)) < 0.01:
                return (n + 1, len(st), "next") if n + 1 < len(st) else (len(st), len(st), "done")
            return n, len(st), "redo"
    return len(st), len(st), "done"


def rest_svg(svg_text, from_stroke, original_rel):
    """The SVG text with the first from_stroke strokes marked as drawn. A design that is itself a rest keeps counting
    from its own original (its strokes are numbered after the ones it already left out)."""
    m = re.search(r"<svg\b[^>]*>", svg_text)
    if not m:
        raise ContinueError("the design is not an SVG")
    tag = m.group(0)
    done = re.search(FROM_STROKE_ATTR + r'="(\d+)"', tag)
    base = int(done.group(1)) if done else 0
    orig = re.search(ORIGINAL_ATTR + r'="([^"]*)"', tag)
    original_rel = orig.group(1) if orig else original_rel
    clean = re.sub(r'\s(?:' + FROM_STROKE_ATTR + "|" + ORIGINAL_ATTR + r')="[^"]*"', "", tag)
    new_tag = clean[:-1].rstrip("/").rstrip() + (f' {FROM_STROKE_ATTR}="{base + int(from_stroke)}"'
                                                  f' {ORIGINAL_ATTR}="{original_rel}"') + ("/>" if clean.endswith("/>") else ">")
    return svg_text[:m.start()] + new_tag + svg_text[m.end():], base + int(from_stroke), original_rel


def original_of(svg_text, own_rel):
    """The design a rest was made from (its own path when it is not a rest)."""
    m = re.search(r"<svg\b[^>]*>", svg_text)
    orig = re.search(ORIGINAL_ATTR + r'="([^"]*)"', m.group(0)) if m else None
    return orig.group(1) if orig else own_rel
