"""Local web UI, revision 3: adds '3 Reviewed region'.

Run:   .venv\\Scripts\\python webui_server_v3.py
Open:  http://127.0.0.1:8765

Revisions 1 and 2 are kept unchanged; this file reuses their safety checks.
Reads only hand-detection run folders produced by this project. Accepted
regions are saved under outputs/webui/<time>-region/. No camera, controller,
serial ports or G-code. motion_ready is always false.
"""
import datetime as dt
import json
import os
import re
from urllib.parse import parse_qs, urlparse

import numpy as np

import webui_server as base
import webui_server_v2 as v2
from henna_preview.region import RegionError, evaluate_region, load_bundle, suggest_region
from step05_region import DEFAULT_MARGIN, holes_png_data_url, png_data_url, save_region_outputs
from PIL import Image

INDEX_V3 = os.path.join(base.WEB_DIR, "index_v3.html")
RUN_ROOTS = {"web": base.OUT_ROOT, "cli": os.path.join(base.ROOT, "outputs", "step04")}
RUN_NAME = re.compile(r"^[0-9A-Za-z_\-]{1,80}$")


def run_dir_from_id(run_id):
    if not isinstance(run_id, str) or ":" not in run_id:
        raise ValueError("unknown run")
    root_key, name = run_id.split(":", 1)
    root = RUN_ROOTS.get(root_key)
    if root is None or not RUN_NAME.match(name):
        raise ValueError("unknown run")
    d = os.path.join(root, name)
    if not (base.is_inside(d, root) and os.path.isfile(os.path.join(d, "frame_bundle.npz"))):
        raise ValueError("unknown run")
    return d


def list_hand_runs():
    items = []
    for key, root in RUN_ROOTS.items():
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            d = os.path.join(root, name)
            rp = os.path.join(d, "report.json")
            if not (RUN_NAME.match(name) and os.path.isfile(os.path.join(d, "frame_bundle.npz")) and os.path.isfile(rp)):
                continue
            with open(rp, encoding="utf-8") as f:
                r = json.load(f)
            items.append({"id": f"{key}:{name}", "created": r.get("created", ""),
                          "recording": os.path.basename(r["input"]["path"]), "frame": r["input"]["frame_index"],
                          "hand_detected": r.get("hand_detected", False),
                          "score": r["hands"][0]["score"] if r.get("hands") else None})
    return sorted(items, key=lambda i: i["created"], reverse=True)


def margin_of(params):
    try:
        m = float(params.get("margin", DEFAULT_MARGIN))
    except (TypeError, ValueError):
        raise ValueError("margin must be a number")
    if not 0.0 <= m < 0.95:
        raise ValueError("margin must be between 0 and 0.95")
    return m


def hand_run_payload(run_id, margin):
    b = load_bundle(run_dir_from_id(run_id))
    h, w = b["depth_raw_aligned"].shape
    lm = b["landmarks_px"].tolist()
    return {"run": run_id, "width": w, "height": h, "source": b["source"],
            "colour": png_data_url(Image.fromarray(b["colour_rgb"])),
            "holes": holes_png_data_url(b["depth_raw_aligned"]),
            "landmarks": lm, "suggestion": suggest_region(lm, margin) if len(lm) == 21 else None}


def region_check(params):
    b = load_bundle(run_dir_from_id(params.get("run")))
    res = evaluate_region(b, params.get("polygon"), max_points=base.MAX_POINTS_3D)
    return {"stats": res["stats"], "points": {"xyz_mm": np.round(res["points_mm"], 1).ravel().tolist(),
                                              "every_nth": res["stats"]["points_every_nth"],
                                              "total": res["stats"]["measured_px"]}}


def region_accept(params):
    b = load_bundle(run_dir_from_id(params.get("run")))
    if params.get("confirm") is not True:
        raise ValueError("acceptance must be confirmed")
    out = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-region")
    n = 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-region-{n}")
    rec, _ = save_region_outputs(out, b, params.get("polygon"), margin_of(params),
                                 bool(params.get("edited")), accepted=True)
    rid = os.path.basename(out)
    return {"record": rec, "output_folder": out, "overlay": f"/outputs/{rid}/region_overlay.png"}


class Handler(v2.Handler):
    server_version = "HennaPreview/0.3"

    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/index_v3.html"):
            return self._file(INDEX_V3, "text/html; charset=utf-8")
        if u.path == "/api/hand_runs":
            return self._send(200, {"runs": list_hand_runs()})
        if u.path == "/api/hand_run":
            q = parse_qs(u.query)
            try:
                return self._send(200, hand_run_payload(q.get("run", [""])[0], margin_of({"margin": q.get("margin", [DEFAULT_MARGIN])[0]})))
            except (ValueError, RegionError) as e:
                return self._send(400, {"error": str(e)})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        handlers = {"/api/region_check": region_check, "/api/region_accept": region_accept}
        if path not in handlers:
            return super().do_POST()
        if not self._host_ok():
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "send JSON"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            params = json.loads(self.rfile.read(min(length, 50000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad JSON"})
        if not base._run_lock.acquire(blocking=False):
            return self._send(409, {"error": "a run is already in progress"})
        try:
            self._send(200, handlers[path](params))
        except (ValueError, RegionError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna preview web UI v3 (preview only, motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    print("Approved depth-file folders:", *base.APPROVED_DIRS, sep="\n  ")
    print("Approved recording folders (.bag/.db3 only):", *v2.APPROVED_RECORDING_DIRS, sep="\n  ")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
