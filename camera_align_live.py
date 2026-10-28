"""Live camera alignment against the daily-check reference, in a terminal (user 2026-10-28: the camera was loose -
"which direction to move camera?"). The same measurement as Admin -> Daily check -> Camera alignment
(henna_preview/camera_align.py). Reads Studio's live picture once a second (Studio running, head at the scan position,
bed as at the reference). Read-only: it moves nothing. Ctrl+C to stop.
  .venv\\Scripts\\python.exe camera_align_live.py
Picture down = machine +X, picture right = machine +Y. Aim for under 0.3 mm, then tighten and run the daily check.
"""
import os
import sys
import time
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from henna_preview import camera_align  # noqa: E402

URL = "http://127.0.0.1:8765/api/cam/preview.jpg?t={}"
LABEL = {"ok": "OK - tighten it here", "close": "close", "move": "move"}


def grab():
    return camera_align.preview_gray(urllib.request.urlopen(URL.format(time.time()), timeout=3).read())


def main():
    from henna_preview.machines import Profiles
    ref = camera_align.load_reference(camera_align.reference_path(ROOT, Profiles(ROOT).active_id()))
    said = False
    print("Live camera alignment - picture down = machine +X, right = machine +Y. Ctrl+C to stop.\n")
    hist = []
    while True:
        try:
            r = camera_align.measure(ref, grab())
        except Exception:                              # noqa: BLE001 - Studio not running / restarting: wait
            if not said:
                print("Waiting for Henna Studio... start it (desktop shortcut), Connect, then Get ready / Move to "
                      "scan position.")
                said = True
            time.sleep(1.5)
            continue
        hist = (hist + [(r["x_px"], r["y_px"])])[-3:]
        x, y = np.mean(hist, axis=0)
        j = camera_align.judge(x, y)
        print(f"\r  shift {j['shift_mm']:4.2f} mm  (picture x {x:+5.1f} px, y {y:+5.1f} px)  {LABEL[j['state']]:22s} "
              f"{'; '.join(j['tips']):60s}", end="", flush=True)
        time.sleep(0.8)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)
