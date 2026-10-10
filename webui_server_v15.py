"""Studio v15: v14 with the two fixes from the first real run (2026-10-10, user approved).

- The machine position stays fresh during a run (status polled on a fixed schedule).
- Pause is verified: if the controller still reports motion, the hold is sent again; if it does not
  hold within 1.5 s, the run is stopped (feed hold + reset).
Same page, steps, rules and safety limits as v14 (4 mm minimum, prosthetic only, jog at Z -20 or higher).
"""
import webui_server as base
import webui_server_v10 as v10
import webui_server_v13 as v13
import webui_server_v14 as v14
from henna_preview.grbl_program_link_v15 import GrblProgramLinkV15

machine = GrblProgramLinkV15(calibration_provider=v10.cal, on_event=v13.log_event)
# One serial link per process: every v13/v14 function and API route looks the link up at call time.
v14.machine = machine
v13.machine = machine


class Handler(v14.Handler):
    server_version = "HennaStudio/0.15"


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    srv.daemon_threads = False          # let a scan that is being verified finish before shutdown
    print("Henna Studio v15 - Studio runs checked prosthetic hover programs. motion_ready=false", flush=True)
    print(f"Open http://{base.HOST}:{base.PORT}; press Ctrl+C to stop (a running program is stopped)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if machine.status().get("busy") in v14.MOVING:
                machine.stop()
        finally:
            try:
                srv.server_close()
            finally:
                try:
                    if machine.status().get("busy") in v14.MOVING:
                        machine.stop()
                finally:
                    try:
                        machine.disconnect()
                    finally:
                        v10.camera.stop()


if __name__ == "__main__":
    main()
