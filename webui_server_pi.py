"""Henna Studio for the Raspberry Pi = Studio v22 with the camera in its own process and the faster live preview.

- henna_preview/camera_process.py: the camera runs in a child process, so a camera-library freeze (2-2.5 s on
  the Pi when the camera starts or stops, stage 3b 2026-10-12) can no longer pause the CNC link and its 1.5 s check.
- henna_preview/web_recorder_fast.py (inside that child): lookup-table colours, a new picture only when fetched.
Everything else - page, machine link, checks, recordings and their verification - is v22 unchanged.
The laptop keeps webui_server_v22.py. Started on the Pi by raspberry_pi/henna_studio.sh (the desktop icon).
PASS on the Pi 2026-10-12: stage 3b phase E (worst status age 0.29 s) and 3 real scans, none refused.
When a new Studio version comes, change the one import below to it (its tests check the swap).
"""
import webui_server_v22 as studio
from henna_preview.camera_process import ProcessCameraService

# Every v10-v22 route looks v10.camera up at call time, so this one swap reaches all of them.
studio.v10.camera = ProcessCameraService()


if __name__ == "__main__":
    print("Raspberry Pi: camera in its own process + faster live preview", flush=True)
    try:
        studio.main()
    finally:
        studio.v10.camera.close()
