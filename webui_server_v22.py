"""Studio v22 - "Third review fix" (outside review by Codex, 2026-10-12).

Same as v21 (same page, studio_v20.html) with one safety fix: a successful Home clears "STOP NOT CONFIRMED"
inside the homing operation, before the machine is released, so a late Home can never erase a newer failed-STOP
warning (henna_preview/grbl_program_link_v22.py; v13-v21 files unchanged).
"""
from urllib.parse import urlparse

import webui_server as base
import webui_server_v10 as v10
import webui_server_v14 as v14
import webui_server_v16 as v16
import webui_server_v20 as v20
import webui_server_v21 as v21
from henna_preview.grbl_program_link import MachineError
from henna_preview.grbl_program_link_v22 import GrblProgramLinkV22

STUDIO_HTML = v21.STUDIO_HTML

machine = GrblProgramLinkV22(calibration_provider=v10.cal, on_event=v14.v13.log_event)
# One serial link per process: every v13-v21 function and route looks the link up at call time.
v21.machine = machine
v20.machine = machine
v16.v15.machine = machine
v14.machine = machine
v14.v13.machine = machine


class Handler(v21.Handler):
    server_version = "HennaStudio/0.22"

    def do_GET(self):
        if urlparse(self.path).path in ("/", "/index.html", "/studio"):
            if self._host_ok():
                return self._file(STUDIO_HTML, "text/html; charset=utf-8")
            return
        return super().do_GET()


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    print("Henna Studio v22 - third review fix. motion_ready=false", flush=True)
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
