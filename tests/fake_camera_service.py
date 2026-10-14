"""A stand-in camera for tests of henna_preview/camera_process.py (runs inside the child process; no hardware).

record(path, seconds) behaves by the path's ending: '.crash' ends the process, '.fail' raises CameraError,
'.boom' raises ValueError, '.slowstatus' makes status() hang 3 s while it records; otherwise it sleeps `seconds`.
"""
import os
import threading
import time

from henna_preview.web_recorder import CameraError


class FakeCamera:
    def __init__(self):
        self.state = "off"
        self.slow_status = threading.Event()

    def status(self):
        if self.slow_status.is_set():
            time.sleep(3.0)
        return {"state": self.state, "error": None, "has_frame": self.state != "off", "pid": os.getpid()}

    def start_preview(self):
        self.state = "preview"
        return self.status()

    def stop(self):
        if self.state == "preview":
            self.state = "off"
        return self.status()

    def jpeg(self):
        return b"JPEG" if self.state != "off" else None

    def depth_sample(self):
        import numpy as np
        self.state = "preview"
        return {"t": time.time(), "stride": 4, "scale": 0.0001, "depth": np.full((3, 4), 1700, np.uint16), "intr": {}}

    def record(self, out_path, seconds):
        if out_path.endswith(".crash"):
            os._exit(3)
        if out_path.endswith(".fail"):
            raise CameraError("recording refused (test)")
        if out_path.endswith(".boom"):
            raise ValueError("unexpected (test)")
        if out_path.endswith(".slowstatus"):
            self.slow_status.set()
        self.state = "recording"
        try:
            time.sleep(seconds)
        finally:
            self.state = "off"
            self.slow_status.clear()
        return {"frame_pairs": 136, "pid": os.getpid(), "path": out_path}
