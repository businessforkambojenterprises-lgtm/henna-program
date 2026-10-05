"""Local web UI for the henna preview prototype.

Run:   python webui_server.py
Open:  http://127.0.0.1:8765

Preview only. Bound to 127.0.0.1 so no other computer can reach it.
It never touches the camera, the motion controller or serial ports,
never writes G-code, and only reads input files from APPROVED_DIRS.
Results are written under outputs/webui/<run id>/.
"""
import datetime as dt
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse, unquote

import numpy as np

HOST = "127.0.0.1"
PORT = 8765
ROOT = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(ROOT, "webui")
OUT_ROOT = os.path.join(ROOT, "outputs", "webui")
STEP02 = os.path.join(ROOT, "step02_inspect_depth.py")

# Folders the user has approved for reading. Old-PC data is deliberately not listed.
APPROVED_DIRS = [os.path.join(os.path.expanduser("~"), "palm_3d_scans")]
ALLOWED_EXT = (".npz",)
REQUIRED_ARRAYS = ("median", "intr")
MAX_POINTS_3D = 30000
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}

_run_lock = threading.Lock()


def _real(p):
    return os.path.normcase(os.path.realpath(p))


def is_inside(path, folder):
    p, f = _real(path), _real(folder)
    try:
        return os.path.commonpath([p, f]) == f
    except ValueError:  # e.g. different drives
        return False


def resolve_input(path, approved=None):
    """Return the real path if it is an existing .npz inside an approved folder, else raise ValueError."""
    approved = APPROVED_DIRS if approved is None else approved
    if not isinstance(path, str) or not path:
        raise ValueError("no file given")
    if not path.lower().endswith(ALLOWED_EXT):
        raise ValueError("only .npz files are accepted")
    if not any(is_inside(path, d) for d in approved):
        raise ValueError("file is outside the approved folders")
    if not os.path.isfile(path):
        raise ValueError("file not found")
    return os.path.realpath(path)


def check_compatible(path):
    """Step 2 needs a depth 'median' array and 'intr' intrinsics. Returns (ok, reason)."""
    try:
        with np.load(path, allow_pickle=False) as z:
            missing = [k for k in REQUIRED_ARRAYS if k not in z.files]
    except Exception as e:  # unreadable file
        return False, f"cannot read: {e}"
    if missing:
        return False, "missing " + ", ".join(missing)
    return True, ""


def list_inputs(approved=None):
    approved = APPROVED_DIRS if approved is None else approved
    items = []
    for d in approved:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if not (name.lower().endswith(ALLOWED_EXT) and os.path.isfile(p)):
                continue
            st = os.stat(p)
            ok, reason = check_compatible(p)
            items.append({
                "path": p, "name": name, "folder": d, "size": st.st_size,
                "modified": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "compatible": ok, "reason": reason,
            })
    return items


def validate_params(params):
    src = resolve_input(params.get("path"))
    units = params.get("depth_units")
    if units not in ("mm", "raw"):
        raise ValueError("depth_units must be 'mm' or 'raw'")
    try:
        mvf = float(params.get("min_valid_frac", 0.6))
        mad = float(params.get("max_mad_mm", 1.0))
    except (TypeError, ValueError):
        raise ValueError("thresholds must be numbers")
    if not 0.0 <= mvf <= 1.0:
        raise ValueError("min_valid_frac must be between 0 and 1")
    if not 0.0 < mad <= 100.0:
        raise ValueError("max_mad_mm must be between 0 and 100")
    return src, units, bool(params.get("units_confirmed")), mvf, mad


def run_step02(params):
    src, units, confirmed, mvf, mad = validate_params(params)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = os.path.join(OUT_ROOT, run_id)
    n = 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(OUT_ROOT, f"{run_id}-{n}")
    run_id = os.path.basename(out)

    cmd = [sys.executable, STEP02, "--input", src, "--depth-units", units,
           "--min-valid-frac", str(mvf), "--max-mad-mm", str(mad), "--out", out]
    if confirmed:
        cmd.append("--units-confirmed")
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    wall = time.perf_counter() - t0
    if proc.returncode != 0:
        raise RuntimeError("step 2 failed:\n" + proc.stderr[-3000:])

    with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
        report = json.load(f)
    with np.load(os.path.join(out, "points_camera_mm.npz")) as z:
        pts = z["xyz_mm"][z["status"] == 0]
    every = max(1, len(pts) // MAX_POINTS_3D)
    sub = pts[::every]
    return {
        "run_id": run_id,
        "output_folder": out,
        "report": report,
        "images": {name: f"/outputs/{run_id}/{name}.png" for name in ("depth_status", "points_3d")},
        "points": {"xyz_mm": np.round(sub, 1).ravel().tolist(), "every_nth": every, "total": int(len(pts))},
        "wall_time_s": round(wall, 3),
    }


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "HennaPreview/0.1"

    def _host_ok(self):
        if self.headers.get("Host") in ALLOWED_HOSTS:
            return True
        self._send(403, {"error": "unexpected Host header"})
        return False

    def _send(self, code, body, ctype="application/json"):
        data = json.dumps(body).encode() if ctype == "application/json" else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _file(self, path, ctype):
        with open(path, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_GET(self):
        if not self._host_ok():
            return
        url = urlparse(self.path).path
        if url in ("/", "/index.html"):
            return self._file(os.path.join(WEB_DIR, "index.html"), "text/html; charset=utf-8")
        if url == "/api/files":
            return self._send(200, {"approved_dirs": APPROVED_DIRS, "files": list_inputs()})
        if url.startswith("/outputs/"):
            p = os.path.join(OUT_ROOT, *unquote(url[len("/outputs/"):]).split("/"))
            if is_inside(p, OUT_ROOT) and os.path.isfile(p) and p.lower().endswith(".png"):
                return self._file(p, "image/png")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        if not self._host_ok():
            return
        if urlparse(self.path).path != "/api/run":
            return self._send(404, {"error": "not found"})
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "send JSON"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            params = json.loads(self.rfile.read(min(length, 10000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad JSON"})
        if not _run_lock.acquire(blocking=False):
            return self._send(409, {"error": "a run is already in progress"})
        try:
            self._send(200, run_step02(params))
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            _run_lock.release()

    def log_message(self, fmt, *args):
        sys.stderr.write("[webui] " + fmt % args + "\n")


def main():
    srv = http.server.ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"Henna preview web UI (preview only, motion_ready=false)")
    print(f"Open http://{HOST}:{PORT}  -  press Ctrl+C to stop")
    print("Approved input folders:", *APPROVED_DIRS, sep="\n  ")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
