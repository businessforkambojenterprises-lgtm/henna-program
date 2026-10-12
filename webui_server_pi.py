"""Henna Studio for the Raspberry Pi = Studio v22 with the faster live preview (henna_preview/web_recorder_fast.py).

Everything else - page, machine link, checks, recordings and their verification - is v22 unchanged.
The laptop keeps webui_server_v22.py. Started on the Pi by raspberry_pi/henna_studio_fast.sh.
When a new Studio version comes, change the one import below to it (its tests check the swap).
"""
import webui_server_v22 as studio
from henna_preview.web_recorder_fast import FastCameraService

# Every v10-v22 route looks v10.camera up at call time, so this one swap reaches all of them.
studio.v10.camera = FastCameraService()


if __name__ == "__main__":
    print("Raspberry Pi: faster live preview (lookup-table colours, a new picture only when the page fetches one)",
          flush=True)
    studio.main()
