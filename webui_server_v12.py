"""Henna Studio with a machine link - web UI revision 12 (v11 + USB link: position reading and homing only).

Run:   .venv\\Scripts\\python webui_server_v12.py      Open: http://127.0.0.1:8765
Adds, on top of v11 (unchanged):
  * Connect / disconnect to the controller (the CH340 USB port, found automatically; disconnect UGS first - one program per port).
  * Live machine state and position; "Use machine position" fills the ? boxes (Idle only).
  * Home ($HZ then $H) after the user confirms nothing is under the nozzle; STOP (feed hold + reset).
The page still cannot send any other G-code: programs and moves are run by the user in UGS.
Decided by the user 2026-10-09: USB COM7; allowed = read position automatically + homing.
"""
import webui_server as base
import webui_server_v10 as v10
import webui_server_v11 as v11
from henna_preview.grbl_link import GrblLink, MachineError

FALLBACK_PORT = "COM7"
machine = GrblLink()


def ports():
    import serial.tools.list_ports as lp
    return [{"device": p.device, "description": p.description, "ch340": (p.vid, p.pid) == (0x1A86, 0x7523)}
            for p in lp.comports()]


def default_port():
    """The CH340 USB port of the controller (its COM number differs between PCs), else COM7."""
    ch = [p["device"] for p in ports() if p["ch340"]]
    return ch[0] if len(ch) == 1 else FALLBACK_PORT


def connect(params):
    port = params.get("port") or default_port()
    if port not in [p["device"] for p in ports()]:
        raise MachineError(f"{port} does not exist on this PC")
    st = machine.connect(port)
    v10.log_row(v10.LOG_FILE, 0, "Studio: machine connected", st["mpos"], f"open {port} (DTR/RTS low)", "status line",
                st["line"] or "", "connected", "", "controller restarted on connect" if st["restarted"] else "")
    return st


def disconnect(params):
    return machine.disconnect()


def _homing_done(err):
    st = machine.status()
    v10.log_row(v10.LOG_FILE, 0, "Studio: homing finished" if not err else "Studio: homing FAILED", st["mpos"],
                "$HZ then $H", "Idle after homing", (st["line"] or "") + (f"; {err}" if err else ""),
                "done" if not err else "FAILED", "")


def home(params):
    if params.get("confirm_clear") is not True or params.get("confirm_estop") is not True:
        raise ValueError("confirm that nothing is under the nozzle and the emergency stop is within reach")
    st = machine.start_homing(on_done=_homing_done)
    v10.log_row(v10.LOG_FILE, 0, "Studio: homing started", st["mpos"], "$HZ then $H", "", "user confirmed clear bed + e-stop",
                "started", "")
    return st


def stop(params):
    st = machine.stop()
    v10.log_row(v10.LOG_FILE, 0, "Studio: STOP", st["mpos"], "! then Ctrl-X", "", "feed hold + soft reset", "stopped", "")
    return st


def status_line(params):
    line = machine.status_line()
    return {"line": line}


class Handler(v11.Handler):
    server_version = "HennaStudio/0.12"
    GET_API = dict(v11.Handler.GET_API, **{"/api/machine/status": lambda q: dict(machine.status(), default_port=default_port()),
                                            "/api/machine/ports": lambda q: {"ports": ports()},
                                            "/api/machine/status_line": lambda q: status_line({})})
    POST_API = dict(v11.Handler.POST_API, **{"/api/machine/connect": (connect, False),
                                              "/api/machine/disconnect": (disconnect, False),
                                              "/api/machine/home": (home, False),
                                              "/api/machine/stop": (stop, False)})
    ERRORS = v11.Handler.ERRORS + (MachineError,)


def main():
    srv = base.http.server.ThreadingHTTPServer((base.HOST, base.PORT), Handler)
    print("Henna Studio (web UI v12) - with machine link: position reading and homing only. motion_ready=false")
    print(f"Open http://{base.HOST}:{base.PORT}   (disconnect UGS before pressing Connect)")
    print("Press Ctrl+C to stop")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        machine.disconnect()
        v10.camera.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
