"""Faster live preview for slow computers (Raspberry Pi 5) - the recording itself is unchanged.

Measured on the Pi 5 (stage 3a, 2026-10-12): Studio's preview took 48 ms per frame (every depth pixel coloured
in float, then the picture shrunk) and coloured EVERY camera frame (about 27 per second), while the page fetches
one picture every 0.4 s - most of that work was thrown away and it kept about one CPU core busy.
This version:
1. colours with a 65536-entry table built from the same _depth_rgb (identical colours) on a half-size picture
   (6.2 ms per frame on the Pi; same 848x240 JPEG as before);
2. makes a new picture only after the page fetched the previous one (and the very first one).
record() is inherited unchanged: same streams, same .db3 file, same repair and verification. Only the preview
pictures made while recording use the faster colouring too. web_recorder.py is unchanged.
Used on the Pi through webui_server_pi.py; the laptop keeps the original preview.
"""
import numpy as np

from henna_preview.web_recorder import CameraService, _depth_rgb


def depth_lut():
    """65536 x 3 uint8 table: lut[raw depth] == _depth_rgb(raw depth) for every possible value."""
    return _depth_rgb(np.arange(65536, dtype=np.uint16).reshape(1, -1))[0]


class FastCameraService(CameraService):
    def __init__(self):
        super().__init__()
        self._lut = depth_lut()
        self._wanted = True          # the page asked for a picture that has not been made yet

    def jpeg(self):
        self._wanted = True
        return super().jpeg()

    def _encode(self, fs):
        if not self._wanted and self._jpeg is not None:
            return                   # nobody fetched the last picture yet: skip this frame
        import cv2
        d, c = fs.get_depth_frame(), fs.get_color_frame()
        if not d or not c:
            return
        self._wanted = False         # cleared before encoding, so a fetch during encoding asks again
        rgb = np.asanyarray(c.get_data())[::2, ::2]
        dep = self._lut[np.asanyarray(d.get_data())[::2, ::2]]
        ok, buf = cv2.imencode(".jpg", np.hstack([rgb, dep])[:, :, ::-1], [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            self._jpeg = buf.tobytes()
