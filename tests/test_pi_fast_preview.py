"""Faster Pi preview (web_recorder_fast + webui_server_pi): same colours, same picture, made only when fetched."""
import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.web_recorder import CameraService, _depth_rgb  # noqa: E402
from henna_preview.web_recorder_fast import FastCameraService, depth_lut  # noqa: E402


class _Frame:
    def __init__(self, a):
        self.a = a

    def get_data(self):
        return self.a


class _FrameSet:
    def __init__(self, depth, color):
        self.d, self.c = _Frame(depth), _Frame(color)

    def get_depth_frame(self):
        return self.d

    def get_color_frame(self):
        return self.c


def frames():
    """Smooth test scene: a depth ramp 120-230 mm (raw 0.0001 m units) with a no-depth hole, a colour gradient."""
    y, x = np.mgrid[0:480, 0:848]
    depth = (1200 + (x * 1100 // 848)).astype(np.uint16)
    depth[200:260, 300:400] = 0
    color = np.dstack([x * 255 // 848, y * 255 // 480, np.full_like(x, 128)]).astype(np.uint8)
    return _FrameSet(depth, color)


class FastPreviewTests(unittest.TestCase):
    def test_lut_is_studio_colouring(self):
        d = np.random.default_rng(2).integers(0, 65536, (60, 70), dtype=np.uint16)
        np.testing.assert_array_equal(depth_lut()[d], _depth_rgb(d))

    def test_same_picture_as_studio(self):
        fs = frames()
        old, new = CameraService(), FastCameraService()
        old._encode(fs)
        new._encode(fs)
        a = cv2.imdecode(np.frombuffer(old._jpeg, np.uint8), cv2.IMREAD_COLOR)
        b = cv2.imdecode(np.frombuffer(new._jpeg, np.uint8), cv2.IMREAD_COLOR)
        self.assertEqual(a.shape, b.shape)
        self.assertEqual(a.shape, (240, 848, 3))
        self.assertLess(float(np.abs(a.astype(int) - b.astype(int)).mean()), 4.0)

    def test_new_picture_only_after_a_fetch(self):
        cam, fs = FastCameraService(), frames()
        cam._encode(fs)
        first = cam._jpeg
        self.assertIsNotNone(first)
        cam._encode(fs)
        self.assertIs(cam._jpeg, first, "no fetch yet: the frame is skipped")
        self.assertIs(cam.jpeg(), first)
        cam._encode(fs)
        self.assertIsNot(cam._jpeg, first, "after a fetch the next frame makes a new picture")

    def test_recording_is_inherited_unchanged(self):
        self.assertIs(FastCameraService.record, CameraService.record)
        self.assertIs(FastCameraService._pipeline, CameraService._pipeline)


class PiServerTests(unittest.TestCase):
    def test_pi_server_swaps_only_the_camera(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            import webui_server_pi
            import webui_server_v28 as v28
            from henna_preview.camera_process import ProcessCameraService
            self.assertIsInstance(v10.camera, ProcessCameraService)
            self.assertIsNone(v10.camera.pid, "no camera process until Studio uses the camera")
            self.assertIs(webui_server_pi.studio, v28)
            self.assertIs(v28.v10, v10)
        finally:
            v10.camera = original


    def test_pi_page_gets_the_touch_layout_only(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            import webui_server_pi as pi
            with open(pi.studio.STUDIO_HTML, encoding="utf-8") as f:
                page = f.read()
            got = pi.pi_page()
            self.assertIn('<style id="pi-touch">', got)
            self.assertIn("--force-device-scale-factor", open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "raspberry_pi",
                                                                            "henna_studio.sh"),
                                                               encoding="utf-8").read())
            self.assertIn('<script id="pi-touch-js">', got)
            css = got[got.index('<style id="pi-touch">'):got.index("</head>")]
            js = got[got.index('<script id="pi-touch-js">'):got.rindex("</body>")]
            self.assertEqual(got.replace(css, "").replace(js, ""), page)          # nothing else changed
            self.assertIs(pi.studio.Handler, pi.PiHandler)              # served by v28's main
            self.assertTrue(issubclass(pi.PiHandler, pi.studio.v26.Handler))
        finally:
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)                   # the swap test imports it fresh

    def test_stop_signal_runs_the_safe_shutdown(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            import webui_server_pi as pi
            with self.assertRaises(KeyboardInterrupt):                 # INT/TERM -> v26.main's finally
                pi._stop(15, None)
            src = open(pi.__file__, encoding="utf-8").read()
            self.assertIn("signal.signal(signal.SIGINT, _stop)", src)
            self.assertIn("signal.signal(signal.SIGTERM, _stop)", src)
        finally:
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)

if __name__ == "__main__":
    unittest.main()
