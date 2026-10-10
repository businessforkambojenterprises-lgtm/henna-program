"""Studio v13: confirmed fixed scan positioning and automatic capture-pose readings.

New revision; v12 and all calibrated processing scripts remain unchanged.
The user operates Connect, Home, Move to scan position and Record in the UI.
Programs and other manual movements remain in UGS. motion_ready remains false.
"""
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v12 as v12
from henna_preview.grbl_scan_link import GrblScanLink, MachineError

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v13.html")


def log_event(event):
    """Persist commanded stages and their observed positions, never every status poll."""
    v10.log_row(
        v10.LOG_FILE, 0, "Studio machine: " + event.get("event", "event"), event.get("mpos"),
        event.get("command", ""), event.get("stage", ""), event.get("line", ""),
        event.get("error") or "recorded", "", event.get("detail", "fixed scan positioning"),
    )


machine = GrblScanLink(calibration_provider=v10.cal, on_event=log_event)


def machine_status(_=None):
    return dict(machine.status(), default_port=v12.default_port())


def connect(params):
    port = params.get("port") or v12.default_port()
    if port not in [p["device"] for p in v12.ports()]:
        raise MachineError(f"{port} does not exist on this PC")
    return machine.connect(port)


def confirmed_clear(params):
    if params.get("confirm_clear") is not True or params.get("confirm_estop") is not True:
        raise ValueError("Confirm that all hands and objects are clear of the travel area and the emergency stop is within reach")


def home(params):
    confirmed_clear(params)
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before homing")
    return machine.start_homing()


def scan_position(params):
    # Validate the job and confirmations before anything can reach the controller.
    job = v11.job_load(params.get("job"))
    if job["subject"] not in v11.SUBJECTS:
        raise ValueError("Unknown job subject")
    confirmed_clear(params)
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before moving to scan position")
    return machine.start_scan_position()


def scan(params):
    job = v11.job_load(params.get("job"))
    r = legacy_scan(params)
    v = r["verification"]
    # Publication happens only after both camera verification and the machine
    # guard's final position check have succeeded.
    v11.set_step(job, "scan", {"recording": r["recording"], "name": r["name"],
        "status_line": r["status_line"], "mpos": r["mpos"],
        "frame_pairs": v["frame_pairs"], "checks": {
            "depth_units_m": v["depth_units_m"], "camera_settings": v["camera_settings"],
            "median_depth_mm": v["median_depth_mm"]}, "source": "studio scan"})
    return {"job": job, "verification": v}


def legacy_scan(params):
    seconds = v10.number(params, "seconds", 3, 10, 5)
    folder = v10.v2.APPROVED_RECORDING_DIRS[0]
    if v10.shutil.disk_usage(folder).free < v10.MIN_FREE_BYTES:
        raise ValueError("less than 2 GB free for recordings - move old recordings to D: first")
    name = v10.dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".db3"
    path = os.path.join(folder, name)
    if os.path.exists(path):
        raise v10.CameraError("a recording with this name already exists")
    st = None
    try:
        # Ignore browser-supplied status. Monitor the controller throughout
        # capture, and finish the guard BEFORE publishing any accepted scan.
        with machine.capture_guard() as line:
            st = v10.parse_status(line)
            v10.check_pose(st["mpos"], tuple(v10.cal()["capture_pose_mpos"]))
            info = v10.camera.record(path, seconds)
    except Exception as exc:
        if os.path.exists(path):
            os.replace(path, path + ".rejected")
        v10.log_row(v10.LOG_FILE, 0, "Web scan refused", st["mpos"] if st else None,
                    "Studio Record", "usable recording and fixed head", str(exc), "REFUSED", name)
        raise
    d = v10.new_dir("scan")
    rec = {"schema": "web_scan/1", "created": v10.dt.datetime.now().isoformat(timespec="seconds"),
           "recording": path, "head_status_line": st["line"], "head_mpos": st["mpos"],
           "seconds": seconds, "verification": info, "motion_ready": v10.MOTION_READY}
    v10.jsave(os.path.join(d, "scan.json"), rec)
    v10.log_row(v10.LOG_FILE, 0, "Web scan (pose 1)", st["mpos"], f"Studio Record {seconds:g} s",
                "usable recording and fixed head", f"{info['frame_pairs']} frame pairs; depth units "
                f"{info['depth_units_m']:g}; median depth {info['median_depth_mm']} mm", "recorded", name)
    return {"recording": path, "name": name, "scan": os.path.basename(d), "verification": info,
            "status_line": st["line"], "mpos": st["mpos"]}


class Handler(v12.Handler):
    server_version = "HennaStudio/0.13"
    GET_API = dict(v12.Handler.GET_API, **{
        "/api/machine/status": machine_status,
        "/api/machine/status_line": lambda q: {"line": machine.status_line()},
    })
    POST_API = dict(v12.Handler.POST_API, **{
        "/api/machine/connect": (connect, False),
        "/api/machine/disconnect": (lambda p: machine.disconnect(), False),
        "/api/machine/home": (home, False),
        "/api/machine/stop": (lambda p: machine.stop(), False),
        "/api/machine/scan_position": (scan_position, True),
        "/api/studio/scan": (scan, True),
        "/api/cam/record": (legacy_scan, True),
    })
    ERRORS = v12.Handler.ERRORS + (MachineError,)

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    # Let active recording requests finish verification/quarantine before the
    # serial link and camera are released during shutdown.
    srv.daemon_threads = False
    print("Henna Studio v13 - confirmed scan positioning. motion_ready=false", flush=True)
    print(f"Open http://{base.HOST}:{base.PORT}; press Ctrl+C to stop", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            # Stop an existing move promptly, before waiting for HTTP requests.
            if machine.status().get("busy") in ("homing", "scan_position"):
                machine.stop()
        finally:
            try:
                # Close the listener and join active requests, including a scan
                # that still owns capture_guard while its file is verified.
                srv.server_close()
            finally:
                try:
                    # An already accepted handler may have started a move while
                    # the requests were draining. Stop that move as well.
                    if machine.status().get("busy") in ("homing", "scan_position"):
                        machine.stop()
                finally:
                    try:
                        machine.disconnect()
                    finally:
                        v10.camera.stop()


if __name__ == "__main__":
    main()
