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
- STORAGE (2026-10-17): henna_preview/storage.py - every recording route is refused below 3 GB free; Admin, Storage
  deletes old recordings (default 14 days) of finished jobs, optionally after a copy to a USB stick; automatic at start.
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
import threading
from urllib.parse import urlparse

import webui_server_v26 as v26
import webui_server_v29 as studio
from henna_preview import admin_jog, paste_setup, storage
from henna_preview.admin_lock import AdminLock
from henna_preview.camera_process import ProcessCameraService
from henna_preview.paste_gpio import GpioPasteOutput
from henna_preview.paste_servo import ServoPasteOutput, pi_model, wants_servo
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
               "/api/machines/setup_scan", "/api/pi/servo/try", "/api/pi/servo/save", "/api/pi/paste_setup",
               "/api/pi/storage/settings", "/api/pi/storage/cleanup", "/api/pi/scan_position"}
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


def scan_position_view(_=None):
    """Admin -> Machine -> Scan position (user 2026-10-31: "add scan position option to pi admin panel", as on the PC):
    the active machine's scan position and limits, and - when connected and homed - where the head is now (to copy)."""
    m = v26.profiles.active()
    mo = v26.profiles.motion(m)
    st = studio.machine.status() or {}
    head = st.get("mpos") if st.get("connected") and st.get("homed") and st.get("state") == "Idle" else None
    return {"machine": m["name"], "scan_pose": [float(v) for v in mo["scan_pose_mpos"]], "travel_z": float(mo["travel_z"]),
            "z_floor": float(mo["firmware_z_floor"]), "work_area": [float(v) for v in m["work_area_mm"]],
            "connected": bool(st.get("connected")), "head": head}


def scan_position_save(params):
    """Both files (registry and calibration) change together, as machine_scan_xy_set.py and machine_scan_z_set.py do;
    only while the machine is disconnected (the link takes the new values then). The daily reference must be set again."""
    import machine_scan_xy_set
    import machine_scan_z_set
    p = params or {}
    if p.get("confirm") is not True:
        raise ValueError("Confirm the new scan position")
    if (studio.machine.status() or {}).get("connected"):
        raise studio.MachineError("Disconnect the machine first (the new position is taken when it is off)")
    try:
        x, y, z = (float(p[k]) for k in ("x", "y", "z"))
    except (KeyError, TypeError, ValueError):
        raise ValueError("X, Y and Z: numbers in mm")
    mid = v26.profiles.active_id()
    m = v26.profiles.get(mid)
    mo = v26.profiles.motion(m)
    x, y = machine_scan_xy_set.check_value(x, y, m["work_area_mm"])
    z = machine_scan_z_set.check_value(z, mo)                    # both checked before either file is written
    before = [float(v) for v in mo["scan_pose_mpos"]]
    machine_scan_xy_set.set_scan_xy(v26.profiles, mid, x, y)
    machine_scan_z_set.set_scan_z(v26.profiles, mid, z)
    v26.apply_active()
    studio._log("Admin: scan position changed", None, "Admin -> Machine -> Scan position", "inside the work area, Z between "
                "the floor and travel Z", f"{before} -> {[x, y, z]}", "changed", "", "Studio Pi admin")
    return dict(scan_position_view(), saved=True, before=before)


# ---------- storage (user 2026-10-17: guard + cleanup) ----------
RECORDING_PATHS = {"/api/studio/scan", "/api/cam/record", "/api/calibration/check", "/api/calibration/reference",
                   "/api/calibration/xy_scan", "/api/calibration/z_scan", "/api/machines/setup_scan"}


def job_done(job):
    return studio.v14.status_of(job) == "done"


def storage_view(_=None):
    return storage.summary(studio.base.OUT_ROOT, job_done)


def storage_settings(params):
    p = params or {}
    v = storage.save_settings(p.get("keep_days", 14), p.get("auto", True))
    studio._log("Admin: storage settings", None, "Admin -> Storage", "keep days, automatic cleanup",
                f"keep {v['keep_days']} days, auto {v['auto']}", "saved", "", "Studio Pi storage")
    return storage_view()


def on_pi():
    return "Raspberry Pi" in pi_model()


def run_cleanup(export=False, who="Admin"):
    if not on_pi():                                         # never delete a laptop's recordings by mistake
        raise ValueError("recording cleanup runs only on the Raspberry Pi")
    s = storage.load_settings()
    stick = None
    if export:
        stick = storage.usb_stick()
        if not stick:
            raise ValueError("no USB stick found - plug one in and try again")
    r = storage.cleanup(studio.base.OUT_ROOT, job_done, s["keep_days"], export_to=stick)
    studio._log(f"Storage cleanup ({who})", None, f"recordings older than {s['keep_days']} days, not used by unfinished jobs",
                "only recordings deleted", f"{r['deleted']} deleted, {r['freed_gb']} GB freed, {r['exported']} exported"
                + (f" to {r['export_dir']}" if r["export_dir"] else "") + (f", {len(r['skipped'])} skipped" if r["skipped"] else ""),
                "done", "", "Studio Pi storage")
    return r


def storage_cleanup(params):
    return dict(run_cleanup(bool((params or {}).get("export"))), storage=storage_view())


_PROGRAM = studio.Handler.POST_API["/api/studio/program"]


def pi_program(params):
    """Every program uses the Admin's paste setup (the operator's Program step cannot change it)."""
    return _PROGRAM[0](paste_setup.apply(params, paste_setup.load()))


# Sideways jog in Admin (user 2026-10-18: "jog is also off in admin panel"). Studio v29 refuses X/Y jogs while the
# last job opened is a real-hand job; in Admin (a valid session - calibrating, nothing under the machine but the dot
# sheet or block) the jog goes to the v28 route below that rule. Every other jog check stays: no-person tick, travel
# height, the machine box, homed, not busy. Without a valid session the v29 rule applies as before.
# Z in Admin (user 2026-10-18: "i want z to move freely under admin panel"): straight up or down by the slider's
# distance between Z 0 and the machine's Z floor, not only down to travel height; X/Y (user: "now x and y should aslo
# move no restriction in admin panel") at any height down to the floor, inside the work area (henna_preview/admin_jog.py).
_REQ = threading.local()
_JOG = studio.Handler.POST_API["/api/machine/jog"]


def z_floor():
    m = v26.profiles.active()
    return float(v26.profiles.motion(m)["firmware_z_floor"])


def pi_jog(params):
    admin = getattr(_REQ, "admin", False)
    # X and Y move at any height in the normal Jog too (user 2026-10-31: "remove it everywhere"; as webui_server_pc);
    # Z keeps the normal rules there, and a real-hand job still refuses sideways jogs (v29).
    if not admin and (params.get("axis") not in ("X", "Y") or studio._ACTIVE["subject"] in studio.NO_SIDEWAYS):
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
                                              "/api/pi/paste_setup": paste_setup_view,
                                              "/api/pi/storage": storage_view,
                                              "/api/pi/jog_limits": jog_limits,
                                              "/api/pi/scan_position": scan_position_view})
    POST_API = dict(studio.Handler.POST_API, **{"/api/pi/servo/try": (servo_try, False),
                                                "/api/pi/servo/save": (servo_save, False),
                                                "/api/pi/paste_setup": (paste_setup_save, False),
                                                "/api/pi/scan_position": (scan_position_save, False),
                                                "/api/pi/admin/status": (admin_status, False),
                                                "/api/pi/admin/unlock": (admin_unlock, False),
                                                "/api/pi/admin/set_pin": (admin_set_pin, False),
                                                "/api/pi/admin/lock": (admin_lock, False),
                                                "/api/pi/storage/settings": (storage_settings, False),
                                                "/api/pi/storage/cleanup": (storage_cleanup, True),
                                                "/api/studio/program": (pi_program, _PROGRAM[1]),
                                                "/api/machine/jog": (pi_jog, _JOG[1])})

    def do_POST(self):
        path = urlparse(self.path).path
        _REQ.admin = path == "/api/machine/jog" and ADMIN.valid(self.headers.get("X-Admin-Token", ""))
        if needs_admin(path) and not ADMIN.valid(self.headers.get("X-Admin-Token", "")):
            if self._host_ok():
                self._send(403, {"error": "Admin is locked - open Admin and enter the PIN"})
            return
        if path in RECORDING_PATHS:
            try:
                storage.guard()
            except storage.StorageError as e:
                if self._host_ok():
                    self._send(400, {"error": str(e)})
                return
        return super().do_POST()

    def do_GET(self):
        if urlparse(self.path).path in PAGES:
            if self._host_ok():
                return self._send(200, pi_page().encode("utf-8"), "text/html; charset=utf-8")
            return
        return super().do_GET()


studio.Handler = PiHandler                                  # v29.main serves studio.Handler


PI_LOG_NAME = "validation_log_pi.csv"


def use_pi_log(root=None):
    """The Pi's own validation log (user 2026-10-29: "separate Pi log"): Studio on the Pi writes here instead of the
    shared validation_log_S2026-10-05-A.csv, which the laptop keeps in git - so "git pull" on the Pi never stops at the
    log again. The file is git-ignored and starts with the shared log's columns. -> its path."""
    from henna_preview.web_log import COLUMNS
    path = os.path.join(root or os.path.dirname(os.path.abspath(__file__)), PI_LOG_NAME)
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(",".join(COLUMNS) + "\n")
    studio.v10.LOG_FILE = path
    return path


def _stop(signum, frame):
    raise KeyboardInterrupt                                 # -> v26.main's safe shutdown (stop a moving machine, disconnect)


if __name__ == "__main__":
    import signal
    # henna_studio.sh starts this server in the background, and a background job starts with SIGINT IGNORED - so its
    # "window closed -> kill -INT" never stopped it (found 2026-10-14: an old server kept running after an update).
    # Both INT and TERM now run the same safe shutdown as Ctrl+C.
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    print(f"Validation log (this Pi's own): {use_pi_log()}", flush=True)
    print("Raspberry Pi: camera in its own process + faster live preview + touch layout", flush=True)
    print(f"Pause button GPIO{PAUSE_BUTTON.state['pin']}: {PAUSE_BUTTON.start()}", flush=True)
    if PASTE_ON_PI:
        print(f"Paste output {PASTE.name}: {PASTE.start()}", flush=True)
    else:
        print("Paste output: the controller's laser TTL output (M3/M5)", flush=True)
    st = storage.load_settings()
    if st["auto"] and on_pi():                                          # old recordings of finished jobs, before anything runs
        try:
            r = run_cleanup(who="automatic, Studio start")
            print(f"Storage cleanup: {r['deleted']} old recordings deleted, {r['freed_gb']} GB freed", flush=True)
        except Exception as e:                              # noqa: BLE001 - never stop Studio for a cleanup
            print(f"Storage cleanup skipped: {e}", flush=True)
    try:
        studio.main()
    finally:
        PASTE.close()
        PAUSE_BUTTON.close()
        studio.v10.camera.close()
