"""Studio v23 - Calibration section (status + daily check). Same as v22 otherwise (v13-v22 files unchanged).

1. Calibration status (read-only): the approved calibration in use (X/Y, Z, depth scale), its key numbers, age,
   and the daily-check reference with the camera serial it belongs to (henna_preview/calibration_status.py).
2. Daily check (camera only; henna_preview/daily_check.py): bed EMPTY, machine at the fixed scan position
   (the same approved route as v13, here without a job), 3 s recording through the same capture guard and
   recorder as a scan, compared with a reference: height, tilt, sideways shift, same camera -> OK / CHECK /
   RECALIBRATE / UNCLEAR. Setting the reference needs its own confirmation. Each check is logged.
Nothing here changes current_calibration.json or any calibration file. motion_ready stays false.
"""
import datetime as dt
import json
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v14 as v14
import webui_server_v22 as v22
from henna_preview import calibration_status, daily_check
from henna_preview.grbl_program_link import MachineError

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v23.html")
CAL_ROOT = os.path.join(base.ROOT, "outputs", "calibration")
REF_DIR = os.path.join(CAL_ROOT, "reference")
CHECK_DIR = os.path.join(CAL_ROOT, "checks")
machine = v22.machine


def _recent_checks(n=5):
    if not os.path.isdir(CHECK_DIR):
        return []
    out = []
    for d in sorted(os.listdir(CHECK_DIR), reverse=True):
        p = os.path.join(CHECK_DIR, d, "result.json")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                r = json.load(f)
            out.append({k: r.get(k) for k in ("time", "verdict", "height_mm", "tilt_deg", "sideways_mm", "changed_pct",
                                              "same_camera", "reasons")})
            if len(out) >= n:
                break
    return out


def cal_status(_=None):
    st = calibration_status.status(base.ROOT, v10.cal())
    ref = daily_check.load_reference(REF_DIR, with_arrays=False)
    st["reference"] = ({k: ref.get(k) for k in ("created", "serial", "median_depth_mm", "mpos", "confirmed")}
                       if ref else None)
    st["checks"] = _recent_checks()
    st["limits"] = daily_check.LIMITS
    return st


def _confirm(params, *keys):
    for k in keys:
        if params.get(k) is not True:
            raise ValueError({"confirm_clear": "Confirm that all hands and objects are clear of the travel area",
                              "confirm_estop": "Confirm that the emergency stop is within reach",
                              "confirm_empty": "Confirm that the bed is empty (no hand) and as at the reference",
                              "confirm_reference": "Confirm that the calibration is known good now"}[k])


def cal_scan_position(params):
    """The approved fixed scan-position route (v13), for the calibration check: no job, same confirmations."""
    _confirm(params, "confirm_clear", "confirm_estop")
    if v10.camera.status()["state"] == "recording":
        raise MachineError("Finish recording before moving to scan position")
    return machine.start_scan_position()


def _record(stamp):
    if v10.shutil.disk_usage(base.ROOT).free < v10.MIN_FREE_BYTES:
        raise ValueError("less than 2 GB free - make room before a calibration check")
    d = os.path.join(CHECK_DIR, stamp)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "check.db3")
    st = None
    with machine.capture_guard() as line:            # fresh, homed, idle at the verified pose - for the whole recording
        st = v10.parse_status(line)
        v10.check_pose(st["mpos"], tuple(v10.cal()["capture_pose_mpos"]))
        v10.camera.record(path, 3.0)
    return d, path, st


def cal_check(params):
    _confirm(params, "confirm_empty")
    ref = daily_check.load_reference(REF_DIR)
    if ref is None:
        raise ValueError("No reference yet: set the reference once, right after a known-good calibration")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d, path, st = _record(stamp)
    try:
        cur = daily_check.analyse_recording(path, v10.cal())
        res = daily_check.compare(ref, cur)
    finally:
        if os.path.exists(path):
            os.remove(path)                           # 170 MB a day; the result below is what is kept
    res.update(time=dt.datetime.now().isoformat(timespec="seconds"), mpos=st["mpos"], reference=ref.get("created"))
    with open(os.path.join(d, "result.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    v10.log_row(v10.LOG_FILE, 0, "Calibration daily check", st["mpos"], "Studio Calibration check 3 s",
                "camera unchanged vs reference",
                f"height {res.get('height_mm')} mm; tilt {res.get('tilt_deg')} deg; sideways {res.get('sideways_mm')} mm; "
                f"bed changed {res.get('changed_pct')} %; same camera {res.get('same_camera')}",
                res["verdict"], "", "; ".join(res.get("reasons", [])))
    return res


def cal_reference(params):
    _confirm(params, "confirm_empty", "confirm_reference")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    d, path, st = _record(stamp)
    try:
        cur = daily_check.analyse_recording(path, v10.cal())
        meta = daily_check.save_reference(REF_DIR, cur, {"created": dt.datetime.now().isoformat(timespec="seconds"),
                                                         "mpos": st["mpos"], "confirmed": "bed empty + calibration known good",
                                                         "calibration": v10.cal().get("decided_by")}, recording=path)
    finally:
        if os.path.exists(path):                      # failed before the move: do not leave 170 MB behind
            os.remove(path)
        if os.path.isdir(d) and not os.listdir(d):
            os.rmdir(d)
    v10.log_row(v10.LOG_FILE, 0, "Calibration reference set", st["mpos"], "Studio Calibration set reference 3 s",
                "bed empty, calibration known good", f"camera {meta.get('serial')}; median depth {meta.get('median_depth_mm')} mm",
                "recorded", "reference.db3", "daily-check reference (old one kept under outputs/calibration/reference/old)")
    return {"reference": {k: meta.get(k) for k in ("created", "serial", "median_depth_mm", "mpos", "confirmed")}}


class Handler(v22.Handler):
    server_version = "HennaStudio/0.23"
    GET_API = dict(v22.Handler.GET_API, **{"/api/calibration/status": cal_status})
    POST_API = dict(v22.Handler.POST_API, **{"/api/calibration/scan_position": (cal_scan_position, True),
                                            "/api/calibration/check": (cal_check, True),
                                            "/api/calibration/reference": (cal_reference, True)})

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    print("Henna Studio v23 - calibration status + daily check. motion_ready=false", flush=True)
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
