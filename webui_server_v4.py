"""Local web UI, revision 4: adds '5–6 Design and projection'.

Run:   .venv\\Scripts\\python webui_server_v4.py
Open:  http://127.0.0.1:8765

Revisions 1–3 are kept unchanged; this file reuses their safety checks.
Designs can only be placed on regions the user has accepted. Previews are
computed in memory; 'Save preview' writes outputs/webui/<time>-design/.
No camera, controller, serial ports or G-code. motion_ready is always false.
"""
import datetime as dt
import json
import os
import re
from urllib.parse import parse_qs, urlparse

import numpy as np
from PIL import Image

import webui_server as base
import webui_server_v3 as v3
from henna_preview.design import DesignError
from henna_preview.region import RegionError, evaluate_region
from step05_region import holes_png_data_url, png_data_url
import step06_design as s6

INDEX_V4 = os.path.join(base.WEB_DIR, "index_v4.html")
REGION_ROOTS = {"web": base.OUT_ROOT, "cli": os.path.join(base.ROOT, "outputs", "step05")}
NAME = re.compile(r"^[0-9A-Za-z_\-]{1,80}$")
_cache = {}


def region_dir_from_id(rid):
    if not isinstance(rid, str) or ":" not in rid:
        raise ValueError("unknown region")
    key, name = rid.split(":", 1)
    root = REGION_ROOTS.get(key)
    if root is None or not NAME.match(name):
        raise ValueError("unknown region")
    d = os.path.join(root, name)
    if not (base.is_inside(d, root) and os.path.isfile(os.path.join(d, "region.json"))):
        raise ValueError("unknown region")
    return d


def list_regions():
    items = []
    for key, root in REGION_ROOTS.items():
        if not os.path.isdir(root):
            continue
        for name in os.listdir(root):
            p = os.path.join(root, name, "region.json")
            if not (NAME.match(name) and os.path.isfile(p)):
                continue
            with open(p, encoding="utf-8") as f:
                r = json.load(f)
            items.append({"id": f"{key}:{name}", "accepted": bool(r.get("accepted")), "created": r.get("created", ""),
                          "recording": os.path.basename(r["source"]["recording"]), "frame": r["source"]["frame_index"],
                          "measured_px": r.get("stats", {}).get("measured_px")})
    return sorted(items, key=lambda i: (not i["accepted"], i["created"]), reverse=False)


def loaded(rid):
    """Accepted region + its frame, cached in memory. Raises for unaccepted or mismatched regions."""
    d = region_dir_from_id(rid)
    stamp = os.path.getmtime(os.path.join(d, "region.json"))
    if rid not in _cache or _cache[rid][0] != stamp:
        rec, bundle, mask = s6.load_accepted_region(d)
        if len(_cache) > 4:
            _cache.clear()
        _cache[rid] = (stamp, rec, bundle, mask)
    return _cache[rid][1:]


def region_info(rid):
    rec, bundle, mask = loaded(rid)
    pts = evaluate_region(bundle, rec["polygon_px"], max_points=base.MAX_POINTS_3D)
    h, w = mask.shape
    return {"id": rid, "width": w, "height": h, "source": bundle["source"], "polygon": rec["polygon_px"],
            "centroid": s6.region_centroid(rec["polygon_px"]), "default_jump_mm": s6.DEFAULT_JUMP_MM,
            "colour": png_data_url(Image.fromarray(bundle["colour_rgb"])),
            "holes": holes_png_data_url(bundle["depth_raw_aligned"]),
            "points": {"xyz_mm": np.round(pts["points_mm"], 1).ravel().tolist(),
                       "every_nth": pts["stats"]["points_every_nth"], "total": pts["stats"]["measured_px"]}}


def design_args(params):
    try:
        centre = [float(c) for c in params.get("centre")]
        if len(centre) != 2:
            raise TypeError
        return (params.get("shape"), float(params.get("size_mm")), centre,
                float(params.get("rotation_deg", 0.0)), float(params.get("jump_mm", s6.DEFAULT_JUMP_MM)))
    except (TypeError, ValueError):
        raise ValueError("design needs shape, size_mm, centre [u, v], rotation_deg and jump_mm as numbers")


def design_preview(params):
    rec, bundle, mask = loaded(params.get("region"))
    shape, size, centre, rot, jump = design_args(params)
    res = s6.compute(bundle, mask, shape, size, centre, rot, jump)
    return {"planned": res["planned"], "status": res["status"], "breaks": res["jump_breaks_at_samples"],
            "segments": [{"pixels_uv": s["pixels_uv"], "points_mm": s["points_mm"], "length_mm": s["length_mm"]}
                         for s in res["segments"]],
            "stats": res["stats"], "complete_unbroken": res["complete_unbroken"], "timings_s": res["timings_s"],
            "warnings": s6.report(rec, bundle, res, jump)["warnings"]}


def design_save(params):
    rid = params.get("region")
    rec, bundle, mask = loaded(rid)
    shape, size, centre, rot, jump = design_args(params)
    res = s6.compute(bundle, mask, shape, size, centre, rot, jump)
    out = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-design")
    n = 1
    while os.path.exists(out):
        n += 1
        out = os.path.join(base.OUT_ROOT, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-design-{n}")
    rep = s6.save(out, rec, bundle, mask, res, jump)
    rep["inputs"]["region"] = rid
    with open(os.path.join(out, "design_report.json"), "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=2)
    name = os.path.basename(out)
    return {"report": rep, "output_folder": out, "overlay": f"/outputs/{name}/design_overlay.png"}


class Handler(v3.Handler):
    server_version = "HennaPreview/0.4"

    def do_GET(self):
        if not self._host_ok():
            return
        u = urlparse(self.path)
        if u.path in ("/", "/index.html", "/index_v4.html"):
            return self._file(INDEX_V4, "text/html; charset=utf-8")
        if u.path == "/api/regions":
            return self._send(200, {"regions": list_regions()})
        if u.path == "/api/region_info":
            try:
                return self._send(200, region_info(parse_qs(u.query).get("id", [""])[0]))
            except (ValueError, RegionError, KeyError) as e:
                return self._send(400, {"error": str(e)})
        return super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        handlers = {"/api/design_preview": (design_preview, False), "/api/design_save": (design_save, True)}
        if path not in handlers:
            return super().do_POST()
        if not self._host_ok():
            return
        if not self.headers.get("Content-Type", "").startswith("application/json"):
            return self._send(415, {"error": "send JSON"})
        try:
            length = int(self.headers.get("Content-Length", 0))
            params = json.loads(self.rfile.read(min(length, 20000)) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self._send(400, {"error": "bad JSON"})
        fn, needs_lock = handlers[path]
        if needs_lock and not base._run_lock.acquire(blocking=False):
            return self._send(409, {"error": "a run is already in progress"})
        try:
            self._send(200, fn(params))
        except (ValueError, RegionError, DesignError) as e:
            self._send(400, {"error": str(e)})
        except Exception as e:
            self._send(500, {"error": str(e)})
        finally:
            if needs_lock:
                base._run_lock.release()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna preview web UI v4 (preview only, motion_ready=false)")
    print(f"Open http://{base.HOST}:{base.PORT}  -  press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
