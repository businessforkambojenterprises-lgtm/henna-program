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


def path_measure(job):
    """The nozzle-path corridor of a job with a scan and a region: the measured surface (sub-sampled pixels) and
    pc.corridor_top's result. Raises tc.ClearanceError when the path cannot be measured. Also used by
    path_clearance_picture.py, so the picture shows exactly what the design check measured."""
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
    try:
        r = pc.corridor_top(xy, z, xyb, region_ok, head[:2], blind_mask=blind_mask, mm_per_px=mm_per_px, ok_mask=ok)
    except pc.PathClearanceError as e:
        raise tc.ClearanceError(f"Nozzle path check: {e}") from None
    return {"r": r, "frame": fr, "stride": stride, "ok": ok, "xy": xy, "z": z, "region_ok": region_ok,
            "head": head, "parts": p, "recording": rec}


def path_clearance(job, standoff_mm):
    """v27.clearance with the nozzle's path instead of the whole hand. None before the region step (as v27)."""
    from henna_preview import travel_clearance as tc
    s = job["steps"]
    if not s.get("region") or not s.get("scan"):
        return _WHOLE_HAND_CLEARANCE(job, standoff_mm)
    m = path_measure(job)
    r, rec, p = m["r"], m["recording"], m["parts"]
    c = v10.cal()
    bed = v27._paper_z(c, p["k"])
    os.environ["HENNA_PATH_TOP"] = json.dumps({"recording": os.path.basename(rec), "top_z": r["top_z"],
                                               "basis": r["basis"]})
    try:
        out = tc.check(r["top_z"], c["travel_z"], standoff_mm, v27.HOP_CLEARANCE_MM, bed_z=bed)
    except tc.ClearanceError as e:
        msg = str(e)
        if "too tall" in msg:
            gap = max(float(standoff_mm), float(v27.HOP_CLEARANCE_MM)) + tc.MARGIN_MM
            above = f" (about {r['top_z'] - bed:.1f} mm above the bed)" if bed is not None else ""
            msg = (f"Under the nozzle's path (the design area and the way to it from the scan position, "
                   f"{r['margin_mm']:g} mm margin) the highest point is at Z {r['top_z']:.1f}{above}"
                   + (f", near machine X{r['top_xy'][0]:.0f} Y{r['top_xy'][1]:.0f}" if r["top_xy"] else
                      " (the edge of a patch without depth)")
                   + f"; travel height Z {float(c['travel_z']):g} must be at "
                   f"least {gap:g} mm above it. Choose a design area on a lower part of the hand, or move the hand so "
                   "its tall part (wrist, forearm, thumb) is away from that path.")
        raise tc.ClearanceError(msg) from None
    return dict(out, basis=r["basis"], path=r)


_WHOLE_HAND_CLEARANCE = v27.clearance


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
def checked_program(job):
    """v17's checks of the hover file; for a paste program also its own file, fingerprint and the paste re-read.
    Installed as v17.checked_program, which every run start calls (v20.run_start)."""
    name, lines, end, rr, feed = HOVER_CHECKED(job)
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


def run(params):
    job = v11.job_load(params.get("job"))
    d = (job["steps"].get("program") or {}).get("dispense")
    if d:
        cfg = dict(dispense.settings(d["dispenser"], d["start_s"], d["stop_s"]), output=output_for(d["dispenser"]))
        found = controller_ready(cfg["output"])
        machine.arm_dispense(cfg)
    try:
        out = _prosthetic_start(job, params) if job.get("subject") == "prosthetic" else v28.run(params)
    finally:
        machine.arm_dispense(None)                           # start_program took it, or the run was refused
    if d:
        _log("Paste run start", (machine.status() or {}).get("mpos"), "$$ then the paste program",
             "paste output ready", f"{_where(found)}; {d['label']}, "
             f"{d['strokes']} strokes", "started", job["steps"]["scan"]["name"], f"Studio v29; job {job['id']}")
    return out


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
                                           "/api/studio/active_job": lambda q: dict(_ACTIVE)})
    POST_API = dict(v28.Handler.POST_API, **{
        "/api/studio/program": (program, v28.Handler.POST_API["/api/studio/program"][1]),
        "/api/machine/run": (run, v28.Handler.POST_API["/api/machine/run"][1]),
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
