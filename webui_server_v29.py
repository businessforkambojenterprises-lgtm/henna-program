"""Henna Studio v29 = v28 + PASTE DISPENSING (user approval 2026-10-15: "go A").

Two dispensers, both on the controller's spindle/laser output through a relay: Pneumatic (a solenoid air valve) and
Servo dispenser (its driver started by an SSR). For the controller both are M3 S1000 = paste ON, M5 = paste OFF.

- Program step: Paste = None (hover only, as v28) / Pneumatic / Servo dispenser, with a start and a stop delay.
  The hover program is written and checked exactly as in v28; the paste lines are then added (dispense.add: ON after
  every drop to the skin, OFF before every lift) and the result is re-read by dispense_check, which shares no code
  with it: taking the paste lines out must give back the checked hover program, paste ON only after a drop below
  travel Z, never a G0 with paste ON, ends OFF, one ON and one OFF per stroke. Saved as its own file with its own
  SHA-256; the checked hover file stays beside it.
- Run: the checks of every run, then - before anything is sent - the same paste re-read again, and the controller's
  settings are read ($$): laser mode must be off ($32=0) and the full output $30 = 1000, else refused.
  Pause (screen, push button, hand watch) also stops the paste; Resume turns it on, waits the start delay, then
  moves on (grbl_program_link_v29). STOP, errors and closing Studio reset the controller, which turns it off.
- Dispenser test: 0.2 to 3 s of paste with the machine still (same settings check).
- On the Pi the SSR can be on a GPIO pin instead (user 2026-10-15: "B"): webui_server_pi sets machine.paste_output;
  the same program file, the link switches the pin after "G4 P0" (moves finished) instead of sending M3/M5, and the
  laser-mode check is not needed.
v1-v28 files are unchanged; the laptop runs this file, the Pi's webui_server_pi.py switches to it at the next merge.
"""
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sys
import threading
import time
from urllib.parse import unquote, urlparse

import numpy as np

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v17 as v17
import webui_server_v27 as v27
import webui_server_v28 as v28
from henna_preview import dispense, dispense_check
from henna_preview.grbl_program_link import MachineError
from henna_preview.grbl_program_link_v29 import GrblProgramLinkV29

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v29.html")
TEST_S = 1.0
PASTE_NAME = re.compile(r"^DESIGN_PASTE_[0-9-]+\.gcode$")

machine = v28.machine
machine.__class__ = GrblProgramLinkV29          # adds $$ read, paste stop in pauses, the dispenser test
HOVER_CHECKED = v17.checked_program             # v17's checks of the hover file, unchanged

# Closer nozzle heights for PROSTHETIC jobs (user 2026-10-17: "give option for 1 and 2 mm", step 1 of 3 - measure on the
# prosthetic first). 1.0 to 6.0 mm in the Design step; the program floor "never below skin + 1 mm" is unchanged.
# Real-hand jobs keep v28's 6 mm minimum (checked at Design, Program and Run) and 300 mm/min. The approved
# calibration files are not changed: only the lower end of their nozzle-height range is lowered here.
PROSTHETIC_MIN_STANDOFF_MM = 1.0
_CAL = None


def cal():
    c = dict(_CAL())
    lo, hi = c["standoff_mm_allowed"]
    c["standoff_mm_allowed"] = [min(float(lo), PROSTHETIC_MIN_STANDOFF_MM), float(hi)]
    return c


# ---------- travel clearance over the nozzle's own path (user 2026-10-18: "do 2") ----------
# v27 checked the whole hand's highest point - on a person usually the wrist / forearm, which the tip never crosses.
# Now: the surface under the tip's travel path (design region + way from the scan position, 8 mm margin), measured
# with the planner's own model; refused when that path is not measured well enough (henna_preview/path_clearance.py).
# Steps 22 / 23 get the same number (step_path_clearance.py runs the approved scripts unchanged).
STEP22_PATH = os.path.join(base.ROOT, "step22_path_clearance.py")
STEP23_PATH = os.path.join(base.ROOT, "step23_path_clearance.py")
_frames = {}


def _scan_frame(rec):
    if rec not in _frames:
        _frames.clear()
        _frames[rec] = v28.v26._median_frame(rec)
    return _frames[rec]


# The design placement being checked (check_design): the job still holds the previous one until the check passes.
_PENDING = threading.local()


def design_strokes(design):
    """(start XY, end XY) of each stroke in program order for a placement {svg, fit_mm, rotation_deg,
    centre_machine_xy} - the same place() as the program (step21 plan_design); a closed stroke ends at its start."""
    from henna_preview.svg_design import place, read_svg
    d = read_svg(os.path.join(base.ROOT, design["svg"]))
    centre = [float(v) for v in design["centre_machine_xy"]]
    placed, _ = place(d, float(design["fit_mm"]), centre, float(design.get("rotation_deg") or 0.0), spacing_mm=0.5)
    return [(tuple(s["plan"][0]), tuple(s["plan"][0] if s["closed"] else s["plan"][-1])) for s in placed]


def travel_plan(job, design):
    """-> {"starts", "strokes"} of the program for this placement, or None (then the hull rule applies): it may start
    from the scan position - and from the head's current position when the machine reports one."""
    if not design:
        return None
    try:
        strokes = design_strokes(design)
    except (KeyError, TypeError, ValueError, OSError):
        return None
    if not strokes:
        return None
    starts = [[float(v) for v in job["steps"]["scan"]["mpos"][:2]]]
    try:
        st = machine.status()
        if st.get("connected") and st.get("mpos"):
            starts.append([float(v) for v in st["mpos"][:2]])
    except Exception:                                   # noqa: BLE001 - no machine: the scan position only
        pass
    return {"starts": starts, "strokes": strokes}


def travel_lines(job, design, approach=()):
    """The travel-height moves of the program for this placement, or None: from the start(s) through the approach
    waypoints to the first stroke, then between strokes."""
    from henna_preview import path_clearance as pc
    plan = travel_plan(job, design)
    if plan is None:
        return None
    return pc.travel_segments(plan["starts"], plan["strokes"], approach) or None


# The route of the first travel move each job's last path check chose (user 2026-10-22, "build step 2"): the program
# step writes it, and a run is refused when its program's route is a different one.
_LAST_APPROACH = {}
_LAST_GAPS = {}                                         # the same for the jumps between strokes (2026-10-24)


def _gap_key(gaps):
    """Jumps round tall skin, comparable: sorted ((from), (waypoints...), (to)) rounded to 0.001 mm."""
    def r(p):
        return tuple(round(float(v), 3) for v in p)
    out = []
    for g in gaps:
        if isinstance(g, dict):
            g = (g["from"], g["waypoints"], g["to"])
        out.append((r(g[0]), tuple(r(w) for w in g[1]), r(g[2])))
    return sorted(out)


def _same_gaps(a, b, tol=0.011):
    """Same jumps: the same waypoints (written from the check's numbers) between stroke ends that agree to 0.01 mm
    (the program's stroke ends are the placement's, as for the first move's target)."""
    if len(a) != len(b):
        return False
    for (fa, wa, ta), (fb, wb, tb) in zip(a, b):
        if wa != wb or max(abs(x - y) for x, y in zip(fa + ta, fb + tb)) > tol:
            return False
    return True


def path_measure(job, design=None, standoff_mm=None):
    """The nozzle-path corridor of a job with a scan and a region: the measured surface (sub-sampled pixels) and
    pc.corridor_top's result. Raises tc.ClearanceError when the path cannot be measured. Also used by
    path_clearance_picture.py, so the picture shows exactly what the design check measured.
    design: the placement to check (default: the job's); its travel lines are the corridor (pc.travel_segments).
    Jumps between strokes that cross skin too tall for travel Z go round it (travel_route.choose_gap, 2026-10-24);
    standoff_mm sets that limit (default: the placement's, else the real-hand 6 mm)."""
    import cv2
    from henna_preview import path_clearance as pc
    from henna_preview import travel_clearance as tc
    from henna_preview.head_mask import head_pixels
    s = job["steps"]
    rec = s["scan"]["recording"]
    fr = _scan_frame(rec)
    raw, scale = fr["depth_raw_aligned"], fr["depth_scale_m_per_unit"]
    p = v28._parts()
    head = [float(v) for v in s["scan"]["mpos"]]
    stride = v28.REF_STRIDE
    hm = head_pixels(raw, scale, p["k"], p["depth_fn"])
    sub, keep = raw[::stride, ::stride], ~hm[::stride, ::stride]
    rays = v28.rays_for(fr["colour_intrinsics"], sub.shape, stride)
    xy, z, xyb, ok = pc.surface(sub, scale, rays, head, p["R"], p["t"], p["k"], p["depth_fn"], keep)
    region = np.zeros(raw.shape, np.uint8)
    cv2.fillPoly(region, [np.round(np.asarray(s["region"]["polygon_px"], float)).astype(np.int32)], 1)
    region_ok = region[::stride, ::stride].astype(bool)[ok]
    blind_mask = keep & (sub == 0)
    d_med = float(np.median(sub[keep & (sub > 0)])) * float(scale) * 1000.0
    mm_per_px = float(abs(rays[0][0, 1] - rays[0][0, 0])) * d_med
    plan = travel_plan(job, design if design is not None else s.get("design"))
    approach, rated, gaps = [], [], {}
    if plan:
        from henna_preview import grbl_program_link as gpl
        from henna_preview import travel_route
        keep = np.hypot(*(xy - np.asarray(head[:2])).T) > pc.HEAD_FOOTPRINT_MM
        approach, _, rated = travel_route.choose(head[:2], plan["strokes"][0][0], xy, z, pc.MARGIN_MM, keep)
        if standoff_mm is None:
            standoff_mm = (design or s.get("design") or {}).get("standoff_mm") or v28.REAL_MIN_STANDOFF_MM
        limit = float(v10.cal()["travel_z"]) - (max(float(standoff_mm), float(v27.HOP_CLEARANCE_MM)) + tc.MARGIN_MM)
        st = plan["strokes"]
        for i in range(len(st) - 1):
            w, _ = travel_route.choose_gap(st[i][1], st[i + 1][0], xy, z, pc.MARGIN_MM, limit, keep,
                                           (gpl.X_MAX, gpl.Y_MAX))
            if w:
                gaps[i] = w
    segments = pc.travel_segments(plan["starts"], plan["strokes"], approach, gaps) if plan else None
    try:
        r = pc.corridor_top(xy, z, xyb, region_ok, head[:2], blind_mask=blind_mask, mm_per_px=mm_per_px, ok_mask=ok,
                            segments=segments)
    except pc.PathClearanceError as e:
        raise tc.ClearanceError(f"Nozzle path check: {e}") from None
    r = dict(r, approach=[[round(float(v), 3) for v in w] for w in approach],
             gaps=[{"from": [round(float(v), 3) for v in plan["strokes"][i][1]],
                    "to": [round(float(v), 3) for v in plan["strokes"][i + 1][0]],
                    "waypoints": [[round(float(v), 3) for v in w] for w in gaps[i]]} for i in sorted(gaps)],
             approach_candidates=[{"waypoints": [[round(float(v), 1) for v in w] for w in c],
                                   "top_z": None if t is None else round(t, 2)} for c, t in rated])
    target = [round(float(v), 3) for v in plan["strokes"][0][0]] if plan else None
    return {"r": r, "frame": fr, "stride": stride, "ok": ok, "xy": xy, "z": z, "region_ok": region_ok,
            "head": head, "parts": p, "recording": rec, "target": target}


def path_clearance(job, standoff_mm):
    """v27.clearance with the nozzle's path instead of the whole hand. None before the region step (as v27)."""
    from henna_preview import travel_clearance as tc
    s = job["steps"]
    if not s.get("region") or not s.get("scan"):
        return _WHOLE_HAND_CLEARANCE(job, standoff_mm)
    m = path_measure(job, getattr(_PENDING, "design", None), standoff_mm)
    r, rec, p = m["r"], m["recording"], m["parts"]
    c = v10.cal()
    bed = v27._paper_z(c, p["k"])
    os.environ["HENNA_PATH_TOP"] = json.dumps({"recording": os.path.basename(rec), "top_z": r["top_z"],
                                               "basis": r["basis"]})
    os.environ["HENNA_APPROACH"] = json.dumps({"recording": os.path.basename(rec), "target": m["target"],
                                               "waypoints": r["approach"], "gaps": r["gaps"]})
    _LAST_APPROACH[job["id"]] = [tuple(w) for w in r["approach"]]
    _LAST_GAPS[job["id"]] = _gap_key(r["gaps"])
    try:
        out = tc.check(r["top_z"], c["travel_z"], standoff_mm, v27.HOP_CLEARANCE_MM, bed_z=bed)
    except tc.ClearanceError as e:
        msg = str(e)
        if "too tall" in msg:
            gap = max(float(standoff_mm), float(v27.HOP_CLEARANCE_MM)) + tc.MARGIN_MM
            above = f" (about {r['top_z'] - bed:.1f} mm above the bed)" if bed is not None else ""
            what = ("the nozzle's travel moves - to the first stroke and between strokes" if r.get("segments")
                    else "the design area and the way to it from the scan position")
            msg = (f"Under the nozzle's path ({what}, {r['margin_mm']:g} mm margin) the highest point is at "
                   f"Z {r['top_z']:.1f}{above}"
                   + (f", near machine X{r['top_xy'][0]:.0f} Y{r['top_xy'][1]:.0f}" if r["top_xy"] else
                      " (the edge of a patch without depth)")
                   + f"; travel height Z {float(c['travel_z']):g} must be at "
                   f"least {gap:g} mm above it. Choose a design area on a lower part of the hand, or move the hand so "
                   "its tall part (wrist, forearm, thumb) is away from that path.")
        raise tc.ClearanceError(msg) from None
    return dict(out, basis=r["basis"], path=r)


_WHOLE_HAND_CLEARANCE = v27.clearance
_CHECK_DESIGN = v28.Handler.POST_API["/api/studio/check_design"]


def _scan_time(job):
    """When the job's scan was recorded: the recording's name (YYYYMMDD_HHMMSS_...), else its file time; or None."""
    sc = job["steps"].get("scan") or {}
    m = re.match(r"^(\d{8})_(\d{6})", os.path.basename(str(sc.get("name") or sc.get("recording") or "")))
    if m:
        return dt.datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    try:
        return dt.datetime.fromtimestamp(os.path.getmtime(sc["recording"]))
    except (KeyError, OSError, TypeError):
        return None


def _z_home_check(job):
    """A scan made before the Z home switch was moved (machine_z_home_moved.py, user 2026-10-24) measured its heights
    in the old Z numbers - planned now, every height would be off by that much. Refused; a new scan is needed."""
    c = v10.cal()
    at = c.get("z_home_moved_at")
    if not at or not (job["steps"].get("scan")):
        return
    t = _scan_time(job)
    if t is None or t < dt.datetime.fromisoformat(at):
        raise ValueError(f"this job's scan was made before the Z home switch was moved ({at.replace('T', ' ')}) - its "
                         "heights are in the old Z numbers. Start a new job (or record the scan again)")


_REFERENCE = v28.reference


def reference_with_gap(job):
    """The hand watch's reference with the job's nozzle height, so the watch judges the live gap under the nozzle while
    it draws (hand_watch GAP_MIN_MM / GAP_MAX_MM; user 2026-10-24: "build A, pause below 4 mm or above 9 mm")."""
    ref = _REFERENCE(job)
    ref.standoff_mm = float((job["steps"].get("design") or {}).get("standoff_mm") or v28.REAL_MIN_STANDOFF_MM)
    return ref


v28.reference = reference_with_gap                          # v28.run's WATCH.start(job, reference(job)) - real hands


def check_design(params):
    """The design check, with its placement known to the travel-line clearance while it runs."""
    p = params or {}
    try:
        job = v11.job_load(p.get("job"))
    except ValueError:
        job = None                                          # the design check itself answers "unknown job"
    if job:
        _z_home_check(job)
    _PENDING.design = {k: p.get(k) for k in ("svg", "fit_mm", "rotation_deg", "centre_machine_xy")}
    try:
        return _CHECK_DESIGN[0](params)
    finally:
        _PENDING.design = None


def install_path_clearance():
    """Applied when Studio v29 starts from source (main()). Not in the packaged desktop app until it is rebuilt with
    the new step runner (its steps 22 / 23 are frozen workers there) - it keeps the whole-hand rule."""
    if getattr(sys, "frozen", False):
        return False
    v27.clearance = path_clearance
    v10.STEP22, v17.STEP23 = STEP22_PATH, STEP23_PATH
    return True


def install_close_heights():
    """Applied when Studio v29 starts (main(), also on the Pi and in the desktop app) - not on import, so the v14 to
    v28 files and their tests keep their own 4 mm rule."""
    global _CAL
    v14.MIN_STANDOFF_MM = PROSTHETIC_MIN_STANDOFF_MM
    if v10.cal is not cal:
        _CAL = v10.cal
        v10.cal = cal


# ---- jog distance from a slider (user 2026-10-18: "jog should be like mks web ui fo distance there has to be a slider
# rather than fixed numbers"): any distance from 0.1 to 100 mm in 0.1 mm. Only the step rule changes - the link still
# refuses Z above the top or below travel height, X/Y outside the machine or below travel height, as for every step.
JOG_MIN_MM, JOG_MAX_MM = 0.1, 100.0


def free_jog_step(step):
    try:
        s = float(step)
    except (TypeError, ValueError):
        return False
    return JOG_MIN_MM - 1e-9 <= s <= JOG_MAX_MM + 1e-9 and abs(s * 10 - round(s * 10)) < 1e-6


def install_free_jog():
    """Applied when Studio v29 starts (main()) - not on import, so the v14 to v28 files and their tests keep 0.1/1/10."""
    from henna_preview import grbl_program_link as gl
    text = f"step {JOG_MIN_MM:g} to {JOG_MAX_MM:g} mm in 0.1 mm steps"
    v14.jog_step_ok = gl.jog_step_ok = free_jog_step
    v14.JOG_STEP_TEXT = gl.JOG_STEP_TEXT = text


def _log(step, mpos, command, expected, measured, result, recording="", notes="Studio v29"):
    v10.log_row(v10.LOG_FILE, 0, step, mpos, command, expected, measured, result, recording, notes)


def _same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _command_lines(text):
    return [ln for ln in text.splitlines() if ln and not ln.startswith("(")]


# ---------- the paste program ----------
def _route_check(job, lines):
    """The program's route to its first stroke must be the one the path check just measured (it ran first in the
    same request: v27's guard / v28's real-hand rules)."""
    from henna_preview import travel_route
    travel = v10.cal()["travel_z"]
    got = [tuple(round(float(v), 3) for v in w) for w in travel_route.program_approach(lines, travel)]
    got_gaps = _gap_key(travel_route.program_gaps(lines, travel))
    want = _LAST_APPROACH.get(job["id"])
    if v27.clearance is not path_clearance or want is None:
        if got or got_gaps:
            raise ValueError("this program goes round tall skin between its strokes or on its way to the first one, "
                             "which only the path check can approve - write the program again")
        return
    if got != [tuple(round(float(v), 3) for v in w) for w in want]:
        raise ValueError("the program's route to the first stroke is not the one checked now (the hand or the design "
                         "changed) - write the program again")
    if not _same_gaps(got_gaps, _LAST_GAPS.get(job["id"], [])):
        raise ValueError("the program's jumps round tall skin between strokes are not the ones checked now (the hand "
                         "or the design changed) - write the program again")


def checked_program(job):
    """v17's checks of the hover file; for a paste program also its own file, fingerprint and the paste re-read.
    Installed as v17.checked_program, which every run start calls (v20.run_start)."""
    name, lines, end, rr, feed = HOVER_CHECKED(job)
    _route_check(job, lines)
    pr = job["steps"]["program"]
    d = pr.get("dispense")
    if not d:
        return name, lines, end, rr, feed
    pd = v10.out_dir("program", pr["program"])
    path = d["file"]
    if not _same(os.path.dirname(path), pd):
        raise ValueError("the paste program is not the one written for this job")
    with open(path, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != d["sha256"]:
        raise ValueError("the paste program changed after it was written - write it again in step 4")
    cfg = dispense.settings(d["dispenser"], d["start_s"], d["stop_s"])
    paste = _command_lines(raw.decode("ascii"))
    if paste != dispense.add(lines, cfg):
        raise ValueError("the paste program does not match its checked hover program - write it again in step 4")
    chk = dispense_check.check(paste, lines, cfg, v10.cal()["travel_z"], dispense.S_FULL)
    return os.path.basename(path), paste, end, dict(rr, paste=chk), feed


v17.checked_program = checked_program


def program(params):
    _z_home_check(v11.job_load(params.get("job")))
    cfg = dispense.settings(params.get("dispenser"), params.get("start_delay_s"), params.get("stop_delay_s"))
    out = v28.program(params)                                # the hover program, written and checked as in v28
    if cfg is None:
        return out
    job = v11.job_load(params.get("job"))
    try:
        name, lines, _, _, _ = HOVER_CHECKED(job)
        c = v10.cal()
        paste = dispense.add(lines, cfg)
        chk = dispense_check.check(paste, lines, cfg, c["travel_z"], dispense.S_FULL)
        pr = job["steps"]["program"]
        pd = v10.out_dir("program", pr["program"])
        gname = "DESIGN_PASTE_" + name.split("DESIGN_HOVER_TEST_PROSTHETIC_", 1)[-1]
        subject = {"prosthetic": "PROSTHETIC HAND", v28.REAL: "REAL HAND - WATCHED RUN"}.get(job["subject"], job["subject"])
        header = [f"(DESIGN WITH PASTE - {cfg['label'].upper()} - {subject})",
                  f"(made {dt.datetime.now():%Y%m%d-%H%M%S} by Studio v29 from {name}, sha256 {pr['sha256'][:16]})",
                  f"(paste ON M3 S{dispense.S_FULL} after each drop, start delay {cfg['start_s']:g} s; OFF M5 before "
                  f"each lift, stop delay {cfg['stop_s']:g} s - {chk['strokes']} strokes)",
                  "(paste output checked by Studio before the run: a Pi pin, or the controller with $32=0, $30=1000)",
                  "(Run from Studio only - home first - stand at the emergency stop)"]
        gfile = os.path.join(pd, gname)
        with open(gfile, "w", encoding="ascii", newline="\n") as f:
            f.write("\n".join(header + paste) + "\n")
        with open(gfile, "rb") as f:
            sha = hashlib.sha256(f.read()).hexdigest()
        added = dispense.added_time_s(cfg, chk["strokes"])
        pr["dispense"] = dict(cfg, file=gfile, sha256=sha, strokes=chk["strokes"], added_s=added, check=chk,
                              download=f"/download_program/{os.path.basename(pd)}/{gname}")
        pr["estimated_run_time_s"] = round(float(pr["estimated_run_time_s"]) + added, 1)
        v11.job_save(job)
        checked_program(job)                                 # exactly what a run will check, once now
    except Exception:
        job = v11.job_load(params.get("job"))
        job["steps"].pop("program", None)                    # never leave a hover program where paste was chosen
        v11.job_save(job)
        raise
    _log("Studio v29 paste program", None, "Studio Write program", "paste lines added and re-read",
         f"{gname}: {cfg['label']}, {chk['strokes']} strokes, start {cfg['start_s']:g} s, stop {cfg['stop_s']:g} s, "
         f"+{added:g} s; re-read {chk['problems']} problems", "generated - not yet run", job["steps"]["scan"]["name"],
         f"Studio v29; job {job['id']}; {job['subject']}")
    return {"job": v14.job_view(job)}


# ---------- the controller ----------
def output_for(dispenser):
    """Where this Paste choice is switched: the Pi's output (webui_server_pi sets machine.paste_output - the
    pinch-valve servo) for the Servo dispenser, the DLC32's TTL output (M3 / M5) for Pneumatic and whenever no Pi
    output is set (user 2026-10-16: pneumatic must go through the DLC32, not the Pi pin)."""
    return "pi" if machine.paste_output is not None and dispenser == "servo_ssr" else "controller"


def controller_ready(output=None):
    """The paste output is ready: the Pi output, or the controller's output with laser mode off and full output 1000
    (read with $$ right now). output None = the Pi output when one is set (the earlier rule)."""
    pin = machine.paste_output if output in (None, "pi") else None
    if pin is not None:
        if not getattr(pin, "ready", True):
            raise ValueError(f"Paste refused: the Pi paste pin is not working ({getattr(pin, 'error', '')})")
        return {"output": pin.name}
    found = machine.read_settings()
    bad = dispense.controller_ok(found)
    if bad:
        raise ValueError("Paste refused: " + bad)
    return found


def _where(found):
    return f"Pi pin {found['output']}" if "output" in found else f"controller $32={found.get(32)} $30={found.get(30)}"


def _prosthetic_start(job, params):
    """v28's run for a prosthetic job, with the hand check before Start as a WARNING only (owner 2026-10-16: "yes do
    2"): a moved prosthetic can only touch plastic. The numbers are logged and shown; Start goes ahead. Real-hand
    jobs keep v28's refusal (and the live watch)."""
    v28.machine_job_only(job)
    if params.get("confirm_clear") is not True or params.get("confirm_estop") is not True:
        raise ValueError("Confirm that nothing else is in the travel area and that you are at the emergency stop")
    warning = None
    try:
        res = v28.hand_check(job)
        measured = (f"prosthetic: median {res['median_dz_mm']} mm, {100 * res['share_off']:.0f} % off, "
                    f"{100 * res['seen_share']:.0f} % seen")
    except MachineError:
        raise                                               # not connected: nothing is started
    except Exception as e:                                  # noqa: BLE001 - moved / unseen / no picture: warn only
        warning = str(e).replace("Hand check:", "Hand check (warning only, prosthetic):", 1)
        measured = str(e)
    out = v28._INNER["/api/machine/run"](dict(params, confirm_prosthetic=True, confirm_unmoved=True))
    _log("Hand check before Start", (machine.status() or {}).get("mpos"), "camera, 3 pictures",
         "hand where it was scanned", measured, "ok" if warning is None else "WARNING - started anyway (prosthetic)",
         job["steps"]["scan"]["name"], f"Studio v29; job {job['id']}; prosthetic: warning only (owner 2026-10-16)")
    return dict(out, hand_check_warning=warning) if warning else out


# ---- (the air run - Start with no hand, the camera requiring the design area empty - was removed, user 2026-10-27:
# "remove air run from every option"; run() refuses it.)
def resume(params):
    return _RESUME[0](params)


_RESUME = v28.Handler.POST_API["/api/machine/resume"]


# ---- (the hand watch during a run and the live height following it fed were removed, user 2026-10-27: "completely
# remove this feature" -> "The whole hand watch"; henna_preview/follow.py and hand_watch.py are no longer used for runs.)
def follow_view(_=None):
    return {"active": False, "dz_mm": None, "stats": None}


def _real_start(job, params):
    """v28's real-hand start - every program rule and the hand check right before Start (the hand where it was
    scanned) - WITHOUT the hand watch during the run (user 2026-10-27: "completely remove this feature" -> "The whole
    hand watch": no camera pauses while it runs - no "too near", "hand moved" or "cannot see") and so without live
    height following, which needs the watch's pictures. The program's own heights and the emergency stop remain."""
    v28.machine_job_only(job)
    if params.get("confirm_clear") is not True or params.get("confirm_estop") is not True:
        raise ValueError("Confirm that nothing else is in the travel area and that you are at the emergency stop")
    v28._real_rules(job, (job["steps"].get("design") or {}).get("standoff_mm", 0))
    if float((job["steps"].get("program") or {}).get("draw_feed_mm_min", v28.REAL_FEED_MM_MIN)) > v28.REAL_FEED_MM_MIN:
        raise ValueError(f"A real hand runs at {v28.REAL_FEED_MM_MIN} mm/min - write the program again")
    res = v28.hand_check(job)
    out = v28._INNER["/api/machine/run"](dict(params, confirm_prosthetic=True, confirm_unmoved=True))
    _log("Hand check before Start", (machine.status() or {}).get("mpos"), "camera, 3 pictures",
         "hand where it was scanned", f"realhand: median {res['median_dz_mm']} mm, {100 * res['share_off']:.0f} % off, "
         f"{100 * res['seen_share']:.0f} % seen", "ok - no hand watch during the run", job["steps"]["scan"]["name"],
         f"Studio v29; job {job['id']}")
    return out


def run(params):
    job = v11.job_load(params.get("job"))
    _z_home_check(job)
    if params.get("air_run"):
        raise ValueError("Air runs were removed from Studio - a run starts with a hand (or the prosthetic) in place")
    d = (job["steps"].get("program") or {}).get("dispense")
    if d:
        cfg = dict(dispense.settings(d["dispenser"], d["start_s"], d["stop_s"]), output=output_for(d["dispenser"]))
        found = controller_ready(cfg["output"])
        machine.arm_dispense(cfg)
    try:
        if job.get("subject") == "prosthetic":
            out = _prosthetic_start(job, params)
        else:
            out = _real_start(job, params)
    finally:
        machine.arm_dispense(None)                           # start_program took it, or the run was refused
        if hasattr(machine, "arm_follow"):
            machine.arm_follow(None, 0.0, 0.0)
    if d:
        _log("Paste run start", (machine.status() or {}).get("mpos"), "$$ then the paste program",
             "paste output ready", f"{_where(found)}; {d['label']}, "
             f"{d['strokes']} strokes", "started", job["steps"]["scan"]["name"], f"Studio v29; job {job['id']}")
    return out


# ---- Quick re-scan (user 2026-10-22: "it takes too much time" to redo scan / hand / region / design / program after
# the hand moved). One request does them in order through the page's own routes, with every guard: (move to the scan
# position when the head is elsewhere and the straight way there is clear), scan, hand detection, the outline and
# the design moved WITH the hand (henna_preview/follow_hand.py), the design check on the NEW scan, and the program
# again when there was one. It stops at the first refusal with that step's message.
RESCAN_SECONDS = 3


def _route(path):
    return globals()["Handler"].POST_API[path][0]          # looked up now: on the Pi, its own handler's routes


def _hand_report(job):
    run = str(job["steps"]["hand"]["run"]).split(":", 1)[-1]
    with open(os.path.join(base.OUT_ROOT, run, "report.json"), encoding="utf-8") as f:
        return json.load(f)


def _way_to_scan_position_clear(job, st):
    """The scan-position route rises to travel Z and goes STRAIGHT over the hand: check that line on the last scan."""
    from henna_preview import path_clearance as pc
    from henna_preview import travel_clearance as tc
    m = path_measure(job)
    a, b = [float(v) for v in st["mpos"][:2]], [float(v) for v in job["steps"]["scan"]["mpos"][:2]]
    keep = np.hypot(*(m["xy"] - np.asarray(b)).T) > pc.HEAD_FOOTPRINT_MM
    near = pc.near_segments(m["xy"], [(tuple(a), tuple(b))], pc.MARGIN_MM) & keep
    if not near.any():
        return
    top = float(np.percentile(m["z"][near], pc.TOP_PERCENTILE))
    c = v10.cal()
    standoff = float((job["steps"].get("design") or {}).get("standoff_mm", 6.0))
    gap = max(standoff, float(v27.HOP_CLEARANCE_MM)) + tc.MARGIN_MM
    if top > float(c["travel_z"]) - gap + 1e-9:
        raise ValueError(f"the straight way to the scan position crosses skin at Z {top:.1f} - take the hand away, "
                         "move to the scan position, put the hand back, then Quick re-scan")


def _wait_at_scan_position(timeout_s=120.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        st = machine.status()
        if not st.get("busy"):
            if st.get("capture_ready"):
                return st
            raise MachineError("the move to the scan position did not end there - home and try again")
        time.sleep(0.25)
    raise MachineError("the move to the scan position took too long")


def _placement_of(job, need_pass=False):
    """What Quick re-scan and a setup repeat: the hand points of the job's scan, its outline, its design placement and
    its program settings (None when it has no program yet)."""
    from henna_preview import follow_hand
    s = job["steps"]
    for k, name in (("scan", "Scan"), ("hand", "Hand & region"), ("region", "Hand & region"), ("design", "Design")):
        if not s.get(k):
            raise ValueError(f"do the {name} step once first")
    if need_pass and s["design"].get("verdict") != "PASS":
        raise ValueError("the design has not passed its check on this scan - Check on measured skin first")
    p = v28._parts()
    prog = s.get("program") or {}
    d = prog.get("dispense")
    return {"landmarks": follow_hand.landmarks(_hand_report(job), s["scan"]["mpos"], p["R"], p["t"]),
            "outline_px": s["region"]["polygon_px"],
            "design": {k: s["design"][k] for k in ("svg", "fit_mm", "rotation_deg", "centre_machine_xy", "standoff_mm")},
            "program": ({"draw_feed_mm_min": prog.get("draw_feed_mm_min"), "dispense": d and {k: d.get(k) for k in ("dispenser", "start_s", "stop_s", "label")}}
                        if prog.get("program") else None)}


def _follow_and_prepare(job, src, what, allow_move=False, write_program=None):
    """Scan the hand where it is now and repeat src's outline, design and program on it, through the page's own routes
    (every guard): [move to the scan position], scan, hand, outline + design moved with the hand (follow_hand), the
    design check on the new scan, the program. Stops at the first refusal, naming the step."""
    try:
        return _follow_and_prepare_steps(job, src, what, allow_move, write_program)
    finally:
        PROGRESS.update(op=None, step=None)


# Progress of Quick re-scan / Scan & prepare for the page's bar (user 2026-10-24: "remove these [spinners] and loading
# bars with 0 to 100% for better waiting"): each step's share of the bar and its usual time - the page fills the bar
# within the step by time and never past the step's end until the next step starts.
PROGRESS = {"op": None, "step": None, "from": 0, "to": 0, "expect_s": 0.0, "started": None}
STEP_SHARE = {"the move to the scan position": (0, 15, 10.0), "the scan": (15, 40, 15.0),
              "the hand detection": (40, 55, 10.0), "following the hand": (55, 58, 1.0), "the outline": (58, 65, 3.0),
              "the design check": (65, 85, 25.0), "writing the program": (85, 99, 20.0)}


def progress_view(_=None):
    return dict(PROGRESS, now=time.time())


def _follow_and_prepare_steps(job, src, what, allow_move, write_program):
    from henna_preview import follow_hand
    jid, done, p = job["id"], [], v28._parts()
    design = src["design"]
    timing, t_all = {}, time.monotonic()                     # seconds per step (2026-10-26: "wind up in 2-3 min")

    def step(name, fn):
        a, b, sec = STEP_SHARE.get(name, (PROGRESS.get("to") or 0, PROGRESS.get("to") or 0, 1.0))
        PROGRESS.update({"op": what, "step": name, "from": a, "to": b, "expect_s": sec, "started": time.time()})
        t0 = time.monotonic()
        try:
            return fn()
        except Exception as e:                              # noqa: BLE001 - the step's own message, which step
            raise ValueError(f"{what} stopped at {name}: {e} (done: {', '.join(done) or 'nothing'})") from None
        finally:
            timing[name] = round(timing.get(name, 0.0) + time.monotonic() - t0, 1)

    st = machine.status()
    if not st.get("capture_ready"):
        if not allow_move:
            raise ValueError(f"{what}: the head is not at the scan position - take the hand away, Move to scan "
                             "position, then put the hand in place and try again")
        step("the move to the scan position", lambda: _way_to_scan_position_clear(job, st))
        step("the move to the scan position", lambda: _route("/api/machine/scan_position")(
            {"job": jid, "confirm_clear": True, "confirm_estop": True}))
        step("the move to the scan position", _wait_at_scan_position)
        done.append("moved to the scan position")
    step("the scan", lambda: _route("/api/studio/scan")({"job": jid, "seconds": RESCAN_SECONDS}))
    done.append("scan")
    step("the hand detection", lambda: _route("/api/studio/detect")({"job": jid}))
    done.append("hand")
    job = v11.job_load(jid)
    rep_new = _hand_report(job)
    new = step("following the hand", lambda: follow_hand.landmarks(rep_new, job["steps"]["scan"]["mpos"],
                                                                 p["R"], p["t"]))
    f = step("following the hand", lambda: follow_hand.follow(src["landmarks"], new))
    w, h = int(rep_new["colour_intrinsics"]["width"]), int(rep_new["colour_intrinsics"]["height"])
    poly = np.clip(follow_hand.apply(src["outline_px"], f["pixels"]), [0, 0], [w - 1, h - 1])
    from step05_region import DEFAULT_MARGIN                # the region route always needs a number, even for an
    step("the outline", lambda: _route("/api/studio/region_accept")(   # edited outline (the page sends its slider)
        {"job": jid, "polygon": np.round(poly, 1).tolist(), "margin": DEFAULT_MARGIN, "edited": True}))
    done.append("outline moved with the hand")
    centre = follow_hand.apply([design["centre_machine_xy"]], f["machine"])[0]
    rot = ((float(design["rotation_deg"]) + f["turn_deg"] + 180.0) % 360.0) - 180.0
    r = step("the design check", lambda: _route("/api/studio/check_design")(
        {"job": jid, "svg": design["svg"], "fit_mm": design["fit_mm"], "rotation_deg": round(rot, 2),
         "centre_machine_xy": [round(float(v), 3) for v in centre], "standoff_mm": design["standoff_mm"]}))
    verdict = ((r or {}).get("result") or {}).get("verdict")
    if verdict != "PASS":
        raise ValueError(f"{what} stopped at the design check: the design does not fit on the new scan - move or "
                         f"shrink it in Design (done: {', '.join(done)})")
    done.append("design checked")
    prog = src.get("program")
    if write_program is None:
        write_program = prog is not None
    if write_program:
        prog = prog or {}
        pp = {"job": jid, "confirm_prosthetic": True, "confirm_unmoved": True,
              "draw_feed_mm_min": float(prog.get("draw_feed_mm_min") or v28.REAL_FEED_MM_MIN)}
        d = prog.get("dispense")
        if d:
            pp.update(dispenser=d["dispenser"], start_delay_s=d["start_s"], stop_delay_s=d["stop_s"])
        step("writing the program", lambda: _route("/api/studio/program")(pp))
        done.append("program written")
    job = v11.job_load(jid)
    timing["total"] = round(time.monotonic() - t_all, 1)
    _log(what, (machine.status() or {}).get("mpos"), "scan, hand, outline, design, program",
         "the design follows the hand; all checks on the new scan", f"hand moved {f['shift_mm']} mm, turned "
         f"{f['turn_deg']:+.1f} deg (fit {f['machine']['rms']:.1f} mm); {', '.join(done)}; seconds "
         + ", ".join(f"{k} {v:g}" for k, v in timing.items()), "ready", job["steps"]["scan"]["name"],
         f"Studio v29; job {jid}")
    return {"job": v14.job_view(job), "followed": {"shift_mm": f["shift_mm"], "turn_deg": f["turn_deg"],
                                                   "fit_mm": round(f["machine"]["rms"], 2)}, "done": done,
            "timing_s": timing}


def _ticks(params):
    if params.get("confirm_clear") is not True or params.get("confirm_estop") is not True:
        raise ValueError("Confirm that nothing else is in the travel area and that you are at the emergency stop")


def quick_rescan(params):
    params = params or {}
    job = v11.job_load(params.get("job"))
    if job.get("subject") not in ("prosthetic", v28.REAL):
        raise ValueError("Quick re-scan is for prosthetic and real-hand jobs")
    _ticks(params)
    try:
        src = _placement_of(job)
    except ValueError as e:
        raise ValueError(f"{e} - Quick re-scan repeats the steps you have done") from None
    return _follow_and_prepare(job, src, "Quick re-scan", allow_move=True)


# ---- Scan & start and Re-scan and continue (user 2026-10-26: "whole process is too slow for someone to rest hand,
# thats why hand moves" -> "build 1 and 2"). Scan & start: the safety ticks first, the hand in place, ONE press - Quick
# re-scan's steps and then the same Start as the Start button (every check, the hand check right after the scan), no
# minute of reading and clicking in between. Re-scan and continue: after a run was stopped (the hand moved and the
# watch paused it; STOP; hand away; Home; hand back), the same, with only the strokes not drawn yet - the design's rest
# (henna_preview/continue_run.py) keeps the original size and place, so it follows the hand like the whole design.
CONT_DIR = os.path.join(base.ROOT, "outputs", "continue_designs")
_DESIGNS = v11.designs


def designs_with_rests():
    """v11's designs and the rests of stopped runs - so the design check accepts a rest (the page's gallery keeps
    v11's list: the GET route below)."""
    out = _DESIGNS()
    return {"designs": out["designs"] + [v11.design_entry(p) for p in sorted(glob.glob(os.path.join(CONT_DIR, "*.svg")))]}


v11.designs = designs_with_rests
_RECORD_RUN = v14._record_run


def _record_run(job_id, run_id, error, snap):
    """v14's run record plus where the head was last while the run ran or was held (the link notes it)."""
    _RECORD_RUN(job_id, run_id, error, snap)
    prog = (snap or {}).get("program") or {}
    if prog.get("last_mpos") is None:
        return
    with v14._runs_lock:
        job = v11.job_load(job_id)
        r = next((x for x in job.get("runs", []) if x.get("id") == run_id), None)
        if r is not None:
            r.update(last_mpos=prog["last_mpos"], last_acked=prog.get("last_acked"))
            v11.job_save(job)


v14._record_run = _record_run                               # v20's run start looks it up when the run ends


def _start_now(job_id, what, prep):
    """The Start button's run(), right after prep passed (every check of Start runs again)."""
    try:
        return run({"job": job_id, "confirm_prosthetic": True, "confirm_unmoved": True, "confirm_clear": True,
                    "confirm_estop": True})
    except Exception as e:                                  # noqa: BLE001 - say what was done; Start can follow
        raise ValueError(f"{what}: prepared ({', '.join(prep['done'])}) but the start was refused: {e}") from None


def _wait_homed(timeout_s=150.0):
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        st = machine.status()
        if not st.get("busy"):
            if st.get("homed"):
                return st
            raise MachineError("homing did not finish - check the machine, then Home in the top bar")
        time.sleep(0.25)
    raise MachineError("homing took too long")


def get_ready(params):
    """Connect (if needed), Home (if needed), Move to scan position (if not there) - one press with no hand under the
    machine (user 2026-10-26: "Home with Z first ... this should be auto"). Homing never starts by itself: its way to
    the home corner is not path-checked, so the hand must be away - the same two ticks as Home."""
    params = params or {}
    job = v11.job_load(params.get("job"))
    v13_ticks = {"confirm_clear": params.get("confirm_clear"), "confirm_estop": params.get("confirm_estop")}
    if v13_ticks["confirm_clear"] is not True or v13_ticks["confirm_estop"] is not True:
        raise ValueError("Confirm that no hand or object is under the machine and that you are at the emergency stop")
    done = []
    try:
        st = machine.status()
        if not st.get("connected"):
            PROGRESS.update({"op": "Get ready", "step": "connecting", "from": 0, "to": 10, "expect_s": 3.0,
                             "started": time.time()})
            _route("/api/machine/connect")({})
            done.append("connected")
            st = machine.status()
        if not st.get("homed"):
            PROGRESS.update({"op": "Get ready", "step": "homing", "from": 10, "to": 70, "expect_s": 30.0,
                             "started": time.time()})
            _route("/api/machine/home")(dict(v13_ticks))
            _wait_homed()
            done.append("homed")
            st = machine.status()
        if not st.get("capture_ready"):
            PROGRESS.update({"op": "Get ready", "step": "the move to the scan position", "from": 70, "to": 99,
                             "expect_s": 15.0, "started": time.time()})
            _route("/api/machine/scan_position")(dict(v13_ticks, job=job["id"]))
            _wait_at_scan_position()
            done.append("at the scan position")
    except Exception as e:                                  # noqa: BLE001 - which part, what was done
        raise ValueError(f"Get ready stopped: {e} (done: {', '.join(done) or 'nothing'})") from None
    finally:
        PROGRESS.update(op=None, step=None)
    _log("Get ready", (machine.status() or {}).get("mpos"), "connect / home / scan position as needed",
         "machine homed and at the scan position", ", ".join(done) or "already ready", "ready", "",
         f"Studio v29; job {job['id']}; no hand under the machine (ticked)")
    return dict(v14.machine_status(), done=done)


def _hand_job(params, what):
    params = params or {}
    job = v11.job_load(params.get("job"))
    if job.get("subject") not in ("prosthetic", v28.REAL):
        raise ValueError(f"{what} is for prosthetic and real-hand jobs")
    _ticks(params)
    return job


def scan_and_start(params):
    job = _hand_job(params, "Scan & start")
    try:
        src = _placement_of(job)
    except ValueError as e:
        raise ValueError(f"{e} - Scan & start repeats the steps you have done") from None
    if src["program"] is None:
        raise ValueError("write the program once first (step 4) - Scan & start writes it again on the new scan")
    prep = _follow_and_prepare(job, src, "Scan & start", allow_move=True, write_program=True)
    out = _start_now(job["id"], "Scan & start", prep)
    _log("Scan & start", (machine.status() or {}).get("mpos"), "Quick re-scan, then Start",
         "the run starts right after the checks on the new scan", f"hand moved {prep['followed']['shift_mm']} mm; "
         f"{', '.join(prep['done'])}", "started", v11.job_load(job["id"])["steps"]["scan"]["name"],
         f"Studio v29; job {job['id']}")
    return dict(out, prepared={"followed": prep["followed"], "done": prep["done"], "timing_s": prep.get("timing_s")})


def _program_lines_of_run(r):
    pd = v10.out_dir("program", r["program"])
    path = os.path.join(pd, str(r.get("file") or ""))
    if not (os.path.basename(path) == r.get("file") and base.is_inside(path, base.OUT_ROOT) and os.path.isfile(path)):
        raise cr_error("the stopped run's program file is not there any more")
    with open(path, encoding="ascii") as f:
        lines = _command_lines(f.read())
    if r.get("lines") is not None and len(lines) != int(r["lines"]):
        raise cr_error("the stopped run's program file is not the one that ran")
    return lines


def cr_error(text):
    from henna_preview.continue_run import ContinueError
    return ContinueError(text)


def continue_point(job):
    """Where the job's last run stopped: {run, from (0-based stroke of the job's design), strokes, how, base, total}."""
    from henna_preview import continue_run as cr
    runs = job.get("runs") or []
    pr = job["steps"].get("program") or {}
    if not runs:
        raise cr_error("this job has not run yet")
    r = runs[-1]
    if r.get("result") == "running":
        raise cr_error("a run is going")
    if r.get("result") == "finished":
        raise cr_error("the last run finished - nothing to continue")
    if r.get("program") != pr.get("program"):
        raise cr_error("the program was written again after the last run - Scan & start draws the whole design")
    lines = _program_lines_of_run(r)
    start = r.get("start_mpos") or job["steps"]["scan"]["mpos"]       # a run starts at the scan position
    k, n, how = cr.stopped_stroke(lines, r.get("last_mpos"), r.get("last_acked"), start)
    design = job["steps"]["design"]
    placed = design_strokes(design)
    pos, spans = cr.positions(lines), cr.strokes_of(lines)
    if len(placed) != n or any(np.hypot(*(pos[a][:2] - np.asarray(p[0], float))) > 0.05
                               for (a, _), p in zip(spans, placed)):
        raise cr_error("the program's strokes do not match the design - Scan & start draws the whole design")
    with open(os.path.join(base.ROOT, design["svg"]), encoding="utf-8") as f:
        text = f.read()
    _, before, _ = cr.rest_svg(text, 0, design["svg"])     # strokes an earlier continue already left out
    return {"run": r["id"], "from": k, "strokes": n, "how": how, "base": before, "total": before + n,
            "svg_text": text}


def continue_view(q):
    try:
        job = v11.job_load(q.get("job", [""])[0])
        cp = continue_point(job)
    except ValueError as e:
        return {"available": False, "reason": str(e)}
    if cp["from"] >= cp["strokes"]:
        return {"available": False, "reason": "every stroke was drawn"}
    return {"available": True, "from_stroke": cp["base"] + cp["from"] + 1, "total": cp["total"], "redo": cp["how"] == "redo"}


def rescan_continue(params):
    from henna_preview import continue_run as cr
    what = "Re-scan and continue"
    job = _hand_job(params, what)
    cp = continue_point(job)
    if cp["from"] >= cp["strokes"]:
        raise ValueError("every stroke was drawn - nothing to continue")
    src = _placement_of(job)
    if src["program"] is None:
        raise ValueError("write the program once first (step 4)")
    svg_rel = src["design"]["svg"]
    text, from_total, orig = cr.rest_svg(cp["svg_text"], cp["from"], svg_rel)
    os.makedirs(CONT_DIR, exist_ok=True)
    path = os.path.join(CONT_DIR, f"{job['id']}-rest-from-{from_total + 1}.svg")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    src["design"]["svg"] = os.path.relpath(path, base.ROOT).replace("\\", "/")
    prep = _follow_and_prepare(job, src, what, allow_move=True, write_program=True)
    out = _start_now(job["id"], what, prep)
    redo = cp["how"] == "redo"
    _log(what, (machine.status() or {}).get("mpos"), "Quick re-scan with the strokes not drawn yet, then Start",
         "the rest follows the hand; every check on the new scan", f"run {cp['run']} stopped in stroke "
         f"{cp['base'] + cp['from'] + 1} of {cp['total']} ({'drawn again from its start' if redo else 'not started'}); "
         f"{orig} from stroke {from_total + 1}; hand moved {prep['followed']['shift_mm']} mm", "started",
         v11.job_load(job["id"])["steps"]["scan"]["name"], f"Studio v29; job {job['id']}")
    return dict(out, prepared={"followed": prep["followed"], "done": prep["done"], "timing_s": prep.get("timing_s")},
                continued={"from_stroke": from_total + 1, "total": cp["total"], "redo": redo})


# ---- Setups (user 2026-10-22: "this process takes long time, we need to make it faster" -> "yes" to Repeat job):
# a job that passed is saved as a named setup - the hand points, the outline, the design placement and the program
# settings. A new job from a setup needs only: Move to scan position, the hand in the jig, Scan & prepare (scan, hand,
# outline + design moved onto THIS hand, design check, program - the same steps as Quick re-scan), Start.
SETUPS_DIR = os.path.join(base.ROOT, "outputs", "setups")
_SETUP_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


def _setup_path(sid):
    if not (isinstance(sid, str) and _SETUP_ID.match(sid)):
        raise ValueError("unknown setup")
    return os.path.join(SETUPS_DIR, sid + ".json")


def _load_setup(sid):
    path = _setup_path(sid)
    if not os.path.isfile(path):
        raise ValueError("unknown setup")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def setups_list(_=None):
    out = []
    for path in glob.glob(os.path.join(SETUPS_DIR, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                st = json.load(f)
        except (OSError, ValueError):
            continue
        d = st.get("design") or {}
        out.append({"id": st.get("id"), "name": st.get("name"), "subject": st.get("subject"), "created": st.get("created"),
                    "svg": d.get("svg"), "fit_mm": d.get("fit_mm"), "standoff_mm": d.get("standoff_mm"),
                    "machine": st.get("machine"), "paste": ((st.get("program") or {}).get("dispense") or {}).get("label")})
    return {"setups": sorted(out, key=lambda x: str(x.get("created")), reverse=True)}


def setup_save(params):
    params = params or {}
    job = v11.job_load(params.get("job"))
    if job.get("subject") not in ("prosthetic", v28.REAL):
        raise ValueError("setups are for prosthetic and real-hand jobs")
    name = " ".join(str(params.get("name") or "").split())
    if not 2 <= len(name) <= 60:
        raise ValueError("give the setup a name (2 to 60 characters)")
    src = _placement_of(job, need_pass=True)
    try:                                                    # a continued run's design is a rest: save the whole one
        from henna_preview.continue_run import original_of
        with open(os.path.join(base.ROOT, src["design"]["svg"]), encoding="utf-8") as f:
            src["design"]["svg"] = original_of(f.read(), src["design"]["svg"])
    except OSError:
        pass
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:50] or "setup"
    sid = f"{slug}-{stamp}"
    rec = dict(src, id=sid, name=name, created=dt.datetime.now().isoformat(timespec="seconds"),
               subject=job["subject"], from_job=job["id"],
               machine=job.get("machine") or v28.v26.profiles.active_id())
    os.makedirs(SETUPS_DIR, exist_ok=True)
    with open(_setup_path(sid), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    _log("Setup saved", None, "Save as setup", "outline + design relative to the hand", f"{name} ({sid}) from job "
         f"{job['id']}: {src['design']['svg']} {src['design']['fit_mm']:g} mm", "saved", job["steps"]["scan"]["name"],
         "Studio v29")
    return dict(setups_list(), saved=sid)


def setup_delete(params):
    params = params or {}
    if params.get("confirm") is not True:
        raise ValueError("confirm the deletion")
    path = _setup_path(params.get("id"))
    if os.path.isfile(path):
        os.remove(path)
    return setups_list()


def setup_job(params):
    """A new job for a setup: same subject; the Scan step then offers Scan & prepare."""
    st = _load_setup((params or {}).get("setup"))
    if st.get("machine") and st["machine"] != v28.v26.profiles.active_id():
        raise ValueError(f"this setup was made on the machine '{st['machine']}' - choose that machine first")
    out = _route("/api/studio/job_new")({"subject": st["subject"]})
    job = v11.job_load((out.get("job") or out)["id"])
    job["setup"] = {"id": st["id"], "name": st["name"]}
    v11.job_save(job)
    return {"job": v14.job_view(job)}


def setup_prepare(params):
    """Scan & prepare for a job made from a setup: the head must already be at the scan position (Move to scan
    position before the hand goes in - the way there is not known before this scan)."""
    params = params or {}
    job = v11.job_load(params.get("job"))
    if not job.get("setup"):
        raise ValueError("this job was not made from a setup")
    _ticks(params)
    st = _load_setup(job["setup"]["id"])
    return _follow_and_prepare(job, st, "Scan & prepare", allow_move=False, write_program=True)


def setup_start(params):
    """Scan & start for a job made from a setup (user 2026-10-26: "wind up everything in 2 to 3 minutes" -> "build 1
    and 2"): Scan & prepare, then the Start button's run - choose the setup, Move to scan position, hand in, one press."""
    params = params or {}
    job = v11.job_load(params.get("job"))
    if not job.get("setup"):
        raise ValueError("this job was not made from a setup")
    _ticks(params)
    st = _load_setup(job["setup"]["id"])
    prep = _follow_and_prepare(job, st, "Scan & start", allow_move=False, write_program=True)
    out = _start_now(job["id"], "Scan & start", prep)
    _log("Scan & start (setup)", (machine.status() or {}).get("mpos"), "Scan & prepare, then Start",
         "the run starts right after the checks on the new scan", f"setup {job['setup']['name']}; hand "
         f"{prep['followed']['shift_mm']} mm from the setup's hand; seconds "
         + ", ".join(f"{k} {v:g}" for k, v in (prep.get("timing_s") or {}).items()), "started",
         v11.job_load(job["id"])["steps"]["scan"]["name"], f"Studio v29; job {job['id']}")
    return dict(out, prepared={"followed": prep["followed"], "done": prep["done"], "timing_s": prep.get("timing_s")})


def dispense_test(params):
    seconds = float((params or {}).get("seconds", TEST_S))
    dispenser = (params or {}).get("dispenser")
    output = output_for(dispenser) if dispenser in dispense.DISPENSERS else None
    found = controller_ready(output)
    st = machine.status()

    def done(error):
        _log("Dispenser test", st.get("mpos"), f"M3 S{dispense.S_FULL}; G4 P{seconds:.3f}; M5",
             f"paste {seconds:g} s, machine still", "ok" if error is None else error,
             "done" if error is None else "stopped", "", f"Studio v29; {_where(found)}")
    machine.start_dispense_test(seconds, on_done=done, output=output)
    return v14.machine_status()


# ---------- an existing recording: the status Studio saved with it (user 2026-10-16: "remove this status line") ----
_SCAN_ROUTE = v28.Handler.POST_API["/api/studio/scan"]
_USE_REC_ROUTE = v28.Handler.POST_API["/api/studio/use_recording"]


def _note(path):
    return path + ".machine.json"                         # written by every Studio scan since v24 (machine, job)


def saved_status_line(path):
    """The controller status Studio saved when it recorded this file: in its note (from v29 scans), else in the scan
    step of the job that recorded it (the note names it), else in any job whose scan is this recording. None if
    Studio never saved one - such a recording cannot be placed on the machine."""
    try:
        with open(_note(path), encoding="utf-8") as f:
            note = json.load(f)
    except (OSError, ValueError):
        note = {}
    if note.get("status_line"):
        return note["status_line"]
    ids = [note["job"]] if note.get("job") else []
    ids += [os.path.basename(os.path.dirname(p)) for p in sorted(glob.glob(os.path.join(base.OUT_ROOT, "*-job", "job.json")))]
    for job_id in ids:
        try:
            sc = v11.job_load(job_id)["steps"].get("scan") or {}
        except (OSError, ValueError, KeyError):
            continue
        if sc.get("status_line") and sc.get("recording") and _same(sc["recording"], path):
            return sc["status_line"]
    return None


def scan(params):
    out = _SCAN_ROUTE[0](params)
    sc = out["job"]["steps"]["scan"]
    try:
        with open(_note(sc["recording"]), encoding="utf-8") as f:
            note = json.load(f)
        note["status_line"] = sc["status_line"]
        with open(_note(sc["recording"]), "w", encoding="utf-8") as f:
            json.dump(note, f)
    except (OSError, ValueError, KeyError):
        pass                                              # the job still has it; saved_status_line finds it there
    return out


def use_recording(params):
    params = dict(params or {})
    if not str(params.get("status_line") or "").strip():
        from webui_server_v2 import resolve_recording
        line = saved_status_line(resolve_recording(params.get("path")))
        if not line:
            raise ValueError("Studio has no saved machine position for this recording - record a new scan")
        params["status_line"] = line
    return _USE_REC_ROUTE[0](params)


# ---------- no sideways jog while a real-hand job is open (user 2026-10-18: "yes hide sideways jog for real hand") ----
# The path clearance lets a wrist / forearm beside the nozzle's path be taller than travel Z; Studio's own moves never
# go there, but a sideways jog at travel Z could. Studio remembers the subject of the job last opened or created; while
# it is a real hand (hover run or camera only), X/Y jogs are refused - Z+ and Home stay.
_ACTIVE = {"job": None, "subject": None}
NO_SIDEWAYS = ("realhand", "person")
_GET_JOB = v28.Handler.GET_API["/api/studio/job"]
_JOB_NEW = v28.Handler.POST_API["/api/studio/job_new"]
_JOG = v28.Handler.POST_API["/api/machine/jog"]


def _remember_job(job):
    if isinstance(job, dict) and job.get("id"):
        _ACTIVE.update(job=job["id"], subject=job.get("subject"))
    return job


_HAND = v28.Handler.GET_API["/api/studio/hand"]


def hand_view(q):
    """Hand & region: the suggested outline is the whole hand - palm, fingers, thumb, up to the wrist, inset from the
    edge by the margin slider (user 2026-10-24: "this suggested area is too small ... make it big according to our
    designs"). Falls back to the old palm outline when the hand shape is not trustworthy."""
    from henna_preview import hand_outline
    from henna_preview.region import load_bundle
    out = _HAND(q)
    if not out.get("suggestion"):
        return out
    try:
        b = load_bundle(v11.v3.run_dir_from_id(out["run"]))
        depth = b["depth_raw_aligned"].astype(float) * b["depth_scale_m_per_unit"] * 1000.0
        margin = v11.v3.margin_of({"margin": q.get("margin", [0.15])[0]})
        reach, info = _reach(v11.job_load(q.get("job", [""])[0]), depth, b["colour_intrinsics"]["fx"])
        poly, kind = hand_outline.suggest(depth, b["landmarks_px"], b["colour_intrinsics"]["fx"], margin, reach)
    except Exception:                                       # noqa: BLE001 - keep the palm suggestion, never fail
        return dict(out, suggestion_kind="palm")
    return dict(out, suggestion=poly, suggestion_kind=kind, reach=info,
                inset_mm=round(hand_outline.inset_mm_of(margin), 1) if kind != "palm" else None)


def _reach(job, depth_mm, fx=None):
    """(mask of the skin the nozzle can work on, numbers for the page) for the job's scan pose - (None, None) for a
    camera-only job. The same model as the design check: skin Z = head Z - (corrected depth - k); too low when the
    nozzle at the largest allowed height above it is under the Z floor, too tall above the path check's limit
    (travel Z - max(smallest allowed height, hop) - margin). With fx (the colour lens' focal length, px), the tall
    skin is grown by the path check's corridor margin (8 mm) first: every jump starts or ends at a stroke, and the check
    looks 8 mm round each jump - a stroke ending that close to a tall mound is refused whatever the jump does
    (2026-10-24, job 11:14: strokes on the edge of the heel of the palm)."""
    from henna_preview import hand_outline
    from henna_preview import path_clearance as pc
    from henna_preview import travel_clearance as tc
    if job.get("subject") not in ("prosthetic", v28.REAL):
        return None, None
    c, p = v10.cal(), v28._parts()
    lo, hi = (float(v) for v in c["standoff_mm_allowed"])
    if job["subject"] == v28.REAL:
        lo = max(lo, v28.REAL_MIN_STANDOFF_MM)
        hi = max(hi, lo)
    ok = depth_mm > 0
    d = np.where(ok, depth_mm, 1.0)
    skin = float(job["steps"]["scan"]["mpos"][2]) - (np.asarray(p["depth_fn"](d), float) - p["k"])
    top = float(c["travel_z"]) - (max(lo, float(v27.HOP_CLEARANCE_MM)) + tc.MARGIN_MM)
    reach, low, tall = hand_outline.reach_mask(skin, ok, float(c["firmware_z_floor"]), hi, top)
    if fx and tall.any():
        import cv2
        r = max(1, int(round(pc.MARGIN_MM * float(fx) / float(np.median(depth_mm[ok])))))
        near_tall = cv2.dilate(tall.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1,) * 2))
        reach &= near_tall == 0
    bed = v27._paper_z(c, p["k"])
    info = {"lowest_skin_z": round(float(c["firmware_z_floor"]) - hi, 1), "highest_skin_z": round(top, 1),
            "bed_z": None if bed is None else round(float(bed), 1)}
    return reach, info


# ---- Exit (user 2026-10-24: "also a exit button, so it closes everything running"): only while nothing moves or
# records; then the same shutdown as Ctrl+C in the Studio window (camera off, CNC disconnected, hand watch stopped,
# server closed) and the window closes itself (start_studio_pc.bat ends on EXIT_CODE instead of waiting for a key).
EXIT_CODE = 42
_EXIT = {"requested": False}


def studio_exit(params):
    import _thread
    if (params or {}).get("confirm") is not True:
        raise ValueError("confirm closing Henna Studio")
    st = machine.status()
    if st.get("busy"):
        raise ValueError(f"the machine is busy ({st['busy']}) - wait until it finishes, or press STOP, then Exit")
    if v28.WATCH.view().get("active"):
        raise ValueError("a run is still being watched - wait until it ends, or press STOP, then Exit")
    _EXIT["requested"] = True
    _log("Studio exit", st.get("mpos"), "Exit button", "nothing moving or recording",
         "camera off, CNC disconnected, Studio closed", "closed", "", "Studio v29")

    def later():
        time.sleep(0.5)                                     # let the page get its answer first
        _thread.interrupt_main()                            # -> main()'s KeyboardInterrupt shutdown
    threading.Thread(target=later, daemon=True).start()
    return {"closing": True}


def get_job(q):
    return _remember_job(_GET_JOB(q))


def job_new(params):
    out = _JOB_NEW[0](params)
    _remember_job(out.get("job") if isinstance(out, dict) and "job" in out else out)
    return out


def jog(params):
    p = params or {}
    if p.get("axis") in ("X", "Y") and _ACTIVE["subject"] in NO_SIDEWAYS:
        raise ValueError("Sideways jog is off while a real-hand job is open - at travel height the nozzle could reach "
                         "the arm. Use Z+ or Home, or open a prosthetic job.")
    return _JOG[0](params)


def dispensers(_=None):
    return {"dispensers": dispense.DISPENSERS, "delay_range_s": [dispense.DELAY_MIN_S, dispense.DELAY_MAX_S],
            "test_s": TEST_S, "s_full": dispense.S_FULL}


class Handler(v28.Handler):
    server_version = "HennaStudio/0.29"
    GET_API = dict(v28.Handler.GET_API, **{"/api/studio/dispensers": dispensers, "/api/studio/job": get_job,
                                           "/api/studio/active_job": lambda q: dict(_ACTIVE),
                                           "/api/studio/setups": setups_list,
                                           "/api/studio/hand": hand_view,
                                           "/api/studio/follow": follow_view,
                                           "/api/studio/progress": progress_view,
                                           "/api/studio/continue": continue_view,
                                           "/api/studio/designs": lambda q: _DESIGNS()})
    POST_API = dict(v28.Handler.POST_API, **{
        "/api/studio/check_design": (check_design, _CHECK_DESIGN[1]),
        "/api/studio/program": (program, v28.Handler.POST_API["/api/studio/program"][1]),
        "/api/machine/run": (run, v28.Handler.POST_API["/api/machine/run"][1]),
        "/api/machine/resume": (resume, _RESUME[1]),
        "/api/studio/quick_rescan": (quick_rescan, True),
        "/api/studio/scan_and_start": (scan_and_start, True),
        "/api/studio/get_ready": (get_ready, True),
        "/api/studio/rescan_continue": (rescan_continue, True),
        "/api/studio/setup_save": (setup_save, True),
        "/api/studio/exit": (studio_exit, False),
        "/api/studio/setup_delete": (setup_delete, False),
        "/api/studio/setup_job": (setup_job, False),
        "/api/studio/setup_prepare": (setup_prepare, True),
        "/api/studio/setup_start": (setup_start, True),
        "/api/machine/dispense_test": (dispense_test, False),
        "/api/studio/job_new": (job_new, _JOB_NEW[1]),
        "/api/machine/jog": (jog, _JOG[1]),
        "/api/studio/scan": (scan, _SCAN_ROUTE[1]),
        "/api/studio/use_recording": (use_recording, _USE_REC_ROUTE[1]),
    })

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        if path.startswith("/download_program/") and self._paste_download(unquote(path[len("/download_program/"):])):
            return
        return super().do_GET()

    def _paste_download(self, rest):
        """A copy of a paste program (the v10 route only knows hover program names)."""
        parts = rest.split("/")
        if not (len(parts) == 2 and v10.NAME.match(parts[0]) and PASTE_NAME.match(parts[1])):
            return False
        if not self._host_ok():
            return True                                      # _host_ok answered already
        p = os.path.join(base.OUT_ROOT, *parts)
        if not (base.is_inside(p, base.OUT_ROOT) and os.path.isfile(p)):
            return False
        with open(p, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=ascii")
        self.send_header("Content-Disposition", f'attachment; filename="{parts[1]}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        return True


def main():
    v27.Handler = Handler                                   # v27.main serves v27.Handler (-> v26.main)
    install_close_heights()
    install_path_clearance()
    install_free_jog()
    print("Henna Studio v29 - paste dispensing (pneumatic / servo SSR on the controller output)", flush=True)
    try:
        v27.main()
    finally:
        v28.WATCH.stop()


if __name__ == "__main__":
    main()
    if _EXIT["requested"]:
        sys.exit(EXIT_CODE)
