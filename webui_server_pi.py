"""Henna Studio for the Raspberry Pi = Studio v29 with the camera in its own process, the faster live preview and a
touch layout for the 5" display.

- henna_preview/camera_process.py: the camera runs in a child process, so a camera-library freeze (2-2.5 s on
  the Pi when the camera starts or stops, stage 3b 2026-10-12) can no longer pause the CNC link and its 1.5 s check.
- henna_preview/web_recorder_fast.py (inside that child): lookup-table colours, a new picture only when fetched.
- webui/pi_touch.js (2026-10-14): drag-to-scroll, for a touchscreen the browser reports as a mouse.
- webui/pi_touch.css (2026-10-14): added to the page after its own styles - one column, steps as tabs, larger buttons
  and tick boxes for the Touch Display 2 (5", 1280 x 720); raspberry_pi/henna_studio.sh zooms the window x2.
  Layout and sizes only: the same page, routes and checks.
- henna_preview/pause_button.py (2026-10-15): a hardware PAUSE push button on GPIO17 (pin 11) to GND (pin 9) - Pause
  while a program runs, STOP while anything else moves; GET /api/pi/pause_button, shown in the machine bar. The STOP
  button is a latching emergency stop in the controller's power wire, outside the software.
- Paste SSR (2026-10-15): on the DLC32's laser TTL output, the same as the laptop (user: "ssr not working fine on
  pi, move it to dlc32") - Studio v29 switches it with M3 / M5 and checks $32=0 and $30=1000 before every paste run.
  The Pi pin (henna_preview/paste_gpio.py, GPIO27 pin 13 to GND pin 14) is used only when started with
  HENNA_PASTE=pi; GET /api/pi/paste_output says which.
- ADMIN (2026-10-16, user: "all the calibration, machine selection and valve selection option should come under
  admin panel", 4-digit PIN): henna_preview/admin_lock.py; every /api/calibration/*, /api/machines/* change, the
  servo angles and the paste setup (henna_preview/paste_setup.py, applied to every program) need an unlocked
  session (X-Admin-Token). Forgotten PIN: raspberry_pi/admin_pin_reset.py on the Pi.
- Paste pinch-valve SERVO (2026-10-16, user: "gpio pin from rasp", 180 deg servo, pinch valve): signal on GPIO18
  (pin 12), its own 5-6 V supply. Used when outputs/paste_servo.json was saved by raspberry_pi/servo_angle_set.py
  (closed and open angles), or with HENNA_PASTE=servo. Choice order: HENNA_PASTE (controller / pi / servo), else the
  saved servo file, else the DLC32's TTL output. In Studio (Program step -> Servo angles, user 2026-10-16): try any
  angle and save closed / open (POST /api/pi/servo/try, /api/pi/servo/save) while nothing runs.
Everything else - page, machine link, checks, recordings and their verification - is v29 unchanged (machine profiles,
per-machine calibration in Studio, travel clearance over the hand, real-hand hover runs watched by the camera - the
child camera process also makes the watch's depth samples). The laptop runs webui_server_v29.py. Started on
the Pi by raspberry_pi/henna_studio.sh (the desktop icon).
PASS on the Pi 2026-10-12 (with v22/v23): stage 3b phase E (worst status age 0.29 s) and 3 real scans, none refused.
v23 -> v27 -> v28 on 2026-10-14, v29 (paste) on 2026-10-15 (merges of main).
When a new Studio version comes, change the one import below to it (its tests check the swap).
"""
import os
from urllib.parse import urlparse

import webui_server_v29 as studio
from henna_preview import paste_setup
from henna_preview.admin_lock import AdminLock
from henna_preview.camera_process import ProcessCameraService
from henna_preview.paste_gpio import GpioPasteOutput
from henna_preview.paste_servo import ServoPasteOutput, wants_servo
from henna_preview.pause_button import PauseButton

PI_CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "pi_touch.css")
PI_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "pi_touch.js")
PAGES = ("/", "/index.html", "/studio")

# Every v10-v29 route looks v10.camera up at call time, so this one swap reaches all of them.
studio.v10.camera = ProcessCameraService()
PAUSE_BUTTON = PauseButton(studio.machine, studio._log)
def paste_choice():
    env = os.environ.get("HENNA_PASTE", "").strip().lower()
    if env in ("controller", "pi", "servo"):
        return env
    return "servo"          # 2026-10-16: the servo output always exists (ready once its angles are saved in Admin);
                            # the Paste choice in Admin decides per run: Pneumatic -> DLC32 TTL, Servo -> this servo


PASTE_MODE = paste_choice()
PASTE_ON_PI = PASTE_MODE != "controller"
PASTE = ServoPasteOutput() if PASTE_MODE == "servo" else GpioPasteOutput()
if PASTE_ON_PI:
    studio.machine.paste_output = PASTE                     # not ready until started: paste refused until then


def _servo_idle():
    if not isinstance(PASTE, ServoPasteOutput) or not PASTE_ON_PI:
        raise ValueError("this Pi was started without the servo output (HENNA_PASTE) - restart Studio from the icon")
    st = studio.machine.status()
    if st.get("busy") or (st.get("program") or {}).get("state") == "running":
        raise ValueError("wait until the machine has finished - servo angles are set only while nothing runs")


def servo_try(params):
    _servo_idle()
    v = PASTE.try_angle((params or {}).get("angle"))
    studio._log("Paste servo: try angle", None, f"{PASTE.name} {float(params['angle']):g} deg", "operator looks at the tube",
                "-", "moved", "", "Studio Pi servo angles")
    return v


def servo_save(params):
    _servo_idle()
    p = params or {}
    v = PASTE.set_angles(p.get("closed_deg"), p.get("open_deg"))
    studio._log("Paste servo: angles saved", None, f"{PASTE.name} closed {v['closed_deg']:g} open {v['open_deg']:g}",
                "closed = tube shut, open = tube open", "-", "saved, servo closed", "", "Studio Pi servo angles")
    return v


# ---------- Admin (user 2026-10-16: calibration, machine selection and valve selection under a PIN-locked admin) ----
ADMIN = AdminLock()
ADMIN_PATHS = {"/api/machines/select", "/api/machines/add", "/api/machines/recalibrate", "/api/machines/setup_area",
               "/api/machines/setup_scan", "/api/pi/servo/try", "/api/pi/servo/save", "/api/pi/paste_setup"}
ADMIN_PREFIXES = ("/api/calibration/",)
# The daily check stays with the operator (user 2026-10-16: "operator should run"): moving to its scan position and
# running it. Setting the reference, calibrating and choosing the machine stay under Admin.
OPERATOR_PATHS = {"/api/calibration/scan_position", "/api/calibration/check"}


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
        studio._log("Admin: unlock refused", None, "Admin PIN", "correct PIN", str(e), "refused", "", "Studio Pi admin")
        raise
    studio._log("Admin: unlocked", None, "Admin PIN", "correct PIN", "ok", "unlocked", "", "Studio Pi admin")
    return {"token": token}


def admin_set_pin(params):
    p = params or {}
    first = not ADMIN.pin_set()
    token = ADMIN.set_pin(p.get("pin"), current=p.get("current"), token=p.get("token"))
    studio._log("Admin: PIN set" if first else "Admin: PIN changed", None, "Admin PIN", "4 digits", "-", "saved", "",
                "Studio Pi admin")
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
                "saved", "", "Studio Pi admin")
    return {"setup": v}


_PROGRAM = studio.Handler.POST_API["/api/studio/program"]


def pi_program(params):
    """Every program uses the Admin's paste setup (the operator's Program step cannot change it)."""
    return _PROGRAM[0](paste_setup.apply(params, paste_setup.load()))


def paste_view(_=None):
    if not PASTE_ON_PI:
        return {"output": "controller", "ready": True, "note": "SSR on the DLC32 laser TTL output (M3/M5)"}
    return dict(PASTE.view(), output=PASTE.name, mode=PASTE_MODE)


def pi_page():
    """Studio's page with the Pi touch styles added last and the drag-to-scroll script (read per request: a git pull
    needs no restart for them)."""
    with open(studio.STUDIO_HTML, encoding="utf-8") as f:
        html = f.read()
    with open(PI_CSS, encoding="utf-8") as f:
        css = f.read()
    with open(PI_JS, encoding="utf-8") as f:
        js = f.read()
    if "</head>" not in html or "</body>" not in html:
        raise RuntimeError("Studio page has no </head> or </body>")
    html = html.replace("</head>", f'<style id="pi-touch">\n{css}\n</style>\n</head>', 1)
    i = html.rindex("</body>")
    return html[:i] + f'<script id="pi-touch-js">\n{js}\n</script>\n' + html[i:]


class PiHandler(studio.Handler):
    server_version = studio.Handler.server_version + "-pi"
    GET_API = dict(studio.Handler.GET_API, **{"/api/pi/pause_button": PAUSE_BUTTON.view,
                                              "/api/pi/paste_output": paste_view,
                                              "/api/pi/paste_setup": paste_setup_view})
    POST_API = dict(studio.Handler.POST_API, **{"/api/pi/servo/try": (servo_try, False),
                                                "/api/pi/servo/save": (servo_save, False),
                                                "/api/pi/paste_setup": (paste_setup_save, False),
                                                "/api/pi/admin/status": (admin_status, False),
                                                "/api/pi/admin/unlock": (admin_unlock, False),
                                                "/api/pi/admin/set_pin": (admin_set_pin, False),
                                                "/api/pi/admin/lock": (admin_lock, False),
                                                "/api/studio/program": (pi_program, _PROGRAM[1])})

    def do_POST(self):
        path = urlparse(self.path).path
        if needs_admin(path) and not ADMIN.valid(self.headers.get("X-Admin-Token", "")):
            if self._host_ok():
                self._send(403, {"error": "Admin is locked - open Admin and enter the PIN"})
            return
        return super().do_POST()

    def do_GET(self):
        if urlparse(self.path).path in PAGES:
            if self._host_ok():
                return self._send(200, pi_page().encode("utf-8"), "text/html; charset=utf-8")
            return
        return super().do_GET()


studio.Handler = PiHandler                                  # v29.main serves studio.Handler


def _stop(signum, frame):
    raise KeyboardInterrupt                                 # -> v26.main's safe shutdown (stop a moving machine, disconnect)


if __name__ == "__main__":
    import signal
    # henna_studio.sh starts this server in the background, and a background job starts with SIGINT IGNORED - so its
    # "window closed -> kill -INT" never stopped it (found 2026-10-14: an old server kept running after an update).
    # Both INT and TERM now run the same safe shutdown as Ctrl+C.
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    print("Raspberry Pi: camera in its own process + faster live preview + touch layout", flush=True)
    print(f"Pause button GPIO{PAUSE_BUTTON.state['pin']}: {PAUSE_BUTTON.start()}", flush=True)
    if PASTE_ON_PI:
        print(f"Paste output {PASTE.name}: {PASTE.start()}", flush=True)
    else:
        print("Paste output: the controller's laser TTL output (M3/M5)", flush=True)
    try:
        studio.main()
    finally:
        PASTE.close()
        PAUSE_BUTTON.close()
        studio.v10.camera.close()
