"""Local web UI, revision 2: adds 'Load recording' + 'Hand detection'.

Run with the project environment (it has MediaPipe and pyrealsense2):
    .venv\\Scripts\\python webui_server_v2.py
Open http://127.0.0.1:8765

Revision 1 (webui_server.py) is kept unchanged; this file reuses its
safety checks (127.0.0.1 only, Host check, approved folders, JSON-only POST).
Recordings are played back from file only. No live camera, no controller,
no serial ports, no G-code. motion_ready is always false.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import time
from urllib.parse import urlparse

import webui_server as base
from henna_preview.recording import RECORDING_EXT, RecordingError, check_frame_index, frame_pair_count

STEP04 = os.path.join(base.ROOT, "step04_hand_detect.py")
INDEX_V2 = os.path.join(base.WEB_DIR, "index_v2.html")

# Folders approved for reading recordings (.bag / .db3 only are listed or accepted).
APPROVED_RECORDING_DIRS = [os.path.join(os.path.expanduser("~"), "Documents")]


def resolve_recording(path, approved=None):
    approved = APPROVED_RECORDING_DIRS if approved is None else approved
    if not isinstance(path, str) or not path:
        raise ValueError("no recording given")
    if not path.lower().endswith(RECORDING_EXT):
        raise ValueError("only .bag or .db3 recordings are accepted")
    if not any(base.is_inside(path, d) for d in approved):
        raise ValueError("recording is outside the approved folders")
    if not os.path.isfile(path):
        raise ValueError("recording not found")
    return os.path.realpath(path)


def list_recordings(approved=None):
    approved = APPROVED_RECORDING_DIRS if approved is None else approved
    items = []
    for d in approved:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            p = os.path.join(d, name)
            if not (name.lower().endswith(RECORDING_EXT) and os.path.isfile(p)):
                continue
            st = os.stat(p)
            try:
                count, reason = frame_pair_count(p), ""
            except Exception as e:
                count, reason = None, f"cannot read: {e}"
            items.append({"path": p, "name": name, "folder": d, "size": st.st_size,
                          "modified": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                          "frame_pairs": count, "usable": not reason and count != 0, "reason": reason})
    return items


def run_step04(params):
    src = resolve_recording(params.get("path"))
    count = frame_pair_count(src)
    frame = params.get("frame")
    try:
        check_frame_index(frame, count)
    except RecordingError as e:
        raise ValueError(str(e))
    try:
        conf = float(params.get("min_confidence", 0.5))
    except (TypeError, ValueError):
        raise ValueError("min_confidence must be a number")
    if not 0.0 < conf <= 1.0:
        raise ValueError("min_confidence must be between 0 and 1")

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-hand"
    out = os.path.join(base.OUT_ROOT, run_id)
    n = 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(base.OUT_ROOT, f"{run_id}-{n}")
    run_id = os.path.basename(out)

    cmd = [sys.executable, STEP04, "--input", src, "--frame", str(frame),
           "--min-confidence", str(conf), "--out", out]
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, cwd=base.ROOT, capture_output=True, text=True, timeout=600)
    wall = time.perf_counter() - t0
    if proc.returncode != 0:
        lines = [l for l in proc.stderr.splitlines() if l.strip()]
        raise RuntimeError("step 4 failed: " + (lines[-1] if lines else "unknown error"))
    with open(os.path.join(out, "report.json"), encoding="utf-8") as f:
        report = json.load(f)
    return {"run_id": run_id, "output_folder": out, "report": report,
            "images": {name: f"/outputs/{run_id}/{name}.png" for name in ("overlay", "colour")},
            "wall_time_s": round(wall, 3)}


class Handler(base.Handler):
    server_version = "HennaPreview/0.2"

    def do_GET(self):
        if not self._host_ok():
            return
        url = urlparse(self.path).path
        if url in ("/", "/index.html", "/index_v2.html"):
            return self._file(INDEX_V2, "text/html; charset=utf-8")
        if url == "/api/recordings":
            return self._send(200, {"approved_dirs": APPROVED_RECORDING_DIRS, "recordings": list_recordings()})
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/run_step04":
            return super().do_POST()
        if not self._host_ok():
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "send JSON"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            params = json.loads(self.rfile.read(min(length, 10000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad JSON"})
        if not base._run_lock.acquire(blocking=False):
            return self._send(409, {"error": "a run is already in progress"})
        try:
            self._send(200, run_step04(params))
        except ValueError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna preview web UI v2 (preview only, motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    print("Approved depth-file folders:", *base.APPROVED_DIRS, sep="\n  ")
    print("Approved recording folders (.bag/.db3 only):", *APPROVED_RECORDING_DIRS, sep="\n  ")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
