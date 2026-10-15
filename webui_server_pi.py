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
- henna_preview/paste_gpio.py (2026-10-15, user: "B"): the paste SSR on GPIO27 (pin 13) to GND (pin 14) - Studio
  v29's paste ON / OFF switch this pin instead of the controller's output; GET /api/pi/paste_output.
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
from henna_preview.camera_process import ProcessCameraService
from henna_preview.paste_gpio import GpioPasteOutput
from henna_preview.pause_button import PauseButton

PI_CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "pi_touch.css")
PI_JS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "pi_touch.js")
PAGES = ("/", "/index.html", "/studio")

# Every v10-v29 route looks v10.camera up at call time, so this one swap reaches all of them.
studio.v10.camera = ProcessCameraService()
PAUSE_BUTTON = PauseButton(studio.machine, studio._log)
PASTE = GpioPasteOutput()
studio.machine.paste_output = PASTE                         # not ready until started: paste refused until then


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
                                              "/api/pi/paste_output": PASTE.view})

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
    print(f"Paste output {PASTE.name}: {PASTE.start()}", flush=True)
    try:
        studio.main()
    finally:
        PASTE.close()
        PAUSE_BUTTON.close()
        studio.v10.camera.close()
