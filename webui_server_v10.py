"""Local web UI, revision 10: scan with the D405, plan a design, paper readings + offset, hover program, gap check.

Run:   .venv\\Scripts\\python webui_server_v10.py      Open: http://127.0.0.1:8765
Revisions 1-9 are kept unchanged. New tabs:
  0  Scan - live preview and recording from the D405 (record only; close RealSense Viewer first).
  10 Plan + paper readings - step 22 plan (no program), 3 points, paper '?' lines -> surface offset.
  11 Program + gap check - step 22 hover program (prosthetic hand only), independent re-read, gaps.
The page never talks to the CNC controller: the user pastes '?' lines and runs programs in UGS.
Calibration and path rules come only from current_calibration.json. motion_ready = false.
"""
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlparse

import webui_server as base
import webui_server_v2 as v2
import webui_server_v4 as v4
import webui_server_v9 as v9
from henna_preview import MOTION_READY
from henna_preview.grbl_status import StatusLineError, check_pose, check_xy, parse_status
from henna_preview.program_reread import ProgramReadError, reread
from henna_preview.surface_offset import SurfaceOffsetError, offset_from_touches
from henna_preview.web_log import append as log_row
from henna_preview.web_recorder import CameraError, CameraService

INDEX_V10 = os.path.join(base.WEB_DIR, "index_v10.html")
CAL_FILE = os.path.join(base.ROOT, "current_calibration.json")
LOG_FILE = os.path.join(base.ROOT, "validation_log_S2026-10-05-A.csv")
STEP22 = os.path.join(base.ROOT, "step22_design_hover_test.py")
DESIGN_DIR = os.path.join(base.ROOT, "designs")
SUBJECT = "prosthetic hand (no person)"
NAME = re.compile(r"^[0-9A-Za-z_\-]{1,80}$")
MIN_FREE_BYTES = 2_000_000_000
camera = CameraService()


def cal():
    with open(CAL_FILE, encoding="utf-8") as f:
        return json.load(f)


def new_dir(kind):
    d = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + kind)
    n = 1
    while os.path.exists(d):
        n += 1
        d = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{kind}-{n}")
    os.makedirs(d)
    return d


def out_dir(kind, name):
    if not (isinstance(name, str) and NAME.match(name) and re.search(rf"-{kind}(-\d+)?$", name)):
        raise ValueError(f"unknown {kind}")
    d = os.path.join(base.OUT_ROOT, name)
    if not (base.is_inside(d, base.OUT_ROOT) and os.path.isfile(os.path.join(d, kind + ".json"))):
        raise ValueError(f"unknown {kind}")
    return d


def jload(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def jsave(p, obj):
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")


def number(params, key, lo, hi, default=None):
    v = params.get(key, default)
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number")
    if not lo <= v <= hi:
        raise ValueError(f"{key} must be between {lo} and {hi}")
    return v


def point_commands(x, y, start_z):
    return ["$J=G53 Z-50 F300", f"$J=G53 X{x:.3f} Y{y:.3f} F500", f"$J=G53 Z{start_z:.3f} F200",
            "$J=G91 Z-0.1 F50", "?"]


# ---------------- 0 scan ----------------
def scan_record(params):
    st = parse_status(params.get("status_line"))
    c = cal()
    check_pose(st["mpos"], tuple(c["capture_pose_mpos"]))
    seconds = number(params, "seconds", 3, 10, 5)
    folder = v2.APPROVED_RECORDING_DIRS[0]
    if shutil.disk_usage(folder).free < MIN_FREE_BYTES:
        raise ValueError("less than 2 GB free for recordings - move old recordings to D: first")
    name = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".db3"
    path = os.path.join(folder, name)
    if os.path.exists(path):
        raise CameraError("a recording with this name already exists")
    try:
        info = camera.record(path, seconds)
    except Exception as e:
        # SDK, SQLite and frame-reader failures must also leave no selectable scan.
        from henna_preview.web_recorder import reject
        reject(path)                               # kept for inspection, never listed or used
        log_row(LOG_FILE, 0, "Web scan refused", st["mpos"], "web page Record", "usable recording", str(e), "REFUSED", name)
        raise
    d = new_dir("scan")
    rec = {"schema": "web_scan/1", "created": dt.datetime.now().isoformat(timespec="seconds"), "recording": path,
           "head_status_line": st["line"], "head_mpos": st["mpos"], "seconds": seconds, "verification": info,
           "motion_ready": MOTION_READY}
    jsave(os.path.join(d, "scan.json"), rec)
    log_row(LOG_FILE, 0, "Web scan (pose 1)", st["mpos"], f"web page Record {seconds:g} s", "usable recording",
            f"{info['frame_pairs']} frame pairs; depth units {info['depth_units_m']:g}; {info['camera_settings']} camera "
            f"settings; median depth {info['median_depth_mm']} mm; {len(info['late_repeated_option_messages_removed'])} "
            "late repeated option messages removed", "recorded", name)
    return {"recording": path, "name": name, "scan": os.path.basename(d), "verification": info}


def scan_for(recording):
    best = None
    for p in glob.glob(os.path.join(base.OUT_ROOT, "*-scan", "scan.json")):
        s = jload(p)
        if os.path.basename(s["recording"]) == recording and (best is None or s["created"] > best["created"]):
            best = s
    return best


# ---------------- 10 plan + paper readings ----------------
def setup():
    regions = []
    for r in v4.list_regions():
        if not r["accepted"]:
            continue
        s = scan_for(r["recording"])
        regions.append(dict(r, scan_status_line=s["head_status_line"] if s else None))
    regions.sort(key=lambda r: r["created"], reverse=True)
    designs = sorted(os.path.relpath(p, base.ROOT).replace("\\", "/") for p in glob.glob(os.path.join(DESIGN_DIR, "*.svg")))
    c = cal()
    return {"regions": regions, "designs": designs, "calibration": c,
            "defaults": {"fit_mm": 30.0, "standoff_mm": c["standoff_mm_allowed"][0], "paper_mm": 0.10}}


def run_step22(config_path, write):
    cmd = [sys.executable, STEP22, "--config", config_path] + (["--write-gcode"] if write else [])
    proc = subprocess.run(cmd, cwd=base.ROOT, capture_output=True, text=True, timeout=900)
    if proc.returncode != 0:
        err = proc.stderr
        k = max(err.rfind("Error:"), err.rfind("Error\n"))
        start = err.rfind("\n", 0, k) + 1 if k > 0 else max(0, len(err) - 600)
        raise ValueError("step 22 refused: " + err[start:].strip()[:1500])
    m = re.search(r"^Wrote: (.+)$", proc.stdout, re.M)
    if not m:
        raise RuntimeError("step 22 did not report its output folder")
    return m.group(1).strip()


def plan(params):
    c = cal()
    rdir = v4.region_dir_from_id(params.get("region"))
    region = jload(os.path.join(rdir, "region.json"))
    if not region.get("accepted"):
        raise ValueError("region is not accepted")
    recording = os.path.basename(region["source"]["recording"])
    line = params.get("status_line") or ""
    if not line.strip():
        s = scan_for(recording)
        if not s:
            raise ValueError("paste the ? line you sent before recording this scan")
        line = s["head_status_line"]
    st = parse_status(line)
    check_pose(st["mpos"], tuple(c["capture_pose_mpos"]))
    svg = params.get("svg")
    if not (isinstance(svg, str) and svg.startswith("designs/") and svg.endswith(".svg")
            and base.is_inside(os.path.join(base.ROOT, svg), DESIGN_DIR) and os.path.isfile(os.path.join(base.ROOT, svg))):
        raise ValueError("choose a design from the designs folder")
    fit = number(params, "fit_mm", *c["fit_mm_allowed"])
    standoff = number(params, "standoff_mm", *c["standoff_mm_allowed"])
    centre = "region_centroid"
    if params.get("centre_machine_xy") is not None:          # optional: placed by the user
        cx = params["centre_machine_xy"]
        if not (isinstance(cx, list) and len(cx) == 2):
            raise ValueError("centre_machine_xy must be [x, y]")
        centre = [round(number({"x": cx[0]}, "x", 0, 225), 3), round(number({"y": cx[1]}, "y", 0, 176), 3)]
    rotation = round(number(params, "rotation_deg", -180, 180, 0.0), 2)
    d = new_dir("plan")
    config = {"session_id": "web v10", "purpose": "DESIGN HOVER TEST over the PROSTHETIC hand - no person - no dispensing",
              "decided_by": c["decided_by"] + f"; standoff {standoff:g} mm and design chosen in web page v10",
              "subject": SUBJECT,
              "hand_capture": {"recording": recording, "head": dict(zip("xyz", st["mpos"])), "source": st["line"]},
              "hand_run_dir": os.path.relpath(region["source"]["hand_run"], base.ROOT).replace("\\", "/"),
              "region_dir": os.path.relpath(rdir, base.ROOT).replace("\\", "/"),
              "design": {"svg": svg, "fit_mm": fit, "centre_machine_xy": centre, "rotation_deg": rotation, "jump_mm": 3.0},
              "standoff_mm": standoff}
    for k in ("frames", "min_valid_frac", "max_mad_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min",
              "travel_z", "firmware_z_floor", "xy_calibration", "z_calibration", "depth_scale"):
        config[k] = c[k]
    jsave(os.path.join(d, "config.json"), config)
    out = run_step22(os.path.join(d, "config.json"), write=False)
    rep = jload(os.path.join(out, "report.json"))
    shutil.copyfile(os.path.join(out, "design_hover_preview.png"), os.path.join(d, "preview.png"))
    shutil.copyfile(os.path.join(out, "report.json"), os.path.join(d, "step22_report.json"))
    points = [dict(g, commands=point_commands(g["x"], g["y"], g["program_z"]), stop_z=round(g["camera_skin_z"], 3))
              for g in rep["gap_check_points"]]
    summary = {"schema": "web_plan/1", "created": dt.datetime.now().isoformat(timespec="seconds"), "config": config,
               "recording": recording, "head_mpos": st["mpos"], "design": rep["design"], "hand_top": rep["hand_top"],
               "nozzle_z_range": rep["nozzle_z_range"], "estimated_run_time_s": rep["estimated_run_time_s"],
               "points": points, "step22_output": out, "offset": None}
    jsave(os.path.join(d, "plan.json"), summary)
    log_row(LOG_FILE, 10, "Web plan (no program)", st["mpos"], "web page Plan", "whole design on usable skin",
            f"{rep['design']['strokes']} strokes fit at X{rep['design']['centre_machine_xy'][0]} Y{rep['design']['centre_machine_xy'][1]}; "
            f"standoff {standoff:g}; points " + " / ".join(f"X{p['x']} Y{p['y']} program {p['program_z']} camera {p['camera_skin_z']}"
                                                            for p in points), "plan pass - no program", recording)
    name = os.path.basename(d)
    return dict(summary, plan=name, preview=f"/outputs/{name}/preview.png")


def readings(points, lines, paper):
    if not isinstance(lines, list) or len(lines) != len(points):
        raise ValueError(f"paste {len(points)} ? lines, one per point")
    out = []
    for p, line in zip(points, lines):
        st = parse_status(line)
        check_xy(st["mpos"], p["x"], p["y"])
        out.append({"name": p["name"], "x": p["x"], "y": p["y"], "program_z": p["program_z"],
                    "camera_skin_z": p["camera_skin_z"], "paper_reading_z": st["mpos"][2],
                    "touch_z": round(st["mpos"][2] - paper, 3), "mpos": st["mpos"]})
    return out


def offset(params):
    c = cal()
    d = out_dir("plan", params.get("plan"))
    pl = jload(os.path.join(d, "plan.json"))
    paper = number(params, "paper_mm", *c["paper_mm_allowed"])
    touches = readings(pl["points"], params.get("lines"), paper)
    for t in touches:
        log_row(LOG_FILE, 10, f"Web paper reading {t['name'].split()[0]}", t["mpos"], "paper feeler + ?", "",
                f"reading {t['paper_reading_z']} -> surface {t['touch_z']}; camera {t['camera_skin_z']} -> camera "
                f"{t['touch_z'] - t['camera_skin_z']:.2f} mm too deep", "recorded", pl["recording"])
    try:
        res = offset_from_touches(touches, pl["config"]["standoff_mm"])
        verdict, err = "ACCEPTED", None
    except SurfaceOffsetError as e:
        per = [round(t["touch_z"] - t["camera_skin_z"], 3) for t in touches]
        res, verdict, err = {"per_touch_mm": per, "spread_mm": round(max(per) - min(per), 3), "applied_mm": None}, "REFUSED", str(e)
    rec = {"schema": "surface_offset/1", "subject": SUBJECT, "recording": pl["recording"],
           "method": "paper feeler from the program Z in 0.1 mm steps until it drags; touch_z = reading - paper",
           "feeler_mm": paper, "max_spread_mm": 0.5, "touches": touches, "verdict": verdict, "result": res, "error": err,
           "created": dt.datetime.now().isoformat(timespec="seconds")}
    jsave(os.path.join(d, "surface_offset.json"), rec)
    pl["offset"] = {"verdict": verdict, "applied_mm": res.get("applied_mm"), "file": os.path.join(d, "surface_offset.json")}
    jsave(os.path.join(d, "plan.json"), pl)
    log_row(LOG_FILE, 10, "Web surface offset", None, "", "touch offsets within 0.5 mm",
            f"per point {res['per_touch_mm']} spread {res['spread_mm']}" + (f"; applied {res['applied_mm']}" if res.get("applied_mm") is not None else ""),
            verdict, pl["recording"], err or "")
    return {"verdict": verdict, "error": err, "result": res, "touches": touches}


# ---------------- 11 program + gap check ----------------
def program(params):
    c = cal()
    d = out_dir("plan", params.get("plan"))
    if params.get("confirm_prosthetic") is not True or params.get("confirm_unmoved") is not True:
        raise ValueError("tick both boxes: prosthetic hand only, and the hand has not moved since the scan")
    pl = jload(os.path.join(d, "plan.json"))
    config = dict(pl["config"])
    use_offset = params.get("use_offset") is True
    if use_offset:
        if not pl.get("offset") or pl["offset"]["verdict"] != "ACCEPTED":
            raise ValueError("no accepted offset for this plan")
        config["surface_offset_file"] = os.path.relpath(pl["offset"]["file"], base.ROOT).replace("\\", "/")
    pd = new_dir("program")
    jsave(os.path.join(pd, "config.json"), config)
    out = run_step22(os.path.join(pd, "config.json"), write=True)
    gfiles = glob.glob(os.path.join(out, "DESIGN_HOVER_TEST_PROSTHETIC_*.gcode"))
    if len(gfiles) != 1:
        raise RuntimeError("program file not found")
    gname = os.path.basename(gfiles[0])
    shutil.copyfile(gfiles[0], os.path.join(pd, gname))
    shutil.copyfile(os.path.join(out, "design_hover_preview.png"), os.path.join(pd, "preview.png"))
    shutil.copyfile(os.path.join(out, "report.json"), os.path.join(pd, "step22_report.json"))
    rep = jload(os.path.join(out, "report.json"))
    with open(os.path.join(pd, gname), encoding="ascii") as f:
        rr = reread(f.read(), c["travel_z"], rep["global_floor_in_program"], c["draw_feed_mm_min"])
    points = [dict(g, commands=point_commands(g["x"], g["y"], g["program_z"]),
                   stop_z=round(g["program_z"] - config["standoff_mm"] - 2.0, 3)) for g in rep["gap_check_points"]]
    summary = {"schema": "web_program/1", "created": dt.datetime.now().isoformat(timespec="seconds"),
               "plan": os.path.basename(d), "program_file": os.path.join(pd, gname), "use_offset": use_offset,
               "surface_offset": rep.get("surface_offset"), "standoff_mm": config["standoff_mm"], "reread": rr,
               "nozzle_z_range": rep["nozzle_z_range"], "estimated_run_time_s": rep["estimated_run_time_s"],
               "hand_top": rep["hand_top"], "points": points, "recording": pl["recording"]}
    jsave(os.path.join(pd, "program.json"), summary)
    log_row(LOG_FILE, 11, "Web hover program", None, "web page Write program", "all checks pass",
            f"{gname}: {rr['lines']} lines, {rr['strokes']} strokes, {rr['draw_moves']} draw moves; standoff {config['standoff_mm']:g}"
            + (f"; offset {rep['surface_offset']['applied_mm']}" if use_offset else "; no offset")
            + f"; independent re-read {rr['problems']} problems", "generated - not yet run", pl["recording"], "prosthetic only")
    name = os.path.basename(pd)
    return dict(summary, program=name, preview=f"/outputs/{name}/preview.png",
                download=f"/download_program/{name}/{gname}")


def gapcheck(params):
    c = cal()
    pd = out_dir("program", params.get("program"))
    pr = jload(os.path.join(pd, "program.json"))
    paper = number(params, "paper_mm", *c["paper_mm_allowed"])
    rs = readings(pr["points"], params.get("lines"), paper)
    gaps = [round(r["program_z"] - r["touch_z"], 3) for r in rs]
    clean = params.get("clean_run") is True
    worst = min(gaps)
    verdict = ("PASS" if clean and worst >= c["floor_above_skin_mm"] else
               "TOO CLOSE" if worst < c["floor_above_skin_mm"] else "CHECK - run not confirmed clean")
    for r, g in zip(rs, gaps):
        log_row(LOG_FILE, 11, f"Web gap check {r['name'].split()[0]}", r["mpos"], "program Z then paper feeler + ?",
                f"about {pr['standoff_mm']:g} mm", f"reading {r['paper_reading_z']} -> surface {r['touch_z']} -> real gap {g:.2f} mm",
                "recorded", pr["recording"])
    log_row(LOG_FILE, 11, "Web hover result", None, os.path.basename(pr["program_file"]), "real gap >= floor margin",
            f"real gaps {' / '.join(f'{g:.2f}' for g in gaps)} mm; run clean (user): {'yes' if clean else 'not confirmed'}",
            verdict, pr["recording"])
    pr["gap_check"] = {"gaps_mm": gaps, "clean_run": clean, "verdict": verdict, "readings": rs, "paper_mm": paper}
    jsave(os.path.join(pd, "program.json"), pr)
    return pr["gap_check"]


class Handler(v9.Handler):
    server_version = "HennaPreview/0.10"

    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/index_v10.html"):
            return self._file(INDEX_V10, "text/html; charset=utf-8")
        if u.path == "/api/cam/status":
            return self._send(200, camera.status())
        if u.path == "/api/cam/preview.jpg":
            j = camera.jpeg()
            return self._send(200, j, "image/jpeg") if j else self._send(404, {"error": "no frame yet"})
        if u.path == "/api/v10/setup":
            try:
                return self._send(200, setup())
            except (ValueError, OSError) as e:
                return self._send(400, {"error": str(e)})
        if u.path.startswith("/download_program/"):
            parts = unquote(u.path[len("/download_program/"):]).split("/")
            if len(parts) == 2 and NAME.match(parts[0]) and re.match(r"^DESIGN_HOVER_TEST_PROSTHETIC_[0-9-]+\.gcode$", parts[1]):
                p = os.path.join(base.OUT_ROOT, *parts)
                if base.is_inside(p, base.OUT_ROOT) and os.path.isfile(p):
                    with open(p, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=ascii")
                    self.send_header("Content-Disposition", f'attachment; filename="{parts[1]}"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            return self._send(404, {"error": "not found"})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        handlers = {"/api/cam/start": (lambda p: camera.start_preview(), False),
                    "/api/cam/stop": (lambda p: camera.stop(), False),
                    "/api/cam/record": (scan_record, True), "/api/v10/plan": (plan, True),
                    "/api/v10/offset": (offset, True), "/api/v10/program": (program, True),
                    "/api/v10/gapcheck": (gapcheck, True)}
        if path not in handlers:
            return super().do_POST()
        if not self._host_ok():
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "send JSON"})
        try:
            params = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 20000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad JSON"})
        fn, lock = handlers[path]
        if lock and not base._run_lock.acquire(blocking=False):
            return self._send(409, {"error": "another step is still running"})
        try:
            self._send(200, fn(params))
        except (ValueError, StatusLineError, CameraError, SurfaceOffsetError, ProgramReadError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
        finally:
            if lock:
                base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna preview web UI v10 (scan, plan, paper readings, prosthetic hover programs; motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        camera.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
