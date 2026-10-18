"""Studio v14: Studio runs the checked prosthetic hover program itself - no UGS steps.

User decisions on 2026-10-10: "Studio runs it" and "Remove the paper checks, always 4 mm".
- Steps: 1 Scan, 2 Hand & region, 3 Design, 4 Program, 5 Run. No surface check, no paper gap check.
- Prosthetic programs are written at 4 mm or more above measured skin and never with an offset.
- Run: Start / Pause / Resume / Stop in the page. Only the job's own program, unchanged since it was
  written (SHA-256), re-read again by the independent checker and sent by the narrow program link.
- Jog (approved 2026-10-10): 0.1 / 1 / 10 mm steps after Home, always at or above travel Z -20.
- Real-hand jobs: camera preview only - never a program or a run. motion_ready stays false.
New revision; v13 and all calibrated processing scripts are unchanged.
"""
import datetime as dt
import glob
import hashlib
import os
import threading
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v13 as v13
from henna_preview.grbl_program_link import GrblProgramLink, MachineError, program_lines
from henna_preview.program_reread import reread

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v14.html")
MIN_STANDOFF_MM = 4.0
HEADER = "(DESIGN HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING)"
_runs_lock = threading.Lock()

machine = GrblProgramLink(calibration_provider=v10.cal, on_event=v13.log_event)
# One serial link per process: v13's connect/home/scan-position/recording functions use this link too.
v13.machine = machine


def machine_status(_=None):
    return dict(machine.status(), default_port=v13.v12.default_port())


def standoff_range():
    """v14 nozzle-height range: the approved range, raised to at least 4 mm (the calibration file is unchanged)."""
    lo, hi = v10.cal()["standoff_mm_allowed"]
    return [max(MIN_STANDOFF_MM, lo), hi]


def v14_rules(_=None):
    return {"min_standoff_mm": MIN_STANDOFF_MM, "standoff_mm_allowed": standoff_range()}


# ---------------- jobs ----------------
def status_of(job):
    s = job["steps"]
    if job["subject"] == "person":
        return v11.status_of(job)
    for k, label in (("scan", "scan"), ("hand", "hand"), ("region", "region"), ("plan", "design"), ("program", "program")):
        if k not in s:
            return label
    prog = s["program"]["program"]
    return "done" if any(r.get("program") == prog and r.get("result") == "finished" for r in job.get("runs", [])) else "run"


def job_view(job):
    return dict(job, status=status_of(job))


def jobs_list():
    out = []
    for p in glob.glob(os.path.join(base.OUT_ROOT, "*-job", "job.json")):
        try:
            j = v10.jload(p)
        except (OSError, ValueError):
            continue
        s = j.get("steps", {})
        runs = j.get("runs", [])
        out.append({"id": j["id"], "created": j["created"], "updated": j.get("updated", j["created"]),
                    "subject": j["subject"], "status": status_of(j), "recording": s.get("scan", {}).get("name"),
                    "design": os.path.basename(s.get("design", {}).get("svg", "")) or None,
                    "verdict": "PASS" if status_of(j) == "done" else (runs[-1]["result"] if runs else None)})
    return sorted(out, key=lambda j: j["updated"], reverse=True)[:40]


def wrap(fn):
    """v11 step functions return {"job": job, ...}; show the v14 status."""
    def call(params):
        r = fn(params)
        if isinstance(r, dict) and isinstance(r.get("job"), dict):
            r = dict(r, job=job_view(r["job"]))
        return r
    return call


def not_used(_):
    raise ValueError("Studio v14 has no paper-feeler steps: prosthetic programs use 4 mm or more, without an offset")


# ---------------- 3 design ----------------
def check_design(params):
    job = v11.job_load(params.get("job"))
    standoff = v10.number(params, "standoff_mm", *standoff_range())
    if job["subject"] == "prosthetic" and standoff < MIN_STANDOFF_MM:
        raise ValueError(f"nozzle height must be at least {MIN_STANDOFF_MM:g} mm")
    return v11.check_design(dict(params, standoff_mm=standoff))


# ---------------- 4 program ----------------
def _plan_standoff(job):
    pl = v10.jload(os.path.join(v10.out_dir("plan", job["steps"]["plan"]["plan"]), "plan.json"))
    return float(pl["config"]["standoff_mm"])


def program(params):
    job = v11.job_load(params.get("job"))
    v11.prosthetic_only(job)
    v11.need(job, "plan")
    standoff = _plan_standoff(job)
    if standoff < MIN_STANDOFF_MM:
        raise ValueError(f"this design was checked at {standoff:g} mm - go back to Design, choose "
                         f"{MIN_STANDOFF_MM:g} mm or more and check it again")
    r = v10.program({"plan": job["steps"]["plan"]["plan"], "use_offset": False,
                     "confirm_prosthetic": params.get("confirm_prosthetic") is True,
                     "confirm_unmoved": params.get("confirm_unmoved") is True})
    with open(r["program_file"], "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    v11.set_step(job, "program", {"program": r["program"], "program_file": r["program_file"], "download": r["download"],
                                  "preview": r["preview"], "reread": r["reread"], "use_offset": False, "offset_mm": 0.0,
                                  "standoff_mm": r["standoff_mm"], "sha256": sha, "studio": "v14",
                                  "nozzle_z_range": r["nozzle_z_range"], "estimated_run_time_s": r["estimated_run_time_s"]})
    return {"job": job_view(job)}


# ---------------- 5 run ----------------
def checked_program(job):
    """The job's own program, unchanged since it was written, re-read and turned into the lines to send."""
    pr = job["steps"]["program"]
    if pr.get("studio") != "v14" or not pr.get("sha256"):
        raise ValueError("this program was written for UGS by an older Studio - write it again in step 4")
    pd = v10.out_dir("program", pr["program"])
    summary = v10.jload(os.path.join(pd, "program.json"))
    if summary.get("use_offset") or float(summary["standoff_mm"]) < MIN_STANDOFF_MM:
        raise ValueError(f"only programs at {MIN_STANDOFF_MM:g} mm or more without an offset can be run")
    path = summary["program_file"]
    same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))  # noqa: E731
    if not (same(os.path.dirname(path), pd) and same(path, pr["program_file"])):
        raise ValueError("program file is not the one written for this job")
    with open(path, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != pr["sha256"]:
        raise ValueError("the program file changed after it was written - write it again in step 4")
    text = raw.decode("ascii")
    if not text.startswith(HEADER):
        raise ValueError("not a prosthetic hover program")
    c = v10.cal()
    floor = float(v10.jload(os.path.join(pd, "step22_report.json"))["global_floor_in_program"])
    rr = reread(text, c["travel_z"], floor, c["draw_feed_mm_min"])
    lines, end = program_lines(text, c["travel_z"], floor, c["draw_feed_mm_min"], c["firmware_z_floor"])
    return os.path.basename(path), lines, end, rr


def confirmed_run(params):
    keys = ("confirm_prosthetic", "confirm_unmoved", "confirm_clear", "confirm_estop")
    if not all(params.get(k) is True for k in keys):
        raise ValueError("Tick all four confirmations: prosthetic hand only, hand not moved since the scan, "
                         "nothing else in the travel area, and you are at the emergency stop")


def _record_run(job_id, run_id, error, link_status):
    with _runs_lock:
        job = v11.job_load(job_id)
        run = next((r for r in job.setdefault("runs", []) if r["id"] == run_id), None)
        if run is None:
            run = {"id": run_id}
            job["runs"].append(run)
        prog = (link_status or {}).get("program") or {}
        run.update({"ended": dt.datetime.now().isoformat(timespec="seconds"),
                    "result": prog.get("state") or ("finished" if error is None else "failed"),
                    "error": error, "lines_done": prog.get("done"), "elapsed_s": prog.get("elapsed_s")})
        v11.job_save(job)
    v10.log_row(v10.LOG_FILE, 12, "Studio run result", link_status.get("mpos") if link_status else None,
                run.get("program", ""), "whole program, ends at its end position",
                f"{run['result']}; {run.get('lines_done')}/{run.get('lines')} lines; {run.get('elapsed_s')} s"
                + (f"; {error}" if error else ""), run["result"].upper(), run.get("recording", ""),
                "prosthetic only; 4 mm; no dispensing")


def run_start(params):
    job = v11.job_load(params.get("job"))
    v11.prosthetic_only(job)
    v11.need(job, "program")
    confirmed_run(params)
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before running a program")
    name, lines, end, rr = checked_program(job)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    with _runs_lock:
        status = machine.start_program(
            name, lines, end,
            on_done=lambda error: _record_run(job["id"], run_id, error, machine.status()))
        job.setdefault("runs", []).append({
            "id": run_id, "program": job["steps"]["program"]["program"], "file": name,
            "sha256": job["steps"]["program"]["sha256"], "lines": len(lines), "end_mpos": list(end),
            "recording": job["steps"]["scan"]["name"], "started": dt.datetime.now().isoformat(timespec="seconds"),
            "result": "running"})
        v11.job_save(job)
    v10.log_row(v10.LOG_FILE, 12, "Studio run start", status.get("mpos"), name,
                f"{len(lines)} lines; ends at X{end[0]:.3f} Y{end[1]:.3f} Z{end[2]:.3f}",
                f"re-read {rr['problems']} problems; {rr['strokes']} strokes; Z {rr['z_min']} to {rr['z_max']}",
                "started", job["steps"]["scan"]["name"], "prosthetic only; 4 mm; no dispensing; four confirmations ticked")
    return dict(machine_status(), job=job_view(job))


JOG_STEP_TEXT = "step 0.1, 1 or 10 mm"


def jog_step_ok(step):
    """The steps this route takes (Studio v29 replaces it with its 0.1 to 100 mm slider range at start)."""
    return float(step) in (0.1, 1.0, 10.0)


def jog(params):
    """Axis, direction and step only - never coordinates. The link keeps every jog at or above travel Z."""
    if params.get("confirm_no_person") is not True:
        raise ValueError("Confirm that no person's hand is under the machine before jogging")
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before jogging")
    axis, direction, step = params.get("axis"), params.get("direction"), params.get("step_mm")
    if axis not in ("X", "Y", "Z") or direction not in (1, -1) or type(direction) is not int \
            or not isinstance(step, (int, float)) or isinstance(step, bool) or not jog_step_ok(step):
        raise ValueError(f"jog: axis X/Y/Z, direction 1 or -1, {JOG_STEP_TEXT}")
    return machine.start_jog(axis, direction, float(step))


class Handler(v13.Handler):
    server_version = "HennaStudio/0.14"
    GET_API = dict(v13.Handler.GET_API, **{
        "/api/machine/status": machine_status,
        "/api/studio/v14_rules": v14_rules,
        "/api/studio/jobs": lambda q: {"jobs": jobs_list()},
        "/api/studio/job": lambda q: job_view(v11.job_load(q.get("id", [""])[0])),
    })
    POST_API = dict(v13.Handler.POST_API, **{
        "/api/studio/job_new": (lambda p: job_view(v11.job_new(p)), False),
        "/api/studio/scan": (wrap(v13.scan), True),
        "/api/studio/use_recording": (wrap(v11.use_recording), True),
        "/api/studio/detect": (wrap(v11.detect), True),
        "/api/studio/region_accept": (wrap(v11.region_accept), True),
        "/api/studio/check_design": (wrap(check_design), True),
        "/api/studio/program": (program, True),
        "/api/studio/offset": (not_used, False),
        "/api/studio/skip_offset": (not_used, False),
        "/api/studio/gapcheck": (not_used, False),
        "/api/machine/run": (run_start, True),
        "/api/machine/pause": (lambda p: machine.pause(), False),
        "/api/machine/resume": (lambda p: machine.resume(), False),
        "/api/machine/jog": (jog, False),
    })

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path.startswith("/api/studio/") and machine.status().get("busy") == "program":
            if self._host_ok():
                self._send(409, {"error": "A program is running - wait for it, or Pause / STOP it first"})
            return
        return super().do_POST()


MOVING = ("homing", "scan_position", "program", "jog")


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    print("Henna Studio v14 - Studio runs checked prosthetic hover programs. motion_ready=false", flush=True)
    print(f"Open http://{base.HOST}:{base.PORT}; press Ctrl+C to stop (a running program is stopped)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if machine.status().get("busy") in MOVING:
                machine.stop()
        finally:
            try:
                srv.server_close()
            finally:
                try:
                    if machine.status().get("busy") in MOVING:
                        machine.stop()
                finally:
                    try:
                        machine.disconnect()
                    finally:
                        v10.camera.stop()


if __name__ == "__main__":
    main()
