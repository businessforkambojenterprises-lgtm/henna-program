"""Studio v19 - "Font choice for names" (user request 2026-10-10).

The 'Write a name' box gets a font list: Studio single-line capitals (v18, first), the handwriting set (Segoe
Script, Segoe Print, Ink Free, French Script MT) and every other font installed on this PC. Installed fonts are
turned into single centre lines by henna_preview.font_strokes; only fonts from the server's own list are
accepted. The SVG goes through the normal upload checks; everything after the Design step is unchanged (v17/v18).
"""
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v16 as v16
import webui_server_v18 as v18
from henna_preview import font_strokes

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v19.html")
STUDIO_FONT = "studio"


def fonts(_=None):
    listed = [{"id": f["id"], "name": f["name"], "recommended": f["recommended"]} for f in font_strokes.list_fonts()]
    return {"fonts": [{"id": STUDIO_FONT, "name": "Studio single-line capitals", "recommended": True,
                       "builtin": True}] + listed}


def name_design(params):
    fid = params.get("font") or STUDIO_FONT
    if fid == STUDIO_FONT:
        return v18.name_design(params)
    text = params.get("text")
    svg = font_strokes.font_name_svg(text, fid)
    stem = font_strokes.file_stem(text, fid)
    existing = os.path.join(v11.UPLOAD_DIR, stem + ".svg")
    if os.path.isfile(existing):
        with open(existing, encoding="utf-8") as f:
            if f.read() == svg:                      # the same name in the same font: reuse it
                return {"design": v11.design_entry(existing)}
    return v11.design_upload({"filename": stem, "svg": svg})


class Handler(v18.Handler):
    server_version = "HennaStudio/0.19"
    GET_API = dict(v18.Handler.GET_API, **{"/api/studio/fonts": fonts})
    POST_API = dict(v18.Handler.POST_API, **{"/api/studio/name_design": (name_design, True)})

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    link = v16.machine()
    print("Henna Studio v19 - names in many fonts, low hops. motion_ready=false", flush=True)
    print(f"Open http://{base.HOST}:{base.PORT}; press Ctrl+C to stop (a running program is stopped)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if link.status().get("busy") in v14.MOVING:
                link.stop()
        finally:
            try:
                srv.server_close()
            finally:
                try:
                    if link.status().get("busy") in v14.MOVING:
                        link.stop()
                finally:
                    try:
                        link.disconnect()
                    finally:
                        v10.camera.stop()


if __name__ == "__main__":
    main()
