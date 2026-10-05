"""Local web UI, revision 6: adds '8 Camera–tool calibration' (X/Y, practice run with the pencil).

Run:   .venv\\Scripts\\python webui_server_v6.py
Open:  http://127.0.0.1:8765

Revisions 1–5 are kept unchanged. Recordings are read-only; targets files are
read only from the project's validation/ folder. Results go to
outputs/webui/<time>-calib/. No machine connection. motion_ready=false.
"""
import datetime as dt
import glob
import json
import os
from urllib.parse import parse_qs, urlparse

from PIL import Image

import webui_server as base
import webui_server_v2 as v2
import webui_server_v5 as v5
import step08_calibrate as s8
from henna_preview.calibration import CalibrationError
from henna_preview.recording import RecordingError, check_frame_index, frame_pair_count, read_frame
from step05_region import holes_png_data_url, png_data_url

INDEX_V6 = os.path.join(base.WEB_DIR, "index_v6.html")
VALIDATION_DIR = os.path.join(base.ROOT, "validation")
_frames = {}


def list_targets():
    out = []
    for p in sorted(glob.glob(os.path.join(VALIDATION_DIR, "*", "*targets*.json"))):
        try:
            t = s8.load_targets(p)
        except Exception:
            continue
        out.append({"path": p, "name": os.path.relpath(p, VALIDATION_DIR), "session_id": t.get("session_id"),
                    "tool": t.get("tool"), "labels": sorted(t["targets"]), "head": t["head_pose_at_capture"]})
    return out


def resolve_targets(path):
    if not isinstance(path, str) or not base.is_inside(path, VALIDATION_DIR) or not os.path.isfile(path):
        raise ValueError("targets file must be inside the project's validation folder")
    return s8.load_targets(path)


def get_frame(rec_path, frame):
    src = v2.resolve_recording(rec_path)
    n = frame_pair_count(src)
    try:
        check_frame_index(frame, n)
    except RecordingError as e:
        raise ValueError(str(e))
    key = (src, frame)
    if key not in _frames:
        if len(_frames) > 2:
            _frames.clear()
        _frames[key] = read_frame(src, frame)
    return src, _frames[key]


def cal_frame(q):
    rec = q.get("rec", [""])[0]
    try:
        frame = int(q.get("frame", ["-1"])[0])
    except ValueError:
        raise ValueError("frame must be a whole number")
    src, fr = get_frame(rec, frame)
    return {"recording": src, "frame": frame, "width": fr["colour_rgb"].shape[1], "height": fr["colour_rgb"].shape[0],
            "colour": png_data_url(Image.fromarray(fr["colour_rgb"])), "holes": holes_png_data_url(fr["depth_raw_aligned"])}


def _clicks(params):
    c = params.get("clicks")
    if not isinstance(c, dict) or not c:
        raise ValueError("click the dots first")
    try:
        return {str(k): [float(v[0]), float(v[1])] for k, v in c.items()}
    except (TypeError, ValueError, IndexError):
        raise ValueError("clicks must be {label: [u, v]}")


def cal_solve(params):
    targets = resolve_targets(params.get("targets"))
    src, fr = get_frame(params.get("recording"), params.get("frame"))
    return s8.calibrate(fr, targets, _clicks(params))


def cal_save(params):
    targets = resolve_targets(params.get("targets"))
    src, fr = get_frame(params.get("recording"), params.get("frame"))
    clicks = _clicks(params)
    result = s8.calibrate(fr, targets, clicks)
    out = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-calib")
    os.makedirs(out, exist_ok=True)
    rec = s8.build_record(src, params["frame"], fr, targets, params["targets"], clicks, result)
    with open(os.path.join(out, "calibration.json"), "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=2)
    s8.render(fr["colour_rgb"], result).save(os.path.join(out, "calib_overlay.png"))
    name = os.path.basename(out)
    return {"record": rec, "output_folder": out, "overlay": f"/outputs/{name}/calib_overlay.png"}


class Handler(v5.Handler):
    server_version = "HennaPreview/0.6"

    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/index_v6.html"):
            return self._file(INDEX_V6, "text/html; charset=utf-8")
        if u.path == "/api/cal_targets":
            return self._send(200, {"targets": list_targets()})
        if u.path == "/api/cal_frame":
            try:
                return self._send(200, cal_frame(parse_qs(u.query)))
            except (ValueError, RecordingError) as e:
                return self._send(400, {"error": str(e)})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        handlers = {"/api/cal_solve": cal_solve, "/api/cal_save": cal_save}
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
    print("Henna preview web UI v6 (preview only, motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
