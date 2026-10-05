"""Local web UI, revision 5: adds '7 Report'.

Run:   .venv\\Scripts\\python webui_server_v5.py
Open:  http://127.0.0.1:8765

Revisions 1–4 are kept unchanged. The final report re-checks the whole chain
(recording checksum, accepted region, approved model) before writing.
No camera, controller, serial ports or G-code. motion_ready is always false.
"""
import datetime as dt
import json
import os
import re
from urllib.parse import unquote, urlparse

import webui_server as base
import webui_server_v4 as v4
import step07_report as s7
from henna_preview.final_report import ReportError

INDEX_V5 = os.path.join(base.WEB_DIR, "index_v5.html")
NAME = re.compile(r"^[0-9A-Za-z_\-]{1,80}$")


def list_designs():
    items = []
    for name in sorted(os.listdir(base.OUT_ROOT), reverse=True) if os.path.isdir(base.OUT_ROOT) else []:
        p = os.path.join(base.OUT_ROOT, name, "design_report.json")
        if NAME.match(name) and os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                r = json.load(f)
            items.append({"id": name, "created": r.get("created", ""), "params": r["params"],
                          "complete": r["validity"]["complete_unbroken"], "region": r["inputs"].get("region")})
    return items


def final_report(params):
    name = params.get("design")
    if not isinstance(name, str) or not NAME.match(name):
        raise ValueError("unknown design")
    ddir = os.path.join(base.OUT_ROOT, name)
    if not os.path.isfile(os.path.join(ddir, "design_report.json")):
        raise ValueError("unknown design")
    rid = json.load(open(os.path.join(ddir, "design_report.json"), encoding="utf-8"))["inputs"].get("region")
    rdir = v4.region_dir_from_id(rid)
    out = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-final")
    rep = s7.write(ddir, rdir, out)
    fid = os.path.basename(out)
    return {"report": rep, "output_folder": out, "overlay": f"/outputs/{fid}/final_overlay.png",
            "download": f"/download/{fid}/final_report.json"}


class Handler(v4.Handler):
    server_version = "HennaPreview/0.5"

    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path).path
        if u in ("/", "/index.html", "/index_v5.html"):
            return self._file(INDEX_V5, "text/html; charset=utf-8")
        if u == "/api/designs":
            return self._send(200, {"designs": list_designs()})
        if u.startswith("/download/"):
            parts = unquote(u[len("/download/"):]).split("/")
            if len(parts) == 2 and NAME.match(parts[0]) and parts[1] == "final_report.json":
                p = os.path.join(base.OUT_ROOT, *parts)
                if base.is_inside(p, base.OUT_ROOT) and os.path.isfile(p):
                    with open(p, "rb") as f:
                        data = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Disposition", f'attachment; filename="{parts[0]}_final_report.json"')
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return
            return self._send(404, {"error": "not found"})
        return super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path != "/api/final_report":
            return super().do_POST()
        if not self._host_ok():
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "send JSON"})
        try:
            params = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 5000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad JSON"})
        if not base._run_lock.acquire(blocking=False):
            return self._send(409, {"error": "a run is already in progress"})
        try:
            self._send(200, final_report(params))
        except (ValueError, ReportError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna preview web UI v5 (preview only, motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
