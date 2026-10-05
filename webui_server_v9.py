"""Local web UI, revision 9: adds '9 Pencil practice toolpath' (simulator only).

Run:   .venv\\Scripts\\python webui_server_v9.py      Open: http://127.0.0.1:8765
Revisions 1–8 are kept unchanged. Practice parameters come only from
practice_params.json (the user's decisions). Output: PENCIL PRACTICE - NOT
VALIDATED - DO NOT SEND. No machine connection. motion_ready=false.
"""
import datetime as dt
import json
import os
import re
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image

import webui_server as base
import webui_server_v6 as v6
import webui_server_v7 as v7
import webui_server_v8 as v8
import step10_practice_toolpath as s10
from henna_preview.calibration import CalibrationError
from henna_preview.design import DesignError
from henna_preview.practice_toolpath import PracticeError
from henna_preview.recording import RecordingError, read_frame
from step05_region import holes_png_data_url, png_data_url

INDEX_V9 = os.path.join(base.WEB_DIR, "index_v9.html")
NAME = re.compile(r"^[0-9A-Za-z_\-]{1,80}$")
_frames = {}


def calib_and_frame(path):
    calib = v7.load_calibration(path)
    key = os.path.abspath(path)
    if key not in _frames:
        _frames.clear()
        _frames[key] = read_frame(calib["inputs"]["recording"], calib["inputs"]["frame_index"])
    return calib, _frames[key]


def setup(q):
    path = q.get("calib", [""])[0]
    calib, fr = calib_and_frame(path)
    ver = s10.find_verification(path)
    return {"calibration": path, "tool": calib.get("tool"), "head": calib["inputs"]["head_pose_at_capture"],
            "verified": ver is not None, "verification_rms_mm": ver[1]["result"]["rms_mm"] if ver else None,
            "params": s10.load_params(), "width": fr["colour_rgb"].shape[1], "height": fr["colour_rgb"].shape[0],
            "colour": png_data_url(Image.fromarray(fr["colour_rgb"])), "holes": holes_png_data_url(fr["depth_raw_aligned"])}


def design_of(params):
    d = params.get("design") or {}
    try:
        return {"shape": d["shape"], "size_mm": float(d["size_mm"]), "centre_px": [float(c) for c in d["centre_px"]],
                "rotation_deg": float(d.get("rotation_deg", 0)), "jump_mm": float(d.get("jump_mm", 3.0))}
    except (KeyError, TypeError, ValueError):
        raise ValueError("design needs shape, size_mm, centre_px [u, v], rotation_deg, jump_mm")


def preview(params):
    calib, fr = calib_and_frame(params.get("calibration"))
    out = s10.compute(params["calibration"], calib, fr, design_of(params), s10.load_params())
    return {"samples_px": out["samples_px"], "status": out["status"],
            "segments_px": [s["pixels_uv"] for s in out["segments"]], "projection": out["projection_stats"],
            "moves": out["moves"], "lines": len(out["gcode_lines"]), "gcode_head": out["gcode_lines"][:30],
            "verification": out["verification"]}


def save(params):
    calib, fr = calib_and_frame(params.get("calibration"))
    design, p = design_of(params), s10.load_params()
    out = s10.compute(params["calibration"], calib, fr, design, p)
    d = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-practice")
    rep = s10.save(d, params["calibration"], calib, fr, design, p, out)
    name = os.path.basename(d)
    return {"report": rep, "output_folder": d, "overlay": f"/outputs/{name}/overlay.png",
            "download": f"/download_practice/{name}/practice_gcode.txt"}


class Handler(v8.Handler):
    server_version = "HennaPreview/0.9"

    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/index_v9.html"):
            return self._file(INDEX_V9, "text/html; charset=utf-8")
        if u.path == "/api/practice_setup":
            try:
                return self._send(200, setup(parse_qs(u.query)))
            except (ValueError, PracticeError, RecordingError, KeyError) as e:
                return self._send(400, {"error": str(e)})
        if u.path.startswith("/download_practice/"):
            parts = unquote(u.path[len("/download_practice/"):]).split("/")
            if len(parts) == 2 and NAME.match(parts[0]) and parts[1] == "practice_gcode.txt":
                p = os.path.join(base.OUT_ROOT, *parts)
                if base.is_inside(p, base.OUT_ROOT) and os.path.isfile(p):
                    data = open(p, "rb").read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Content-Disposition", f'attachment; filename="PENCIL_PRACTICE_DO_NOT_SEND_{parts[0]}.txt"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            return self._send(404, {"error": "not found"})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        handlers = {"/api/practice_preview": (preview, False), "/api/practice_save": (save, True)}
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
            return self._send(409, {"error": "a run is already in progress"})
        try:
            self._send(200, fn(params))
        except (ValueError, PracticeError, DesignError, CalibrationError, RecordingError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            if lock:
                base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna preview web UI v9 (preview only, motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
