"""Studio v17: low hops between strokes ("Z like a laser", user decision 2026-10-10: 5 mm above the skin).

Programs are written by step 23 (step 22 + low hops): between strokes the nozzle rises only to the highest
measured skin under the crossing + 5 mm, crosses there and drops onto the next stroke (drops <= 300 mm/min as
in v16). Crossings over unmeasured skin keep travel Z -20. Step 23 re-reads its own text and re-samples the skin
under every hop; Studio re-reads the text again (program_reread_v2) when writing and at every Start.
Everything else is v16 (drawing speed per program) / v15 (fresh position, verified Pause) / v14.
"""
import datetime as dt
import glob
import hashlib
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v16 as v16
from henna_preview.grbl_program_link import MachineError
from henna_preview.hop_path import HOP_CLEARANCE_MM
from henna_preview.program_reread_v2 import ProgramReadError, program_lines_v2, reread_v2

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v17.html")
STEP23 = os.path.join(base.ROOT, "step23_design_hop_program.py")
_G0Z = re.compile(r"^G53 G0 Z(-?\d+\.\d{3})$")
_G0XY = re.compile(r"^G53 G0 X\d+\.\d{3} Y\d+\.\d{3}$")
_G1 = re.compile(r"^(G53 G1 X\d+\.\d{3} Y\d+\.\d{3} Z(-?\d+\.\d{3})) F(\d+)$")


def run_step23(config_path):
    proc = subprocess.run([sys.executable, STEP23, "--config", config_path, "--write-gcode"], cwd=base.ROOT,
                          capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        err = proc.stderr
        k = max(err.rfind("Error:"), err.rfind("Error\n"))
        start = err.rfind("\n", 0, k) + 1 if k > 0 else max(0, len(err) - 600)
        raise ValueError("step 23 refused: " + err[start:].strip()[:1500])
    m = re.search(r"^Wrote: (.+)$", proc.stdout, re.M)
    if not m:
        raise RuntimeError("step 23 did not report its output folder")
    return m.group(1).strip()


def slow_drops(text, feed):
    """Cap each drop (first G1 after a sideways rapid) at DESCENT_MAX; extra seconds vs the drawing speed."""
    out, after_rapid, z, extra, travel = [], False, None, 0.0, None
    cap = min(feed, v16.DESCENT_MAX)
    for line in text.splitlines():
        m0 = _G0Z.match(line)
        if m0:
            z = float(m0.group(1))
            travel = z if travel is None else travel           # the program's first rise is to travel Z
        m = _G1.match(line)
        if after_rapid and _G0XY.match(line) and z is not None and z == travel:
            out.append(line)                                   # a route round tall skin, at travel Z (v29)
            continue
        if after_rapid:
            if not m:
                raise ValueError("program structure: a sideways rapid move is not followed by a drop")
            drop = z - float(m.group(2))
            extra += drop / cap * 60 - drop / feed * 60
            line = f"{m.group(1)} F{cap}"
        after_rapid = bool(_G0XY.match(line))
        out.append(line)
    return "\n".join(out) + "\n", extra


def program(params):
    job = v11.job_load(params.get("job"))
    v11.prosthetic_only(job)
    v11.need(job, "plan")
    feed = v16.draw_feed(params)
    if params.get("confirm_prosthetic") is not True or params.get("confirm_unmoved") is not True:
        raise ValueError("tick both boxes: prosthetic hand only, and the hand has not moved since the scan")
    plan_dir = v10.out_dir("plan", job["steps"]["plan"]["plan"])
    pl = v10.jload(os.path.join(plan_dir, "plan.json"))
    config = dict(pl["config"])
    if float(config["standoff_mm"]) < v14.MIN_STANDOFF_MM:
        raise ValueError(f"this design was checked at {config['standoff_mm']:g} mm - go back to Design, choose "
                         f"{v14.MIN_STANDOFF_MM:g} mm or more and check it again")
    config["draw_feed_mm_min"] = float(feed)
    config["hop_clearance_mm"] = HOP_CLEARANCE_MM
    config["decided_by"] = (str(config.get("decided_by", "")) + f"; drawing speed {feed} mm/min and low hops "
                            f"{HOP_CLEARANCE_MM:g} mm chosen in Studio v17")
    c = v10.cal()
    pd = v10.new_dir("program")
    v10.jsave(os.path.join(pd, "config.json"), config)
    out = run_step23(os.path.join(pd, "config.json"))
    gfiles = glob.glob(os.path.join(out, "DESIGN_HOVER_TEST_PROSTHETIC_*.gcode"))
    if len(gfiles) != 1:
        raise RuntimeError("program file not found")
    gname = os.path.basename(gfiles[0])
    with open(gfiles[0], encoding="ascii") as f:
        written = f.read()
    text, extra_s = slow_drops(written, feed)
    lines = text.splitlines()
    first_code = next(i for i, ln in enumerate(lines) if not ln.startswith("("))
    lines.insert(first_code, f"(drawing speed {feed} mm/min - drop to the hand at {min(feed, v16.DESCENT_MAX)} "
                             "mm/min or slower)")
    text = "\n".join(lines) + "\n"
    rep = v10.jload(os.path.join(out, "report.json"))
    rr = reread_v2(text, c["travel_z"], rep["global_floor_in_program"], feed)
    v16.check_feeds(text, feed)
    if rr["low_hops"] != rep["hops"]["low"]:
        raise ValueError("program text and step 23 disagree about the low hops")
    gfile = os.path.join(pd, gname)
    with open(gfile, "w", encoding="ascii", newline="\n") as f:
        f.write(text)
    shutil.copyfile(os.path.join(out, "design_hover_preview.png"), os.path.join(pd, "preview.png"))
    shutil.copyfile(os.path.join(out, "report.json"), os.path.join(pd, "step22_report.json"))  # same floor field
    est = round(rep["estimated_run_time_s"] + extra_s, 1)
    hops = {"clearance_mm": rep["hops"]["clearance_mm"], "low": rep["hops"]["low"], "total": rep["hops"]["total"],
            "z_range": rep["hops"]["z_range"]}
    summary = {"schema": "web_program/3", "created": dt.datetime.now().isoformat(timespec="seconds"),
               "plan": os.path.basename(plan_dir), "program_file": gfile, "use_offset": False,
               "standoff_mm": config["standoff_mm"], "draw_feed_mm_min": feed,
               "descent_feed_mm_min": min(feed, v16.DESCENT_MAX), "hops": hops, "reread": rr,
               "nozzle_z_range": rep["nozzle_z_range"], "estimated_run_time_s": est, "hand_top": rep["hand_top"],
               "recording": pl["recording"]}
    v10.jsave(os.path.join(pd, "program.json"), summary)
    with open(gfile, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    v10.log_row(v10.LOG_FILE, 11, "Studio v17 hover program", None, "Studio Write program", "all checks pass",
                f"{gname}: {rr['lines']} lines, {rr['strokes']} strokes; standoff {config['standoff_mm']:g}; drawing "
                f"F{feed}, drops F{min(feed, v16.DESCENT_MAX)}; low hops {hops['low']}/{hops['total']} at Z "
                f"{hops['z_range']}; re-read {rr['problems']} problems", "generated - not yet run", pl["recording"],
                "prosthetic only")
    name = os.path.basename(pd)
    v11.set_step(job, "program", {
        "program": name, "program_file": gfile, "download": f"/download_program/{name}/{gname}",
        "preview": f"/outputs/{name}/preview.png", "reread": rr, "use_offset": False, "offset_mm": 0.0,
        "standoff_mm": config["standoff_mm"], "sha256": sha, "studio": "v17", "draw_feed_mm_min": feed,
        "descent_feed_mm_min": min(feed, v16.DESCENT_MAX), "hops": hops,
        "nozzle_z_range": rep["nozzle_z_range"], "estimated_run_time_s": est})
    return {"job": v14.job_view(job)}


def checked_program(job):
    """v17 programs: re-read with hops; older programs: v16's checks unchanged."""
    pr = job["steps"]["program"]
    if pr.get("studio") != "v17":
        return v16.checked_program(job)
    feed = v16.draw_feed({"draw_feed_mm_min": pr.get("draw_feed_mm_min")})
    if not pr.get("sha256"):
        raise ValueError("program has no fingerprint - write it again in step 4")
    pd = v10.out_dir("program", pr["program"])
    summary = v10.jload(os.path.join(pd, "program.json"))
    if summary.get("use_offset") or float(summary["standoff_mm"]) < v14.MIN_STANDOFF_MM:
        raise ValueError(f"only programs at {v14.MIN_STANDOFF_MM:g} mm or more without an offset can be run")
    path = summary["program_file"]
    same = lambda a, b: os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))  # noqa: E731
    if not (same(os.path.dirname(path), pd) and same(path, pr["program_file"])):
        raise ValueError("program file is not the one written for this job")
    with open(path, "rb") as f:
        raw = f.read()
    if hashlib.sha256(raw).hexdigest() != pr["sha256"]:
        raise ValueError("the program file changed after it was written - write it again in step 4")
    text = raw.decode("ascii")
    if not text.startswith(v14.HEADER):
        raise ValueError("not a prosthetic hover program")
    c = v10.cal()
    floor = float(v10.jload(os.path.join(pd, "step22_report.json"))["global_floor_in_program"])
    rr = reread_v2(text, c["travel_z"], floor, feed)
    v16.check_feeds(text, feed)
    if rr["low_hops"] != summary["hops"]["low"]:
        raise ValueError("the program's low hops do not match what was checked when it was written")
    lines, end = program_lines_v2(text, c["travel_z"], floor, feed, c["firmware_z_floor"])
    return os.path.basename(path), lines, end, rr, feed


def run_start(params):
    job = v11.job_load(params.get("job"))
    v11.prosthetic_only(job)
    v11.need(job, "program")
    v14.confirmed_run(params)
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before running a program")
    name, lines, end, rr, feed = checked_program(job)
    link = v16.machine()
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    with v14._runs_lock:
        status = link.start_program(
            name, lines, end, on_done=lambda error: v14._record_run(job["id"], run_id, error, link.status()))
        job.setdefault("runs", []).append({
            "id": run_id, "program": job["steps"]["program"]["program"], "file": name,
            "sha256": job["steps"]["program"]["sha256"], "lines": len(lines), "end_mpos": list(end),
            "draw_feed_mm_min": feed, "low_hops": rr.get("low_hops", 0), "recording": job["steps"]["scan"]["name"],
            "started": dt.datetime.now().isoformat(timespec="seconds"), "result": "running"})
        v11.job_save(job)
    v10.log_row(v10.LOG_FILE, 12, "Studio run start", status.get("mpos"), name,
                f"{len(lines)} lines; ends at X{end[0]:.3f} Y{end[1]:.3f} Z{end[2]:.3f}",
                f"re-read {rr['problems']} problems; {rr['strokes']} strokes; Z {rr['z_min']} to {rr['z_max']}; "
                f"drawing F{feed}; low hops {rr.get('low_hops', 0)}",
                "started", job["steps"]["scan"]["name"], "prosthetic only; 4 mm; no dispensing; four confirmations ticked")
    return dict(v14.machine_status(), job=v14.job_view(job))


class Handler(v16.Handler):
    server_version = "HennaStudio/0.17"
    POST_API = dict(v16.Handler.POST_API, **{
        "/api/studio/program": (program, True),
        "/api/machine/run": (run_start, True),
    })
    ERRORS = v16.Handler.ERRORS + (ProgramReadError,)

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    link = v16.machine()
    print("Henna Studio v17 - low hops between strokes. motion_ready=false", flush=True)
    print(f"Open http://{base.HOST}:{base.PORT}; press Ctrl+C to stop (a running program is stopped)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if link.status().get("busy") in v14.MOVING:
                link.stop()
        finally:
            try:
                srv.server_close()
            finally:
                try:
                    if link.status().get("busy") in v14.MOVING:
                        link.stop()
                finally:
                    try:
                        link.disconnect()
                    finally:
                        v10.camera.stop()


if __name__ == "__main__":
    main()
