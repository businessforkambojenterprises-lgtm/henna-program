"""Studio v24 - machine profiles: each machine has its own name and its own calibration (user decision 2026-10-13).

- machines/registry.json: "Belt drive (first machine)" = current_calibration.json, unchanged; "Ball screw (new
  machine)" = not calibrated yet (henna_preview/machines.py). The active machine is chosen in Studio (Calibration ->
  Machine), only while disconnected, with a confirmation; there is no default.
- Studio's calibration (webui_server_v10.cal and the machine link's provider) is the ACTIVE machine's; a machine
  without a calibration refuses it, so scan position, scan, design check, program, run and the daily check are
  refused on it. Connect, Home, STOP, Pause and Resume always work. Jog: a machine without a measured work area
  may only jog Z up.
- Every new job records its machine; scan, design check, program and run are refused for a job of another machine
  (jobs from before v24 have no machine -> refused for those steps: make a new job).
- Each recording made by a v24 scan gets a <recording>.machine.json note; "use an existing recording" accepts only
  recordings of the active machine.
- Daily-check reference and results per machine (outputs/calibration/<machine>/); log rows name the machine.
v13-v23 files are unchanged. motion_ready stays false.
"""
import json
import os
import threading
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v23 as v23
from henna_preview.grbl_program_link import MachineError
from henna_preview.machines import MachineProfileError, Profiles

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v24.html")
profiles = Profiles(base.ROOT)
machine = v23.machine
_cal_dirs_lock = threading.Lock()

# ---- the active machine's calibration everywhere (every route looks v10.cal up at call time) ----
v10.cal = profiles.active_cal
machine._calibration = profiles.active_cal

# ---- every log row names the machine ----
_log_row = v10.log_row


def log_row(*args, **kwargs):
    mid = profiles.active_id() or "no machine chosen"
    args = list(args)
    if "notes" in kwargs:
        kwargs["notes"] = f"{kwargs['notes']}; machine {mid}" if kwargs["notes"] else f"machine {mid}"
    elif len(args) >= 10:
        args[9] = f"{args[9]}; machine {mid}" if args[9] else f"machine {mid}"
    else:
        kwargs["notes"] = f"machine {mid}"
    return _log_row(*args, **kwargs)


v10.log_row = log_row

# ---- route rules ----
NEEDS_CAL_AND_JOB = {"/api/machine/scan_position", "/api/studio/scan", "/api/studio/use_recording",
                     "/api/studio/check_design", "/api/studio/program", "/api/machine/run",
                     "/api/studio/offset", "/api/studio/skip_offset", "/api/studio/gapcheck"}
NEEDS_CAL = {"/api/cam/record", "/api/calibration/scan_position", "/api/calibration/check",
             "/api/calibration/reference"}
JOB_OF_THIS_MACHINE = {"/api/studio/detect", "/api/studio/region_check", "/api/studio/region_accept"}


def _job_machine_check(params, strict):
    job = v11.job_load(params.get("job"))
    active = profiles.active()
    jm = job.get("machine")
    if jm == active["id"]:
        return
    if jm is None and not strict:
        return
    where = profiles.get(jm)["name"] if jm else "an unknown machine (made before machine profiles)"
    raise MachineProfileError(f"This job was made on {where}; the active machine is the {active['name']}. "
                              "Start a new job on this machine.")


def guarded(path, fn):
    def run(params):
        if path in NEEDS_CAL_AND_JOB:
            _job_machine_check(params, strict=True)
            profiles.active_cal()
        elif path in NEEDS_CAL:
            profiles.active_cal()
        elif path in JOB_OF_THIS_MACHINE:
            _job_machine_check(params, strict=False)
        return fn(params)
    return run


def job_new(params):
    m = profiles.active()
    job = v11.job_new(params)
    job["machine"], job["machine_name"] = m["id"], m["name"]
    return v14.job_view(v11.job_save(job))


def jobs(q):
    out = []
    for j in v14.jobs_list():
        try:
            mj = v11.job_load(j["id"]).get("machine")
        except (OSError, ValueError):
            mj = None
        out.append(dict(j, machine=mj))
    return {"jobs": out}


def _note_path(recording):
    return recording + ".machine.json"


_SCAN = v23.Handler.POST_API["/api/studio/scan"][0]
_USE_REC = v23.Handler.POST_API["/api/studio/use_recording"][0]


def _scan_inner(params):
    r = _SCAN(params)
    rec = r["job"]["steps"]["scan"]["recording"]
    with open(_note_path(rec), "w", encoding="utf-8") as f:
        json.dump({"machine": profiles.active()["id"], "job": r["job"]["id"]}, f)
    return r


def use_recording(params):
    from webui_server_v2 import resolve_recording
    path = resolve_recording(params.get("path"))
    try:
        with open(_note_path(path), encoding="utf-8") as f:
            rm = json.load(f).get("machine")
    except (OSError, ValueError):
        rm = None
    active = profiles.active()
    if rm != active["id"]:
        raise MachineProfileError(f"This recording was not made on the {active['name']} with Studio v24 - "
                                  "record a new scan on this machine")
    return _USE_REC(params)


def jog(params):
    m = profiles.active()
    if not m.get("work_area_mm"):
        if params.get("axis") != "Z" or float(params.get("direction", 0)) <= 0:
            raise MachineProfileError(f"The {m['name']}'s work area is not measured yet - only Z up may be jogged")
    if profiles.calibrated(m):
        return _JOG(params)
    # Not calibrated: the link's pre-jog check reads the calibration only to confirm its own fixed values (scan pose,
    # travel Z, feeds); the jog itself uses the link's fixed limits (Z -20 or higher, inside its box). So the check
    # gets exactly those fixed values - for this one jog only. Scan position, scans, programs and runs stay refused
    # at the routes above.
    with _jog_cfg_lock:
        saved = machine._calibration
        machine._calibration = lambda: dict(JOG_ONLY_CFG)
        try:
            return _JOG(params)
        finally:
            machine._calibration = saved


_JOG = v23.Handler.POST_API["/api/machine/jog"][0]
_jog_cfg_lock = threading.Lock()
from henna_preview.grbl_scan_link import POSE as _POSE, TRAVEL_Z as _TRAVEL_Z  # noqa: E402
JOG_ONLY_CFG = {"capture_pose_mpos": list(_POSE), "travel_z": _TRAVEL_Z, "draw_feed_mm_min": 300.0,
                "travel_feed_mm_min": 1000.0, "note": "jog-only values of the machine link, not a calibration"}


def machines_list(_=None):
    s = profiles.summary()
    st = machine.status()
    s["link"] = {"connected": st.get("connected"), "busy": st.get("busy")}
    return s


def machines_select(params):
    if params.get("confirm_machine") is not True:
        raise ValueError("Confirm which machine this computer is connected to")
    st = machine.status()
    if st.get("connected") or st.get("busy"):
        raise MachineError("Disconnect the machine before choosing another one")
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Wait for the recording to finish")
    m = profiles.select(params.get("id"))
    v10.log_row(v10.LOG_FILE, 0, "Machine chosen", None, "Studio Machine", "the machine this computer drives",
                f"{m['name']} ({m['id']}); calibrated: {profiles.calibrated(m)}", "recorded")
    return machines_list()


# ---- daily check per machine: v23's functions with the active machine's folders ----
def _per_machine(fn):
    def run(params=None):
        m = profiles.active()
        with _cal_dirs_lock:
            v23.REF_DIR = os.path.join(base.ROOT, "outputs", "calibration", m["id"], "reference")
            v23.CHECK_DIR = os.path.join(base.ROOT, "outputs", "calibration", m["id"], "checks")
            return fn(params) if params is not None else fn()
    return run


def cal_status(q=None):
    m = profiles.active()
    if not profiles.calibrated(m):
        return {"machine": {"id": m["id"], "name": m["name"]}, "not_calibrated": True, "ok": False, "items": [],
                "reference": None, "checks": [], "limits": v23.daily_check.LIMITS}
    s = _per_machine(v23.cal_status)()
    s["machine"] = {"id": m["id"], "name": m["name"]}
    return s


class Handler(v23.Handler):
    server_version = "HennaStudio/0.24"
    GET_API = dict(v23.Handler.GET_API, **{"/api/studio/jobs": jobs, "/api/machines": machines_list,
                                           "/api/calibration/status": cal_status})
    POST_API = {p: (guarded(p, fn), lock) for p, (fn, lock) in v23.Handler.POST_API.items()}
    POST_API.update({
        "/api/studio/job_new": (job_new, False),
        "/api/studio/scan": (guarded("/api/studio/scan", _scan_inner), True),
        "/api/studio/use_recording": (guarded("/api/studio/use_recording", use_recording), True),
        "/api/machine/jog": (jog, False),
        "/api/machines/select": (machines_select, False),
        "/api/calibration/check": (guarded("/api/calibration/check", _per_machine(v23.cal_check)), True),
        "/api/calibration/reference": (guarded("/api/calibration/reference", _per_machine(v23.cal_reference)), True),
    })
    ERRORS = v23.Handler.ERRORS + (MachineProfileError,)

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    m = profiles.active_id()
    print(f"Henna Studio v24 - machine profiles (active: {m or 'none chosen'}). motion_ready=false", flush=True)
    print(f"Open http://{base.HOST}:{base.PORT}; press Ctrl+C to stop (a running program is stopped)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if machine.status().get("busy") in v14.MOVING:
                machine.stop()
        except MachineError:
            pass
        finally:
            try:
                srv.server_close()
            finally:
                try:
                    if machine.status().get("busy") in v14.MOVING:
                        machine.stop()
                except MachineError:
                    pass
                finally:
                    try:
                        machine.disconnect()
                    finally:
                        v10.camera.stop()


if __name__ == "__main__":
    main()
