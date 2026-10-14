"""Studio v25 - per-machine motion values + calibration mode (user approval 2026-10-13). v13-v24 files unchanged.

1. Each machine's scan position, travel Z, Z floor and work area come from machines/registry.json ("motion"); the
   machine link uses the ACTIVE machine's values (henna_preview/machine_motion.py), set at start and when the
   machine is switched (only while disconnected). Belt drive: X145.8 Y42.6 Z-40, travel Z -20, floor -97,
   225 x 176 - exactly the values fixed in the code until v24 (and checked against current_calibration.json).
   Ball screw: X80 Y80 Z-20, travel Z -20, floor -76 (4 mm above its bed at about Z-80), 225 x 176 provisional.
2. Calibration mode (a machine without an approved calibration but with motion values - the ball screw now):
   Move to scan position and Record scan are allowed (needed to calibrate); design check, programs, runs and the
   daily check stay refused until its calibration is approved. Connect, Home, Jog, STOP always as before.
motion_ready stays false.
"""
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v14 as v14
import webui_server_v24 as v24
from henna_preview import machine_motion
from henna_preview.grbl_program_link import MachineError
from henna_preview.machines import MachineProfileError

v23 = v24.v23
STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v25.html")
profiles = v24.profiles
machine = v24.machine


def apply_active():
    """Set the machine link's motion values from the active machine (call only while disconnected)."""
    mid = profiles.active_id()
    if mid is None:
        machine_motion.apply(machine_motion.BELT_DRIVE)          # nothing can move without a chosen machine anyway
        return None
    m = profiles.get(mid)
    profiles.check_consistent(m)
    try:
        mo = profiles.motion(m)
    except MachineProfileError:
        machine_motion.apply(machine_motion.BELT_DRIVE)
        return None
    vals = machine_motion.values_for(mo, m["work_area_mm"])
    machine_motion.apply(vals)
    return vals


def cal():
    """Studio's calibration (replaces v10.cal): the approved file, or in calibration mode only the motion values."""
    m = profiles.active()
    if profiles.calibrated(m):
        return profiles.cal_of(m)
    if profiles.calibration_mode(m):
        return profiles.motion_cfg(m)
    return profiles.cal_of(m)                                    # raises: not calibrated


def link_cfg():
    """What the machine link checks before each move: the active machine's own motion values."""
    return profiles.motion_cfg(profiles.active())


v10.cal = cal
machine._calibration = link_cfg

FULL_CAL_JOB = {"/api/studio/check_design", "/api/studio/program", "/api/machine/run", "/api/studio/offset",
                "/api/studio/skip_offset", "/api/studio/gapcheck"}
SCAN_JOB = {"/api/machine/scan_position", "/api/studio/scan", "/api/studio/use_recording"}
SCAN_ONLY = {"/api/cam/record", "/api/calibration/scan_position"}
FULL_CAL_ONLY = {"/api/calibration/check", "/api/calibration/reference"}
JOB_OF_THIS_MACHINE = {"/api/studio/detect", "/api/studio/region_check", "/api/studio/region_accept"}


def scan_allowed():
    m = profiles.active()
    if profiles.calibrated(m) or profiles.calibration_mode(m):
        profiles.motion(m)
        return
    profiles.cal_of(m)                                            # raises: not calibrated


def guarded(path, fn):
    def run(params):
        if path in FULL_CAL_JOB:
            v24._job_machine_check(params, strict=True)
            profiles.active_cal()
        elif path in SCAN_JOB:
            v24._job_machine_check(params, strict=True)
            scan_allowed()
        elif path in SCAN_ONLY:
            scan_allowed()
        elif path in FULL_CAL_ONLY:
            profiles.active_cal()
        elif path in JOB_OF_THIS_MACHINE:
            v24._job_machine_check(params, strict=False)
        return fn(params)
    return run


def jog(params):
    m = profiles.active()
    if not m.get("work_area_mm"):
        if params.get("axis") != "Z" or float(params.get("direction", 0)) <= 0:
            raise MachineProfileError(f"The {m['name']}'s work area is not measured yet - only Z up may be jogged")
    return v24._JOG(params)


def machines_list(_=None):
    s = v24.machines_list()
    vals = machine_motion.current()
    s["motion_in_use"] = {"scan_pose": list(vals["POSE"]), "travel_z": vals["TRAVEL_Z"],
                          "work_area": [vals["X_MAX"], vals["Y_MAX"]]}
    return s


def machines_select(params):
    v24.machines_select(params)                                    # disconnected + tick, logs the choice
    apply_active()
    return machines_list()


def cal_status(q=None):
    s = v24.cal_status(q)
    m = profiles.active()
    s["calibration_mode"] = profiles.calibration_mode(m)
    s["scan_pose"] = (m.get("motion") or {}).get("scan_pose_mpos")
    return s


_ORIG = v23.Handler.POST_API


class Handler(v24.Handler):
    server_version = "HennaStudio/0.25"
    GET_API = dict(v24.Handler.GET_API, **{"/api/machines": machines_list, "/api/calibration/status": cal_status})
    POST_API = {p: (guarded(p, fn), lock) for p, (fn, lock) in _ORIG.items()}
    POST_API.update({
        "/api/studio/job_new": (v24.job_new, False),
        "/api/studio/scan": (guarded("/api/studio/scan", v24._scan_inner), True),
        "/api/studio/use_recording": (guarded("/api/studio/use_recording", v24.use_recording), True),
        "/api/machine/jog": (jog, False),
        "/api/machines/select": (machines_select, False),
        "/api/calibration/check": (guarded("/api/calibration/check", v24._per_machine(v23.cal_check)), True),
        "/api/calibration/reference": (guarded("/api/calibration/reference", v24._per_machine(v23.cal_reference)), True),
    })

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    vals = apply_active()
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    mid = profiles.active_id()
    print(f"Henna Studio v25 - per-machine motion + calibration mode (active: {mid or 'none chosen'}"
          f"{'; scan position ' + str(list(vals['POSE'])) if vals else ''}). motion_ready=false", flush=True)
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
