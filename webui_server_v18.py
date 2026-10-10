"""Studio v18: write a name on the hand (user request 2026-10-10).

Design step: type a name -> single-stroke capital letters (henna_preview.stroke_font) -> saved as a line-art SVG
in designs/uploads through the normal upload checks -> selected like any design. Sizing, placement, the
all-or-nothing skin check, program (speed, low hops) and run are unchanged from v17.
"""
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v16 as v16
import webui_server_v17 as v17
from henna_preview import stroke_font

STUDIO_HTML = os.path.join(base.WEB_DIR, "studio_v18.html")


def name_design(params):
    text = params.get("text")
    svg = stroke_font.name_svg(text)
    stem = stroke_font.file_stem(text)
    existing = os.path.join(v11.UPLOAD_DIR, stem + ".svg")
    if os.path.isfile(existing):
        with open(existing, encoding="utf-8") as f:
            if f.read() == svg:                      # the same name again: reuse it
                return {"design": v11.design_entry(existing)}
    return v11.design_upload({"filename": stem, "svg": svg})


class Handler(v17.Handler):
    server_version = "HennaStudio/0.18"
    POST_API = dict(v17.Handler.POST_API, **{"/api/studio/name_design": (name_design, True)})

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
    print("Henna Studio v18 - names on the hand, low hops. motion_ready=false", flush=True)
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
