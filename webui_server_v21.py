"""Studio v21 - "Second review fixes" (outside review by Codex, 2026-10-12).

Same as v20 (same page, studio_v20.html) with:
1. "STOP NOT CONFIRMED" stays until a successful Home - Connect no longer clears it.
2. Each run's record (job + log) is built only from that run's own progress and machine reports, also when a
   STOP or lost connection released the machine before the run finished recording.
3. Names: the name is XML-escaped in the SVG title ("A&B" made a broken file).
4. Names: reuse looks for the file name that was actually saved (40 characters), so long names are not saved
   again and again.
(henna_preview/grbl_program_link_v21.py, henna_preview/name_files_v21.py; v13-v20 files unchanged.)
"""
import os
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
import webui_server_v14 as v14
import webui_server_v16 as v16
import webui_server_v19 as v19
import webui_server_v20 as v20
from henna_preview import font_strokes, name_files_v21, stroke_font
from henna_preview.grbl_program_link import MachineError
from henna_preview.grbl_program_link_v21 import GrblProgramLinkV21

STUDIO_HTML = v20.STUDIO_HTML

machine = GrblProgramLinkV21(calibration_provider=v10.cal, on_event=v14.v13.log_event)
# One serial link per process: every v13-v20 function and route looks the link up at call time.
v20.machine = machine
v16.v15.machine = machine
v14.machine = machine
v14.v13.machine = machine


def name_design(params):
    fid = params.get("font") or v19.STUDIO_FONT
    text = params.get("text")
    if fid == v19.STUDIO_FONT:
        svg, stem = stroke_font.name_svg(text), stroke_font.file_stem(text)
    else:
        svg, stem = name_files_v21.font_name_svg(text, fid), font_strokes.file_stem(text, fid)
    same = name_files_v21.find_same(v11.UPLOAD_DIR, stem, svg)
    if same:                                         # the same name in the same font: reuse it
        return {"design": v11.design_entry(same)}
    return v11.design_upload({"filename": stem, "svg": svg})


class Handler(v20.Handler):
    server_version = "HennaStudio/0.21"
    POST_API = dict(v20.Handler.POST_API, **{"/api/studio/name_design": (name_design, True)})

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    print("Henna Studio v21 - second review fixes. motion_ready=false", flush=True)
    print(f"Open http://{base.HOST}:{base.PORT}; press Ctrl+C to stop (a running program is stopped)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if machine.status().get("busy") in v14.MOVING:
                machine.stop()
        except MachineError:
            pass
        finally:
            try:
                srv.server_close()
            finally:
                try:
                    if machine.status().get("busy") in v14.MOVING:
                        machine.stop()
                except MachineError:
                    pass
                finally:
                    try:
                        machine.disconnect()
                    finally:
                        v10.camera.stop()


if __name__ == "__main__":
    main()
