"""Henna Studio for the Raspberry Pi = Studio v27 with the camera in its own process, the faster live preview and a
touch layout for the 5" display.

- henna_preview/camera_process.py: the camera runs in a child process, so a camera-library freeze (2-2.5 s on
  the Pi when the camera starts or stops, stage 3b 2026-10-12) can no longer pause the CNC link and its 1.5 s check.
- henna_preview/web_recorder_fast.py (inside that child): lookup-table colours, a new picture only when fetched.
- webui/pi_touch.css (2026-10-14): added to the page after its own styles - one column, steps as tabs, larger buttons
  and tick boxes for the Touch Display 2 (5", 1280 x 720); raspberry_pi/henna_studio.sh zooms the window x2.
  Layout and sizes only: the same page, routes and checks.
Everything else - page, machine link, checks, recordings and their verification - is v27 unchanged (machine profiles,
per-machine calibration in Studio, travel clearance over the hand). The laptop runs webui_server_v27.py. Started on
the Pi by raspberry_pi/henna_studio.sh (the desktop icon).
PASS on the Pi 2026-10-12 (with v22/v23): stage 3b phase E (worst status age 0.29 s) and 3 real scans, none refused.
v23 -> v27 on 2026-10-14 (merge of main); to be checked on the Pi.
When a new Studio version comes, change the one import below to it (its tests check the swap).
"""
import os
from urllib.parse import urlparse

import webui_server_v27 as studio
from henna_preview.camera_process import ProcessCameraService

PI_CSS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui", "pi_touch.css")
PAGES = ("/", "/index.html", "/studio")

# Every v10-v27 route looks v10.camera up at call time, so this one swap reaches all of them.
studio.v10.camera = ProcessCameraService()


def pi_page():
    """Studio's page with the Pi touch styles added last (read per request: a git pull needs no restart for CSS)."""
    with open(studio.v26.STUDIO_HTML, encoding="utf-8") as f:
        html = f.read()
    with open(PI_CSS, encoding="utf-8") as f:
        css = f.read()
    if "</head>" not in html:
        raise RuntimeError("Studio page has no </head>")
    return html.replace("</head>", f'<style id="pi-touch">\n{css}\n</style>\n</head>', 1)


class PiHandler(studio.Handler):
    server_version = studio.Handler.server_version + "-pi"

    def do_GET(self):
        if urlparse(self.path).path in PAGES:
            if self._host_ok():
                return self._send(200, pi_page().encode("utf-8"), "text/html; charset=utf-8")
            return
        return super().do_GET()


studio.Handler = PiHandler                                  # v27.main serves studio.Handler


if __name__ == "__main__":
    print("Raspberry Pi: camera in its own process + faster live preview + touch layout", flush=True)
    try:
        studio.main()
    finally:
        studio.v10.camera.close()
