"""Live depth samples from the preview loop, for the hand watch (Studio v28).

DepthSampleMixin goes in front of a camera service class (CameraService on the laptop, FastCameraService on the Pi).
While someone asked for samples in the last WANT_S seconds, every preview frame is also aligned to the colour
camera (as the scans are) and kept as a strided depth array with its colour intrinsics and arrival time. The
preview pictures, recordings and their checks are unchanged.
"""
import time

import numpy as np

from henna_preview.web_recorder import CameraService

STRIDE = 4            # 848 x 480 -> 212 x 120 points, about 25 000 per picture
WANT_S = 2.0
MAX_AGE_S = 1.0


class DepthSampleMixin:
    def _encode(self, fs):
        super()._encode(fs)
        if time.time() < getattr(self, "_want_until", 0.0):
            try:
                self._store_sample(fs)
            except Exception as e:                    # a bad frame must never stop the preview
                self._sample_error = str(e)

    def _store_sample(self, fs):
        import pyrealsense2 as rs
        if getattr(self, "_align", None) is None:
            self._align = rs.align(rs.stream.color)
        a = self._align.process(fs)
        d, c = a.get_depth_frame(), a.get_color_frame()
        if not d or not c:
            return
        ci = c.profile.as_video_stream_profile().get_intrinsics()
        self._sample = {"t": time.time(), "stride": STRIDE, "scale": float(d.get_units()),
                        "depth": np.asanyarray(d.get_data())[::STRIDE, ::STRIDE].copy(),
                        "intr": {"width": ci.width, "height": ci.height, "fx": ci.fx, "fy": ci.fy, "ppx": ci.ppx,
                                 "ppy": ci.ppy, "model": str(ci.model).split(".")[-1], "coeffs": list(ci.coeffs)}}

    def depth_sample(self):
        """The newest sample (at most MAX_AGE_S old) or None; keeps the preview and the sampling running."""
        now = time.time()
        self._want_until = now + WANT_S
        self._last_view = now
        if self.state == "off":
            self.start_preview()
        s = getattr(self, "_sample", None)
        return s if s is not None and time.time() - s["t"] <= MAX_AGE_S else None


class WatchCameraService(DepthSampleMixin, CameraService):
    """The laptop's camera service with depth samples."""
