"""Local web UI, revision 8: two safety fixes for tab 8.

1. Refuses to calibrate or verify when the targets file's head pose belongs to a
   different recording than the one loaded (prevents a head-pose mix-up).
2. The page (index_v8.html) refreshes the saved-calibration list after saving.

Run:   .venv\\Scripts\\python webui_server_v8.py      Open: http://127.0.0.1:8765
Revisions 1–7 are kept unchanged. No machine connection. motion_ready=false.
"""
import json
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v6 as v6
import webui_server_v7 as v7
from henna_preview.calibration import CalibrationError
from henna_preview.recording import RecordingError

INDEX_V8 = os.path.join(base.WEB_DIR, "index_v8.html")


def check_pairing(params):
    """The recording named in the targets file must be the recording being used."""
    targets = v6.resolve_targets(params.get("targets"))
    named = targets["head_pose_at_capture"].get("recording")
    used = os.path.basename(str(params.get("recording", "")))
    if not named:
        raise ValueError("targets file does not say which recording its head pose belongs to")
    if named.lower() != used.lower():
        raise ValueError(f"head pose mismatch: this targets file belongs to {named}, but {used} is loaded. "
                         "Choose the matching targets file or recording.")


GUARDED = {"/api/cal_solve": v6.cal_solve, "/api/cal_save": v6.cal_save,
           "/api/cal_verify": v7.cal_verify, "/api/cal_verify_save": v7.cal_verify_save}


class Handler(v7.Handler):
    server_version = "HennaPreview/0.8"

    def do_GET(self):
        if not self._host_ok():
            return
        if urlparse(self.path).path in ("/", "/index.html", "/index_v8.html"):
            return self._file(INDEX_V8, "text/html; charset=utf-8")
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in GUARDED:
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
            check_pairing(params)
            self._send(200, GUARDED[path](params))
        except (ValueError, CalibrationError, RecordingError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna preview web UI v8 (preview only, motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
