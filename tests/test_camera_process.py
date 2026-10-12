"""Camera in its own process (henna_preview/camera_process.py), with a fake camera in a real child process."""
import os
import sys
import threading
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from henna_preview.camera_process import ProcessCameraService  # noqa: E402
from henna_preview.web_recorder import CameraError  # noqa: E402

FAKE = os.path.join(HERE, "fake_camera_service.py") + ":FakeCamera"


class ProcessCameraTests(unittest.TestCase):
    def make(self, **kw):
        cam = ProcessCameraService(service_spec=FAKE, **kw)
        self.addCleanup(cam.close)
        return cam

    def test_runs_in_another_process(self):
        cam = self.make()
        st = cam.status()
        self.assertEqual(st["state"], "off")
        self.assertNotEqual(st["pid"], os.getpid())
        self.assertEqual(cam.start_preview()["state"], "preview")
        self.assertEqual(cam.jpeg(), b"JPEG")
        self.assertEqual(cam.stop()["state"], "off")
        info = cam.record("x.db3", 0.2)
        self.assertEqual((info["frame_pairs"], info["path"]), (136, "x.db3"))

    def test_studio_keeps_running_and_sees_recording(self):
        cam = self.make()
        cam.status()                                 # child started
        ticks, stop = [], threading.Event()

        def ticker():
            while not stop.is_set():
                ticks.append(time.monotonic())
                time.sleep(0.02)

        threading.Thread(target=ticker, daemon=True).start()
        t = threading.Thread(target=cam.record, args=("x.db3", 1.0))
        t.start()
        time.sleep(0.3)
        self.assertEqual(cam.status()["state"], "recording")
        t.join()
        stop.set()
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        self.assertLess(max(gaps), 0.3)
        self.assertEqual(cam.status()["state"], "off")

    def test_recording_reported_even_when_the_child_does_not_answer(self):
        cam = self.make(call_timeout=0.5)
        cam.status()
        t = threading.Thread(target=cam.record, args=("x.slowstatus", 2.0))
        t.start()
        time.sleep(0.3)
        st = cam.status()                            # the child's status hangs 3 s -> timeout here
        self.assertEqual(st["state"], "recording")
        self.assertIn("did not answer", st["error"])
        t.join()

    def test_errors_come_back(self):
        cam = self.make()
        with self.assertRaisesRegex(CameraError, "refused"):
            cam.record("x.fail", 0.1)
        with self.assertRaisesRegex(RuntimeError, "ValueError"):
            cam.record("x.boom", 0.1)
        self.assertEqual(cam.status()["state"], "off")

    def test_child_crash_fails_the_call_and_restarts(self):
        cam = self.make()
        first = cam.status()["pid"]
        with self.assertRaisesRegex(CameraError, "stopped"):
            cam.record("x.crash", 0.1)
        second = cam.status()["pid"]
        self.assertNotEqual(first, second)
        self.assertEqual(cam.record("x.db3", 0.1)["frame_pairs"], 136)

    def test_stop_does_not_start_a_process(self):
        cam = self.make()
        self.assertEqual(cam.stop()["state"], "off")
        self.assertIsNone(cam.pid)

    def test_close_ends_the_child(self):
        cam = self.make()
        cam.status()
        proc = cam._proc
        cam.close()
        self.assertIsNotNone(proc.poll())


if __name__ == "__main__":
    unittest.main()
