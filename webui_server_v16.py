"""Studio v16: drawing speed chosen per program (user decision 2026-10-10).

- Step 4 Program: drawing speed 100 / 150 / 200 / 250 / 300 (default, approved) / 400 / 500 / 600 mm/min.
- The drop from travel Z down to the first point of each stroke stays at 300 mm/min or slower, whatever the
  drawing speed (the nozzle approaches the hand there). Rapids and the calibration file are unchanged.
- The program is written by step 22 with the chosen speed, then re-read by the independent checker with that
  speed as the limit, plus a check that every drop is at most 300. Runs check all of this again.
- Everything else is v15 (fresh position during runs, verified Pause) and v14 (4 mm, prosthetic only, jog).
"""
import datetime as dt
import glob
import hashlib
import os
import re
import shutil
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v15 as v15
from henna_preview.grbl_program_link import MachineError, program_lines
from henna_preview.program_reread import reread

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v16.html")
FEEDS = (100, 150, 200, 250, 300, 400, 500, 600)
DEFAULT_FEED = 300
DESCENT_MAX = 300
_G0XY = re.compile(r"^G53 G0 X\d+\.\d{3} Y\d+\.\d{3}$")
_G1 = re.compile(r"^(G53 G1 X\d+\.\d{3} Y\d+\.\d{3} Z(-?\d+\.\d{3})) F(\d+)$")


def machine():
    return v15.machine


def v16_rules(_=None):
    return {"feeds_mm_min": list(FEEDS), "default_feed_mm_min": DEFAULT_FEED, "descent_max_mm_min": DESCENT_MAX,
            "approved_feed_mm_min": v10.cal()["draw_feed_mm_min"]}


def draw_feed(params):
    f = params.get("draw_feed_mm_min", DEFAULT_FEED)
    if isinstance(f, bool) or not isinstance(f, (int, float)) or f not in FEEDS:
        raise ValueError("drawing speed must be one of " + ", ".join(str(x) for x in FEEDS) + " mm/min")
    return int(f)


# ---------------- program text: slow drops, checks ----------------
def slow_descents(text, feed, travel_z):
    """Every first G1 after a sideways rapid is the drop to the stroke: cap its feed at DESCENT_MAX.

    Returns the new text and the extra seconds this adds compared with dropping at the drawing speed."""
    out, after_rapid, extra = [], False, 0.0
    cap = min(feed, DESCENT_MAX)
    for line in text.splitlines():
        m = _G1.match(line)
        if after_rapid:
            if not m:
                raise ValueError("program structure: a sideways rapid move is not followed by a drop")
            drop = travel_z - float(m.group(2))
            extra += drop / cap * 60 - drop / feed * 60
            line = f"{m.group(1)} F{cap}"
        after_rapid = bool(_G0XY.match(line))
        out.append(line)
    return "\n".join(out) + "\n", extra


def check_feeds(text, feed):
    """Drops at most DESCENT_MAX, drawing exactly at the program's speed."""
    after_rapid, drops = False, 0
    for n, line in enumerate(text.splitlines(), start=1):
        m = _G1.match(line)
        if m:
            f = int(m.group(3))
            if after_rapid:
                drops += 1
                if f > min(feed, DESCENT_MAX):
                    raise ValueError(f"line {n}: drop to the hand at F{f}, above {min(feed, DESCENT_MAX)}")
            elif f != feed:
                raise ValueError(f"line {n}: drawing speed F{f} is not the program speed {feed}")
        after_rapid = bool(_G0XY.match(line))
    if not drops:
        raise ValueError("program has no strokes")
    return drops


# ---------------- 4 program ----------------
def program(params):
    job = v11.job_load(params.get("job"))
    v11.prosthetic_only(job)
    v11.need(job, "plan")
    feed = draw_feed(params)
    if params.get("confirm_prosthetic") is not True or params.get("confirm_unmoved") is not True:
        raise ValueError("tick both boxes: prosthetic hand only, and the hand has not moved since the scan")
    plan_dir = v10.out_dir("plan", job["steps"]["plan"]["plan"])
    pl = v10.jload(os.path.join(plan_dir, "plan.json"))
    config = dict(pl["config"])
    if float(config["standoff_mm"]) < v14.MIN_STANDOFF_MM:
        raise ValueError(f"this design was checked at {config['standoff_mm']:g} mm - go back to Design, choose "
                         f"{v14.MIN_STANDOFF_MM:g} mm or more and check it again")
    config["draw_feed_mm_min"] = float(feed)
    config["decided_by"] = str(config.get("decided_by", "")) + f"; drawing speed {feed} mm/min chosen in Studio v16"
    c = v10.cal()
    pd = v10.new_dir("program")
    v10.jsave(os.path.join(pd, "config.json"), config)
    out = v10.run_step22(os.path.join(pd, "config.json"), write=True)
    gfiles = glob.glob(os.path.join(out, "DESIGN_HOVER_TEST_PROSTHETIC_*.gcode"))
    if len(gfiles) != 1:
        raise RuntimeError("program file not found")
    gname = os.path.basename(gfiles[0])
    with open(gfiles[0], encoding="ascii") as f:
        written = f.read()
    text, extra_s = slow_descents(written, feed, c["travel_z"])
    lines = text.splitlines()
    first_code = next(i for i, ln in enumerate(lines) if not ln.startswith("("))
    lines.insert(first_code, f"(drawing speed {feed} mm/min - drop to the hand at {min(feed, DESCENT_MAX)} mm/min or slower)")
    text = "\n".join(lines) + "\n"
    rep = v10.jload(os.path.join(out, "report.json"))
    rr = reread(text, c["travel_z"], rep["global_floor_in_program"], feed)
    check_feeds(text, feed)
    gfile = os.path.join(pd, gname)
    with open(gfile, "w", encoding="ascii", newline="\n") as f:
        f.write(text)
    shutil.copyfile(os.path.join(out, "design_hover_preview.png"), os.path.join(pd, "preview.png"))
    shutil.copyfile(os.path.join(out, "report.json"), os.path.join(pd, "step22_report.json"))
    est = round(rep["estimated_run_time_s"] + extra_s, 1)
    summary = {"schema": "web_program/2", "created": dt.datetime.now().isoformat(timespec="seconds"),
               "plan": os.path.basename(plan_dir), "program_file": gfile, "use_offset": False,
               "standoff_mm": config["standoff_mm"], "draw_feed_mm_min": feed,
               "descent_feed_mm_min": min(feed, DESCENT_MAX), "reread": rr, "nozzle_z_range": rep["nozzle_z_range"],
               "estimated_run_time_s": est, "hand_top": rep["hand_top"], "recording": pl["recording"]}
    v10.jsave(os.path.join(pd, "program.json"), summary)
    with open(gfile, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    v10.log_row(v10.LOG_FILE, 11, "Studio v16 hover program", None, "Studio Write program", "all checks pass",
                f"{gname}: {rr['lines']} lines, {rr['strokes']} strokes; standoff {config['standoff_mm']:g}; drawing "
                f"F{feed}, drops F{min(feed, DESCENT_MAX)}; re-read {rr['problems']} problems",
                "generated - not yet run", pl["recording"], "prosthetic only")
    name = os.path.basename(pd)
    v11.set_step(job, "program", {
        "program": name, "program_file": gfile, "download": f"/download_program/{name}/{gname}",
        "preview": f"/outputs/{name}/preview.png", "reread": rr, "use_offset": False, "offset_mm": 0.0,
        "standoff_mm": config["standoff_mm"], "sha256": sha, "studio": "v16", "draw_feed_mm_min": feed,
        "descent_feed_mm_min": min(feed, DESCENT_MAX), "nozzle_z_range": rep["nozzle_z_range"],
        "estimated_run_time_s": est})
    return {"job": v14.job_view(job)}


# ---------------- 5 run ----------------
def checked_program(job):
    """v14's checks with the program's own drawing speed as the limit, plus the drop-speed check."""
    pr = job["steps"]["program"]
    if pr.get("studio") == "v14":
        feed = int(v10.cal()["draw_feed_mm_min"])
    elif pr.get("studio") == "v16":
        feed = draw_feed({"draw_feed_mm_min": pr.get("draw_feed_mm_min")})
    else:
        raise ValueError("this program was written for UGS by an older Studio - write it again in step 4")
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
    rr = reread(text, c["travel_z"], floor, feed)
    check_feeds(text, feed)
    lines, end = program_lines(text, c["travel_z"], floor, feed, c["firmware_z_floor"])
    return os.path.basename(path), lines, end, rr, feed


def run_start(params):
    job = v11.job_load(params.get("job"))
    v11.prosthetic_only(job)
    v11.need(job, "program")
    v14.confirmed_run(params)
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before running a program")
    name, lines, end, rr, feed = checked_program(job)
    link = machine()
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    with v14._runs_lock:
        status = link.start_program(
            name, lines, end, on_done=lambda error: v14._record_run(job["id"], run_id, error, link.status()))
        job.setdefault("runs", []).append({
            "id": run_id, "program": job["steps"]["program"]["program"], "file": name,
            "sha256": job["steps"]["program"]["sha256"], "lines": len(lines), "end_mpos": list(end),
            "draw_feed_mm_min": feed, "recording": job["steps"]["scan"]["name"],
            "started": dt.datetime.now().isoformat(timespec="seconds"), "result": "running"})
        v11.job_save(job)
    v10.log_row(v10.LOG_FILE, 12, "Studio run start", status.get("mpos"), name,
                f"{len(lines)} lines; ends at X{end[0]:.3f} Y{end[1]:.3f} Z{end[2]:.3f}",
                f"re-read {rr['problems']} problems; {rr['strokes']} strokes; Z {rr['z_min']} to {rr['z_max']}; "
                f"drawing F{feed}, drops F{min(feed, DESCENT_MAX)}",
                "started", job["steps"]["scan"]["name"], "prosthetic only; 4 mm; no dispensing; four confirmations ticked")
    return dict(v14.machine_status(), job=v14.job_view(job))


class Handler(v15.Handler):
    server_version = "HennaStudio/0.16"
    GET_API = dict(v15.Handler.GET_API, **{"/api/studio/v16_rules": v16_rules})
    POST_API = dict(v15.Handler.POST_API, **{
        "/api/studio/program": (program, True),
        "/api/machine/run": (run_start, True),
    })

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    link = machine()
    print("Henna Studio v16 - drawing speed per program. motion_ready=false", flush=True)
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
