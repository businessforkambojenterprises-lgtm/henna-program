"""Henna Studio - web UI revision 11: one guided workflow from scan to a checked hover program.

Run:   .venv\\Scripts\\python webui_server_v11.py      Open: http://127.0.0.1:8765  (classic tabs: /classic)
Steps: 1 Scan - 2 Hand & region - 3 Design - 4 Surface check - 5 Program - 6 Run & verify.
Each workflow is a JOB (outputs/webui/<time>-job/job.json) that can be resumed.

Rules kept from v10: the page never talks to the CNC controller (programs are opened in UGS by the user);
calibration and path rules come only from current_calibration.json; hover programs are written only
for the PROSTHETIC hand. A real-hand job is camera-only: scan, region and design preview (step 21),
never paper readings or a program. motion_ready = false.
"""
import datetime as dt
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

import webui_server as base
import webui_server_v2 as v2
import webui_server_v3 as v3
import webui_server_v4 as v4
import webui_server_v10 as v10
from henna_preview import MOTION_READY
from henna_preview.calibration import predict
from henna_preview.grbl_status import StatusLineError, check_pose, parse_status
from henna_preview.recording import frame_pair_count
from henna_preview.region import RegionError, load_bundle, polygon_mask
from henna_preview.svg_design import SvgDesignError, place, read_svg
from step05_region import holes_png_data_url, png_data_url
from step19_hand_path_avg import region_centre_machine

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio.html")
STEP21 = os.path.join(base.ROOT, "step21_design_preview.py")
UPLOAD_DIR = os.path.join(v10.DESIGN_DIR, "uploads")
SUBJECTS = {"prosthetic": v10.SUBJECT, "person": "real hand (camera only - no programs)"}
ORDER = ["scan", "hand", "region", "design", "plan", "offset", "program", "gap"]
MAX_SVG_BYTES = 300_000
_frame_cache = {}
_design_cache = {}


# ---------------- jobs ----------------
def job_path(job_id):
    if not (isinstance(job_id, str) and v10.NAME.match(job_id) and re.search(r"-job(-\d+)?$", job_id)):
        raise ValueError("unknown job")
    p = os.path.join(base.OUT_ROOT, job_id, "job.json")
    if not (base.is_inside(p, base.OUT_ROOT) and os.path.isfile(p)):
        raise ValueError("unknown job")
    return p


def job_load(job_id):
    return v10.jload(job_path(job_id))


def status_of(job):
    s = job["steps"]
    if job["subject"] == "person":
        for k, label in (("scan", "scan"), ("hand", "hand"), ("region", "region"), ("design", "design")):
            if k not in s:
                return label
        return "done (camera only)"
    for k in ("scan", "hand", "region", "plan", "offset", "program", "gap"):
        if k not in s:
            return {"plan": "design", "offset": "surface", "gap": "run"}.get(k, k)
    return "done" if s["gap"].get("verdict") == "PASS" else "run"


def job_save(job):
    job["updated"] = dt.datetime.now().isoformat(timespec="seconds")
    job["status"] = status_of(job)
    v10.jsave(job_path(job["id"]), job)
    return job


def set_step(job, key, value):
    for k in ORDER[ORDER.index(key) + 1:]:
        job["steps"].pop(k, None)           # a redone step invalidates everything after it
    job["steps"][key] = value
    return job_save(job)


def job_new(params):
    subject = params.get("subject")
    if subject not in SUBJECTS:
        raise ValueError("choose prosthetic hand or real hand")
    d = v10.new_dir("job")
    job = {"schema": "studio_job/1", "id": os.path.basename(d), "created": dt.datetime.now().isoformat(timespec="seconds"),
           "subject": subject, "subject_text": SUBJECTS[subject], "motion_ready": MOTION_READY, "steps": {}}
    v10.jsave(os.path.join(d, "job.json"), job)
    return job_save(job)


def jobs_list():
    out = []
    for p in glob.glob(os.path.join(base.OUT_ROOT, "*-job", "job.json")):
        try:
            j = v10.jload(p)
        except (OSError, ValueError):
            continue
        s = j.get("steps", {})
        out.append({"id": j["id"], "created": j["created"], "updated": j.get("updated", j["created"]),
                    "subject": j["subject"], "status": j.get("status", ""),
                    "recording": s.get("scan", {}).get("name"),
                    "design": os.path.basename(s.get("design", {}).get("svg", "")) or None,
                    "verdict": s.get("gap", {}).get("verdict")})
    return sorted(out, key=lambda j: j["updated"], reverse=True)[:40]


def need(job, *keys):
    for k in keys:
        if k not in job["steps"]:
            raise ValueError(f"finish the earlier step first ({k})")


# ---------------- 1 scan ----------------
def scan(params):
    job = job_load(params.get("job"))
    r = v10.scan_record(params)
    v = r["verification"]
    st = parse_status(params.get("status_line"))
    set_step(job, "scan", {"recording": r["recording"], "name": r["name"], "status_line": st["line"], "mpos": st["mpos"],
                           "frame_pairs": v["frame_pairs"], "checks": {"depth_units_m": v["depth_units_m"],
                           "camera_settings": v["camera_settings"], "median_depth_mm": v["median_depth_mm"]},
                           "source": "studio scan"})
    return {"job": job, "verification": v}


def use_recording(params):
    job = job_load(params.get("job"))
    path = v2.resolve_recording(params.get("path"))
    st = parse_status(params.get("status_line"))
    check_pose(st["mpos"], tuple(v10.cal()["capture_pose_mpos"]))
    pairs = frame_pair_count(path)
    if not pairs or pairs < 30:
        raise ValueError("this recording has too few frames")
    set_step(job, "scan", {"recording": path, "name": os.path.basename(path), "status_line": st["line"],
                           "mpos": st["mpos"], "frame_pairs": pairs, "source": "existing recording"})
    return {"job": job}


# ---------------- 2 hand & region ----------------
def detect(params):
    job = job_load(params.get("job"))
    need(job, "scan")
    sc = job["steps"]["scan"]
    r = v2.run_step04({"path": sc["recording"], "frame": int(sc["frame_pairs"]) // 2, "min_confidence": 0.5})
    rep = r["report"]
    h = rep["hands"][0] if rep.get("hands") else None
    info = {"run": "web:" + r["run_id"], "detected": bool(rep.get("hand_detected")), "frame": int(sc["frame_pairs"]) // 2,
            "score": round(h["score"], 3) if h else None,
            "landmarks_on_missing_depth": sum(1 for l in h["landmarks"] if l["measured_depth_mm"] is None) if h else None}
    if not info["detected"]:
        raise ValueError("no hand found in this scan - check the hand is fully in view and scan again")
    set_step(job, "hand", info)
    return {"job": job, "hand": hand_payload({"job": job["id"]})}


def hand_payload(params):
    job = job_load(params.get("job"))
    need(job, "hand")
    return v3.hand_run_payload(job["steps"]["hand"]["run"], v3.margin_of(params))


def region_check(params):
    job = job_load(params.get("job"))
    need(job, "hand")
    return v3.region_check({"run": job["steps"]["hand"]["run"], "polygon": params.get("polygon")})


def region_accept(params):
    job = job_load(params.get("job"))
    need(job, "hand")
    r = v3.region_accept({"run": job["steps"]["hand"]["run"], "polygon": params.get("polygon"),
                          "margin": params.get("margin"), "edited": bool(params.get("edited")), "confirm": True})
    rid = "web:" + os.path.basename(r["output_folder"])
    st = r["record"]["stats"]
    set_step(job, "region", {"id": rid, "polygon_px": r["record"]["polygon_px"], "overlay": r["overlay"],
                             "inside_px": st["inside_px"], "missing_pct": st["missing_pct"],
                             "depth_mm_range": st["depth_mm_range"]})
    return {"job": job}


# ---------------- 3 design ----------------
def design_entry(path):
    rel = os.path.relpath(path, base.ROOT).replace("\\", "/")
    key = (rel, os.path.getmtime(path))
    if key in _design_cache:
        return _design_cache[key]
    try:
        d = read_svg(path)
        filled = [s["source"] for s in d["strokes"] if s["filled_only"]]
        placed, _ = place(d, 1.0, (0.0, 0.0), 0.0, spacing_mm=0.01)
        e = {"svg": rel, "name": os.path.splitext(os.path.basename(path))[0].replace("_", " "),
             "uploaded": "/uploads/" in rel, "strokes": len(placed), "filled_shapes": filled,
             "usable": not filled, "length_per_mm": round(sum(s["length_mm"] for s in placed), 3),
             "preview": [{"closed": s["closed"], "xy": np.round(s["plan"], 4).tolist()} for s in placed]}
    except SvgDesignError as ex:
        e = {"svg": rel, "name": os.path.basename(path), "usable": False, "error": str(ex), "preview": []}
    _design_cache[key] = e
    return e


def designs():
    paths = sorted(glob.glob(os.path.join(v10.DESIGN_DIR, "*.svg"))) + sorted(glob.glob(os.path.join(UPLOAD_DIR, "*.svg")))
    return {"designs": [design_entry(p) for p in paths]}


def design_upload(params):
    text, name = params.get("svg"), params.get("filename", "design")
    if not isinstance(text, str) or "<svg" not in text:
        raise ValueError("this is not an SVG file")
    if len(text.encode("utf-8")) > MAX_SVG_BYTES:
        raise ValueError("SVG is larger than 300 kB")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        raise ValueError("SVG files with DOCTYPE or ENTITY declarations are not accepted")
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(str(name)))[0]).strip("_")[:40] or "design"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".svg", dir=UPLOAD_DIR)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    try:
        d = read_svg(tmp)
        filled = [s["source"] for s in d["strokes"] if s["filled_only"]]
        if filled:
            raise ValueError(f"the design has {len(filled)} filled shape(s) without a line - henna needs line art "
                             "(strokes), not filled areas")
        final, n = os.path.join(UPLOAD_DIR, stem + ".svg"), 1
        while os.path.exists(final):
            n += 1
            final = os.path.join(UPLOAD_DIR, f"{stem}-{n}.svg")
        os.replace(tmp, final)
    except SvgDesignError as e:
        raise ValueError(f"could not read the SVG: {e}")
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    return {"design": design_entry(final)}


def design_frame(params):
    """Photo, region and a machine X/Y -> pixel mapping for placing the design by hand (preview only)."""
    job = job_load(params.get("job"))
    need(job, "region")
    key = (job["id"], job["steps"]["region"]["id"])
    if key in _frame_cache:
        return _frame_cache[key]
    b = load_bundle(v3.run_dir_from_id(job["steps"]["hand"]["run"]))
    poly = job["steps"]["region"]["polygon_px"]
    c, head = v10.cal(), dict(zip("xyz", job["steps"]["scan"]["mpos"]))
    xy = v10.jload(os.path.join(base.ROOT, c["xy_calibration"]))["result"]
    raw, scale, intr = b["depth_raw_aligned"], b["depth_scale_m_per_unit"], b["colour_intrinsics"]
    h, w = raw.shape
    m = polygon_mask(poly, w, h) & (raw > 0)
    vs, us = np.nonzero(m)
    k = slice(None, None, max(1, len(us) // 4000))
    us, vs = us[k], vs[k]
    z = raw[vs, us].astype(float) * scale * 1000.0
    cam = np.column_stack([(us - intr["ppx"]) / intr["fx"] * z, (vs - intr["ppy"]) / intr["fy"] * z])
    mach = predict(cam, [head["x"], head["y"]], np.asarray(xy["R"]), np.asarray(xy["t_mm"]))
    A = np.column_stack([mach, np.ones(len(mach))])
    M, *_ = np.linalg.lstsq(A, np.column_stack([us, vs]).astype(float), rcond=None)
    rms = float(np.sqrt(np.mean(np.sum((A @ M - np.column_stack([us, vs])) ** 2, axis=1))))
    frame = {"depth_raw_aligned": raw, "depth_scale_m_per_unit": scale, "colour_intrinsics": intr}
    centre, _ = region_centre_machine(poly, frame, head, xy)
    out = {"width": w, "height": h, "colour": png_data_url(Image.fromarray(b["colour_rgb"])),
           "holes": holes_png_data_url(raw), "polygon_px": poly, "affine_machine_to_px": M.T.round(6).tolist(),
           "affine_rms_px": round(rms, 2), "centre_machine_xy": centre,
           "px_per_mm": round(float(np.sqrt(abs(np.linalg.det(M[:2, :])))), 3)}
    _frame_cache.clear()
    _frame_cache[key] = out
    return out


def _config(job, svg, fit, rotation, centre, standoff):
    c = v10.cal()
    region = v10.jload(os.path.join(v4.region_dir_from_id(job["steps"]["region"]["id"]), "region.json"))
    sc = job["steps"]["scan"]
    cfg = {"session_id": "studio v11", "purpose": "DESIGN PREVIEW - " + job["subject_text"], "subject": job["subject_text"],
           "decided_by": c["decided_by"] + "; design, size, position and nozzle height chosen in Henna Studio",
           "hand_capture": {"recording": os.path.basename(sc["recording"]), "head": dict(zip("xyz", sc["mpos"])),
                            "source": sc["status_line"]},
           "hand_run_dir": os.path.relpath(region["source"]["hand_run"], base.ROOT).replace("\\", "/"),
           "region_dir": os.path.relpath(v4.region_dir_from_id(job["steps"]["region"]["id"]), base.ROOT).replace("\\", "/"),
           "design": {"svg": svg, "fit_mm": fit, "centre_machine_xy": centre, "rotation_deg": rotation, "jump_mm": 3.0},
           "standoff_mm": standoff}
    for k in ("frames", "min_valid_frac", "max_mad_mm", "floor_above_skin_mm", "draw_feed_mm_min", "travel_feed_mm_min",
              "travel_z", "firmware_z_floor", "xy_calibration", "z_calibration", "depth_scale"):
        cfg[k] = c[k]
    return cfg


def _preview21(job, svg, fit, rotation, centre, standoff):
    """Step 21 (preview only, any subject): verdict, problems and a picture even when refused."""
    d = v10.new_dir("check")
    v10.jsave(os.path.join(d, "config.json"), _config(job, svg, fit, rotation, centre, standoff))
    p = subprocess.run([sys.executable, STEP21, "--config", os.path.join(d, "config.json")], cwd=base.ROOT,
                       capture_output=True, text=True, timeout=900)
    m = re.search(r"^Wrote: (.+)$", p.stdout, re.M)
    if not m:
        err = p.stderr.strip().splitlines()
        raise ValueError("design check failed: " + (err[-1] if err else "unknown error"))
    out = m.group(1).strip()
    shutil.copyfile(os.path.join(out, "design_preview.png"), os.path.join(d, "preview.png"))
    rep = v10.jload(os.path.join(out, "report.json"))
    return {"verdict": "PASS" if rep["verdict"].startswith("PASS") else "REFUSED", "problems": rep["problems"],
            "preview": f"/outputs/{os.path.basename(d)}/preview.png", "strokes": rep["design"]["strokes"],
            "line_length_mm": rep["design"]["line_length_mm"], "estimated_run_time_s": rep["estimated_run_time_s"],
            "nozzle_z_range": rep["nozzle_z_range"], "skin_z_range": rep["skin_z_range"], "check": os.path.basename(d)}


def check_design(params):
    job = job_load(params.get("job"))
    need(job, "region")
    c = v10.cal()
    svg = params.get("svg")
    e = next((x for x in designs()["designs"] if x["svg"] == svg), None)
    if not e or not e["usable"]:
        raise ValueError("choose a usable design")
    fit = v10.number(params, "fit_mm", *c["fit_mm_allowed"])
    standoff = v10.number(params, "standoff_mm", *c["standoff_mm_allowed"])
    rotation = round(v10.number(params, "rotation_deg", -180, 180, 0.0), 2)
    cx = params.get("centre_machine_xy")
    if not (isinstance(cx, list) and len(cx) == 2):
        raise ValueError("centre_machine_xy must be [x, y]")
    centre = [round(v10.number({"x": cx[0]}, "x", 0, 225), 3), round(v10.number({"y": cx[1]}, "y", 0, 176), 3)]
    design = {"svg": svg, "fit_mm": fit, "rotation_deg": rotation, "centre_machine_xy": centre, "standoff_mm": standoff}
    if job["subject"] == "person":
        res = _preview21(job, svg, fit, rotation, centre, standoff)
        set_step(job, "design", dict(design, verdict=res["verdict"], preview=res["preview"]))
        return {"job": job, "result": res}
    try:
        pl = v10.plan({"region": job["steps"]["region"]["id"], "status_line": job["steps"]["scan"]["status_line"],
                       "svg": svg, "fit_mm": fit, "standoff_mm": standoff, "centre_machine_xy": centre, "rotation_deg": rotation})
    except ValueError as ex:
        if "step 22 refused" not in str(ex):
            raise
        res = _preview21(job, svg, fit, rotation, centre, standoff)   # show which strokes failed
        if res["verdict"] == "PASS":
            res = dict(res, verdict="REFUSED", problems=[str(ex)])
        set_step(job, "design", dict(design, verdict="REFUSED", preview=res["preview"]))
        return {"job": job, "result": res}
    res = {"verdict": "PASS", "problems": [], "preview": pl["preview"], "strokes": pl["design"]["strokes"],
           "line_length_mm": pl["design"]["line_length_mm"],
           "estimated_run_time_s": pl["estimated_run_time_s"], "nozzle_z_range": pl["nozzle_z_range"]}
    job["steps"]["design"] = dict(design, verdict="PASS", preview=pl["preview"])
    set_step(job, "plan", {"plan": pl["plan"], "points": pl["points"], "preview": pl["preview"],
                           "nozzle_z_range": pl["nozzle_z_range"], "estimated_run_time_s": pl["estimated_run_time_s"],
                           "hand_top": pl["hand_top"]})
    return {"job": job, "result": res}


# ---------------- 4-6 surface check, program, run & verify (prosthetic only) ----------------
def prosthetic_only(job):
    if job["subject"] != "prosthetic":
        raise ValueError("paper readings and programs are for the prosthetic hand only - never a person")


def offset(params):
    job = job_load(params.get("job"))
    prosthetic_only(job)
    need(job, "plan")
    r = v10.offset({"plan": job["steps"]["plan"]["plan"], "lines": params.get("lines"), "paper_mm": params.get("paper_mm")})
    set_step(job, "offset", {"verdict": r["verdict"], "applied_mm": r["result"].get("applied_mm"),
                             "per_point_mm": r["result"]["per_touch_mm"], "spread_mm": r["result"]["spread_mm"],
                             "error": r["error"], "touches": r["touches"]})
    return {"job": job, "result": r}


def skip_offset(params):
    job = job_load(params.get("job"))
    prosthetic_only(job)
    need(job, "plan")
    set_step(job, "offset", {"verdict": "SKIPPED", "applied_mm": None})
    v10.log_row(v10.LOG_FILE, 10, "Studio: surface offset skipped", None, "", "", "user skipped the paper readings",
                "skipped", job["steps"]["scan"]["name"])
    return {"job": job}


def program(params):
    job = job_load(params.get("job"))
    prosthetic_only(job)
    need(job, "plan", "offset")
    use = job["steps"]["offset"]["verdict"] == "ACCEPTED" and params.get("use_offset") is True
    r = v10.program({"plan": job["steps"]["plan"]["plan"], "use_offset": use,
                     "confirm_prosthetic": params.get("confirm_prosthetic") is True,
                     "confirm_unmoved": params.get("confirm_unmoved") is True})
    set_step(job, "program", {"program": r["program"], "program_file": r["program_file"], "download": r["download"],
                              "preview": r["preview"], "reread": r["reread"], "use_offset": use,
                              "offset_mm": r["surface_offset"]["applied_mm"] if use else 0.0,
                              "nozzle_z_range": r["nozzle_z_range"], "estimated_run_time_s": r["estimated_run_time_s"],
                              "points": r["points"]})
    return {"job": job}


def gapcheck(params):
    job = job_load(params.get("job"))
    prosthetic_only(job)
    need(job, "program")
    r = v10.gapcheck({"program": job["steps"]["program"]["program"], "lines": params.get("lines"),
                      "paper_mm": params.get("paper_mm"), "clean_run": params.get("clean_run") is True})
    set_step(job, "gap", r)
    return {"job": job, "result": r}


class Handler(v10.Handler):
    server_version = "HennaStudio/0.11"
    GET_API = {"/api/studio/jobs": lambda q: {"jobs": jobs_list()},
               "/api/studio/job": lambda q: job_load(q.get("id", [""])[0]),
               "/api/studio/designs": lambda q: designs(),
               "/api/studio/design_frame": lambda q: design_frame({"job": q.get("job", [""])[0]}),
               "/api/studio/hand": lambda q: hand_payload({"job": q.get("job", [""])[0], "margin": q.get("margin", [0.15])[0]}),
               "/api/studio/calibration": lambda q: v10.cal()}
    POST_API = {"/api/studio/job_new": (job_new, False), "/api/studio/scan": (scan, True),
                "/api/studio/use_recording": (use_recording, True), "/api/studio/detect": (detect, True),
                "/api/studio/region_check": (region_check, True), "/api/studio/region_accept": (region_accept, True),
                "/api/studio/design_upload": (design_upload, True), "/api/studio/check_design": (check_design, True),
                "/api/studio/offset": (offset, True), "/api/studio/skip_offset": (skip_offset, True),
                "/api/studio/program": (program, True), "/api/studio/gapcheck": (gapcheck, True)}
    ERRORS = (ValueError, StatusLineError, RegionError, SvgDesignError, v10.CameraError, v10.SurfaceOffsetError,
              v10.ProgramReadError)

    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/studio"):
            return self._file(STUDIO_HTML, "text/html; charset=utf-8")
        if u.path == "/classic":
            return self._file(v10.INDEX_V10, "text/html; charset=utf-8")
        if u.path in self.GET_API:
            try:
                return self._send(200, self.GET_API[u.path](parse_qs(u.query)))
            except self.ERRORS as e:
                return self._send(400, {"error": str(e)})
            except Exception as e:
                return self._send(500, {"error": f"{type(e).__name__}: {e}"})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in self.POST_API:
            return super().do_POST()
        if not self._host_ok():
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "send JSON"})
        try:
            params = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 400_000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad JSON"})
        fn, lock = self.POST_API[path]
        if lock and not base._run_lock.acquire(blocking=False):
            return self._send(409, {"error": "another step is still running - wait for it to finish"})
        try:
            self._send(200, fn(params))
        except self.ERRORS as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": f"{type(e).__name__}: {e}"})
        finally:
            if lock:
                base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna Studio (web UI v11) - scan, region, design, surface check, program, verify. motion_ready=false")
    print(f"Open http://{base.HOST}:{base.PORT}   (classic tabs: http://{base.HOST}:{base.PORT}/classic)")
    print("Press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        v10.camera.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
