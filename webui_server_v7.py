"""Local web UI, revision 7: adds calibration VERIFY to tab 8.

Run:   .venv\\Scripts\\python webui_server_v7.py
Open:  http://127.0.0.1:8765

Revisions 1–6 are kept unchanged. A saved calibration is checked at a different
head pose without re-fitting. No machine connection. motion_ready=false.
"""
import datetime as dt
import glob
import json
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v6 as v6
import step09_verify as s9
from henna_preview.calibration import CalibrationError
from henna_preview.recording import RecordingError
from henna_preview.verify import verify

INDEX_V7 = os.path.join(base.WEB_DIR, "index_v7.html")


def list_calibrations():
    out = []
    for p in sorted(glob.glob(os.path.join(base.OUT_ROOT, "*-calib*", "calibration.json")), reverse=True):
        with open(p, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("schema") != "camera_tool_xy_calibration/1":
            continue
        out.append({"path": p, "name": os.path.basename(os.path.dirname(p)), "tool": c.get("tool"),
                    "head": c["inputs"]["head_pose_at_capture"], "rms_mm": c["result"]["rms_mm"]})
    return out


def load_calibration(path):
    if not isinstance(path, str) or not base.is_inside(path, base.OUT_ROOT) or not os.path.isfile(path):
        raise ValueError("calibration must be a saved calibration in this project's outputs")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _inputs(params):
    calib = load_calibration(params.get("calibration"))
    targets = v6.resolve_targets(params.get("targets"))
    src, fr = v6.get_frame(params.get("recording"), params.get("frame"))
    return calib, targets, src, fr, v6._clicks(params)


def cal_verify(params):
    calib, targets, src, fr, clicks = _inputs(params)
    return verify(fr, targets, clicks, calib)


def cal_verify_save(params):
    calib, targets, src, fr, clicks = _inputs(params)
    result = verify(fr, targets, clicks, calib)
    out = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-verify")
    os.makedirs(out, exist_ok=True)
    rec = s9.build_record(params["calibration"], calib, src, params["frame"], fr, targets, params["targets"], clicks, result)
    with open(os.path.join(out, "verification.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    s9.render(fr["colour_rgb"], result).save(os.path.join(out, "verify_overlay.png"))
    return {"record": rec, "output_folder": out}


class Handler(v6.Handler):
    server_version = "HennaPreview/0.7"

    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/index_v7.html"):
            return self._file(INDEX_V7, "text/html; charset=utf-8")
        if u.path == "/api/calibrations":
            return self._send(200, {"calibrations": list_calibrations()})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        handlers = {"/api/cal_verify": cal_verify, "/api/cal_verify_save": cal_verify_save}
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
        if not base._run_lock.acquire(blocking=False):
            return self._send(409, {"error": "a run is already in progress"})
        try:
            self._send(200, handlers[path](params))
        except (ValueError, CalibrationError, RecordingError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna preview web UI v7 (preview only, motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
