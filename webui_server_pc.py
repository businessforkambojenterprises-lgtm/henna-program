"""Henna Studio on the PC = Studio v29 with the Pi's home page and Admin page (user 2026-10-24: "it should have same
home page and admin page like pis studio").

Ported from the raspberry-pi branch's webui_server_pi.py and webui/pi_touch.js (not merged - the Pi branch stays
apart): the parts that are about the home page and Admin, without the Pi hardware and the touch screen.
- Home: chips (machine, paste) and tiles - New henna job, Saved jobs, Daily check, Admin; a Home button in the top
  bar. The machine and calibration buttons move into Admin.
- ADMIN (4-digit PIN, henna_preview/admin_lock.py; outputs/admin_pin.json on this PC): one page with tabs - Machine,
  Calibrate, Daily check, Paste (dispenser + delays, test), PIN - and a Position card on top (state, X Y Z, what the
  tab needs, ONE safety tick, Home / Move to scan position / Jog). Every /api/calibration/* and /api/machines/*
  change and the paste setup need an unlocked session (X-Admin-Token); the daily check (scan position + check) stays
  with the operator. Forgotten PIN: admin_pin_reset.py on this PC.
- Paste setup (henna_preview/paste_setup.py, outputs/paste_setup.json): chosen in Admin, applied to every program;
  the Program step shows it. Both dispensers switch on the DLC32's laser TTL output (M3/M5), as in v29.
- Admin jog (henna_preview/admin_jog.py): with a session, Z moves freely between Z 0 and the machine's Z floor and
  X/Y at any height down to it, inside the work area; without one the normal jog rules apply.
Not ported (Pi only): the camera child process, the GPIO pause button, the servo / GPIO paste outputs, the storage
cleanup, the touch layout, drag-to-scroll, the small-screen confirm box and the full-screen region editor.
Everything else is v29 unchanged; start_studio_pc.bat starts this file.
"""
import json
import os
import sys
import threading
import time
from urllib.parse import urlparse

import webui_server_v26 as v26
import webui_server_v29 as studio
from henna_preview import admin_jog, paste_setup
from henna_preview.admin_lock import AdminLock

HERE = os.path.dirname(os.path.abspath(__file__))
PC_CSS = os.path.join(HERE, "webui", "pc_admin.css")
PC_JS = os.path.join(HERE, "webui", "pc_admin.js")
PAGES = ("/", "/index.html", "/studio")
BOOT = str(time.time())                     # this Studio start: the page opens on the home page once per start

ADMIN = AdminLock()
ADMIN_PATHS = {"/api/machines/select", "/api/machines/add", "/api/machines/recalibrate", "/api/machines/setup_area",
               "/api/machines/setup_scan", "/api/admin/paste_setup"}
ADMIN_PREFIXES = ("/api/calibration/",)
OPERATOR_PATHS = {"/api/calibration/scan_position", "/api/calibration/check"}      # the daily check: operators


def needs_admin(path):
    if path in OPERATOR_PATHS:
        return False
    return path in ADMIN_PATHS or path.startswith(ADMIN_PREFIXES)


def admin_status(params):
    return ADMIN.status((params or {}).get("token"))


def admin_unlock(params):
    try:
        token = ADMIN.unlock((params or {}).get("pin"))
    except ValueError as e:
        studio._log("Admin: unlock refused", None, "Admin PIN", "correct PIN", str(e), "refused", "", "Studio PC admin")
        raise
    studio._log("Admin: unlocked", None, "Admin PIN", "correct PIN", "ok", "unlocked", "", "Studio PC admin")
    return {"token": token}


def admin_set_pin(params):
    p = params or {}
    first = not ADMIN.pin_set()
    token = ADMIN.set_pin(p.get("pin"), current=p.get("current"), token=p.get("token"))
    studio._log("Admin: PIN set" if first else "Admin: PIN changed", None, "Admin PIN", "4 digits", "-", "saved", "",
                "Studio PC admin")
    return {"token": token}


def admin_lock(params):
    ADMIN.lock_now((params or {}).get("token"))
    return {"unlocked": False}


def paste_setup_view(_=None):
    return {"setup": paste_setup.load(), "dispensers": studio.dispense.DISPENSERS}


def paste_setup_save(params):
    p = params or {}
    v = paste_setup.save(p.get("dispenser"), p.get("start_s"), p.get("stop_s"))
    studio._log("Admin: paste setup saved", None, "Admin -> Paste", "dispenser and delays",
                f"{v.get('label')}" + (f", start {v['start_s']:g} s, stop {v['stop_s']:g} s" if v.get("start_s") is not None else ""),
                "saved", "", "Studio PC admin")
    return {"setup": v}


_PROGRAM = studio.Handler.POST_API["/api/studio/program"]


def pc_program(params):
    """Every program uses the Admin's paste setup (none saved yet: the Program step's own choice, as in v29)."""
    return _PROGRAM[0](paste_setup.apply(params, paste_setup.load()))


# Admin jog: the Pi's rules (webui_server_pi.pi_jog) - with a valid session Z free to the floor, X/Y at any height
# down to it inside the work area; without one v29's jog (travel height, real-hand rule).
_REQ = threading.local()
_JOG = studio.Handler.POST_API["/api/machine/jog"]


def z_floor():
    m = v26.profiles.active()
    return float(v26.profiles.motion(m)["firmware_z_floor"])


def pc_jog(params):
    if not getattr(_REQ, "admin", False):
        return _JOG[0](params)
    if params.get("confirm_no_person") is not True:
        raise ValueError("Confirm that no person's hand is under the machine before jogging")
    if studio.v10.camera.status()["state"] == "recording":
        raise studio.MachineError("Finish recording before jogging")
    if params.get("axis") == "Z":
        return admin_jog.start_admin_z(studio.machine, params.get("direction"), params.get("step_mm"), z_floor(),
                                       studio.free_jog_step)
    m = v26.profiles.active()
    if not m.get("work_area_mm"):
        raise studio.MachineError(f"The {m['name']}'s work area is not measured yet - X and Y stay locked")
    from henna_preview import grbl_program_link as gpl
    travel = float(v26.profiles.motion(m)["travel_z"])
    return admin_jog.start_admin_xy(studio.machine, params.get("axis"), params.get("direction"), params.get("step_mm"),
                                    z_floor(), travel, gpl.X_MAX, gpl.Y_MAX, studio.free_jog_step)


def jog_limits(_=None):
    """For the Admin Jog window: how far Z may go down there."""
    try:
        return {"z_floor": z_floor(), "z_top": 0.0}
    except Exception as e:                              # noqa: BLE001 - no machine chosen / not set up yet
        return {"z_floor": None, "z_top": 0.0, "error": str(e)}


def pc_page():
    """Studio's page with the home / Admin styles added last and their script (read per request: no restart)."""
    with open(studio.STUDIO_HTML, encoding="utf-8") as f:
        html = f.read()
    with open(PC_CSS, encoding="utf-8") as f:
        css = f.read()
    with open(PC_JS, encoding="utf-8") as f:
        js = f.read()
    if "</head>" not in html or "</body>" not in html:
        raise RuntimeError("Studio page has no </head> or </body>")
    html = html.replace("</head>", f'<style id="pc-admin">\n{css}\n</style>\n</head>', 1)
    i = html.rindex("</body>")
    return html[:i] + f'<script id="pc-admin-js">\nwindow.STUDIO_BOOT = "{BOOT}";\n{js}\n</script>\n' + html[i:]


_ALIGN_REF = {"key": None, "gray": None, "created": None}


def camera_align_view(_=None):
    """Admin -> Daily check -> Camera alignment (user 2026-10-28: "add this tool in admin section"): the live picture's
    shift against the daily-check reference, in mm, and which way to turn the camera. Read-only - it moves nothing."""
    from henna_preview import camera_align
    path = camera_align.reference_path(HERE, v26.profiles.active_id())
    if not os.path.isfile(path):
        return {"ready": False, "reason": "no daily-check reference yet - set it first (bed as every day, no hand)"}
    key = (path, os.path.getmtime(path))
    if _ALIGN_REF["key"] != key:
        created = None
        try:
            with open(os.path.join(os.path.dirname(path), "reference.json"), encoding="utf-8") as f:
                created = json.load(f).get("created")
        except (OSError, ValueError):
            pass
        _ALIGN_REF.update(key=key, gray=camera_align.load_reference(path), created=created)
    cam = studio.v10.camera
    if cam.status().get("state") == "off" and hasattr(cam, "start_preview"):
        try:
            cam.start_preview()
        except Exception as e:                              # noqa: BLE001 - say why there is no picture
            return {"ready": False, "reason": f"the camera could not start: {e}"}
    jpeg = cam.jpeg()
    if not jpeg:
        return {"ready": False, "reason": "waiting for the camera picture"}
    st = studio.machine.status() or {}
    out = camera_align.measure(_ALIGN_REF["gray"], camera_align.preview_gray(jpeg))
    return dict(out, ready=True, reference=_ALIGN_REF["created"], at_scan_position=bool(st.get("capture_ready")))


class PcHandler(studio.Handler):
    server_version = studio.Handler.server_version + "-pc"
    GET_API = dict(studio.Handler.GET_API, **{"/api/admin/paste_setup": paste_setup_view,
                                              "/api/admin/jog_limits": jog_limits,
                                              "/api/admin/camera_align": camera_align_view})
    POST_API = dict(studio.Handler.POST_API, **{"/api/admin/status": (admin_status, False),
                                                "/api/admin/unlock": (admin_unlock, False),
                                                "/api/admin/set_pin": (admin_set_pin, False),
                                                "/api/admin/lock": (admin_lock, False),
                                                "/api/admin/paste_setup": (paste_setup_save, False),
                                                "/api/studio/program": (pc_program, _PROGRAM[1]),
                                                "/api/machine/jog": (pc_jog, _JOG[1])})

    def do_POST(self):
        path = urlparse(self.path).path
        _REQ.admin = path == "/api/machine/jog" and ADMIN.valid(self.headers.get("X-Admin-Token", ""))
        if needs_admin(path) and not ADMIN.valid(self.headers.get("X-Admin-Token", "")):
            if self._host_ok():
                self._send(403, {"error": "Admin is locked - open Admin and enter the PIN"})
            return
        return super().do_POST()

    def do_GET(self):
        if urlparse(self.path).path in PAGES:
            if self._host_ok():
                return self._send(200, pc_page().encode("utf-8"), "text/html; charset=utf-8")
            return
        return super().do_GET()


studio.Handler = PcHandler                                  # v29.main serves studio.Handler


if __name__ == "__main__":
    print("Henna Studio PC: home page and Admin (PIN) as on the Pi", flush=True)
    studio.main()
    if studio._EXIT["requested"]:
        sys.exit(studio.EXIT_CODE)
