"""The camera in its own process (Raspberry Pi): a camera-library freeze can no longer pause Studio.

Measured on the Pi 5 (stage 3b, 2026-10-12): starting or stopping the D405 paused the WHOLE Studio process for
2.0-2.5 s (the camera library holds Python's interpreter lock; most pauses last exactly its 2 s frame-wait
timeout), so the CNC status went stale (> 1.5 s) and Studio rightly refused the scan. The camera and the CNC were
on different USB controllers - the pause was Python, not USB.

Here the camera (FastCameraService) runs in a child process. Studio's process - with the CNC link and its 1.5 s
check - only talks to it through a pipe, so a camera freeze can no longer stop it from reading the machine.
ProcessCameraService has the methods Studio uses: start_preview, stop, jpeg, status, record, and (Studio v28)
depth_sample - live aligned depth for the real-hand watch (henna_preview/depth_sample.py), made in the child too.
- The recording, its repair and its verification run unchanged in the child (CameraService.record).
- While record() runs, status() says 'recording' from Studio's side even if the child is busy or frozen, so
  homing, moving to the scan position, jogging and runs stay refused during a scan (as with CameraService).
- If the child stops, the waiting call fails with CameraError and the next call starts a new child.
Child:  python -m henna_preview.camera_process [--service file.py:Class]   (the option is for tests only)
"""
import importlib.util
import itertools
import os
import pickle
import struct
import subprocess
import sys
import threading

from henna_preview.web_recorder import CameraError

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METHODS = ("start_preview", "stop", "jpeg", "status", "record", "depth_sample")
OFF = {"state": "off", "error": None, "has_frame": False}


def _send(stream, obj):
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    stream.write(struct.pack(">I", len(data)) + data)
    stream.flush()


def _recv(stream):
    head = stream.read(4)
    if len(head) < 4:
        return None
    n = struct.unpack(">I", head)[0]
    data = stream.read(n)
    return pickle.loads(data) if len(data) == n else None


# ---------- child ----------
def _load_service(spec):
    if not spec:
        from henna_preview.depth_sample import DepthSampleMixin
        from henna_preview.web_recorder_fast import FastCameraService

        class FastWatchCameraService(DepthSampleMixin, FastCameraService):
            """The Pi's faster preview + depth samples for the hand watch (v28)."""

        return FastWatchCameraService()
    path, cls = spec.rsplit(":", 1)
    s = importlib.util.spec_from_file_location("camera_service_under_test", path)
    mod = importlib.util.module_from_spec(s)
    s.loader.exec_module(mod)
    return getattr(mod, cls)()


def serve(service_spec=None):
    """Answer requests from stdin on stdout, each in its own thread (status/jpeg are answered during a recording)."""
    rx, tx = sys.stdin.buffer, sys.stdout.buffer
    sys.stdout = sys.stderr                  # a print must never mix into the reply channel
    svc = _load_service(service_spec)
    lock = threading.Lock()

    def handle(rid, method, args):
        try:
            if method not in METHODS:
                raise CameraError(f"unknown camera call {method}")
            reply = (rid, True, getattr(svc, method)(*args))
        except CameraError as e:
            reply = (rid, False, ("CameraError", str(e)))
        except Exception as e:
            reply = (rid, False, (type(e).__name__, str(e)))
        with lock:
            try:
                _send(tx, reply)
            except OSError:
                pass

    while True:
        msg = _recv(rx)
        if msg is None:                      # Studio closed the pipe (stopped or crashed)
            break
        threading.Thread(target=handle, args=msg).start()
    try:
        svc.stop()                           # a recording that is still running finishes first (non-daemon)
    except Exception:
        pass


# ---------- Studio's side ----------
class ProcessCameraService:
    def __init__(self, service_spec=None, call_timeout=5.0, start_timeout=30.0, record_extra_s=240.0):
        self._spec = service_spec
        self._call_timeout, self._start_timeout, self._record_extra_s = call_timeout, start_timeout, record_extra_s
        self._lock = threading.Lock()
        self._proc = None
        self._waiters = {}
        self._ids = itertools.count(1)
        self._recording = 0
        self._last_status = dict(OFF)
        self._last_jpeg = None

    @staticmethod
    def _alive(proc):
        # gone = its reply pipe closed, even if the OS has not reported the exit yet
        return proc is not None and proc.poll() is None and not getattr(proc, "henna_gone", False)

    @property
    def pid(self):
        return self._proc.pid if self._alive(self._proc) else None

    def _ensure_locked(self):
        if self._alive(self._proc):
            return self._proc
        cmd = [sys.executable, "-m", "henna_preview.camera_process"] + (["--service", self._spec] if self._spec else [])
        self._proc = proc = subprocess.Popen(cmd, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        threading.Thread(target=self._receiver, args=(proc,), daemon=True).start()
        return proc

    def _receiver(self, proc):
        while True:
            try:
                msg = _recv(proc.stdout)
            except Exception:
                msg = None
            if msg is None:
                break
            rid, ok, val = msg
            w = self._waiters.pop(rid, None)
            if w is not None:
                w[1] = (ok, val)
                w[0].set()
        proc.henna_gone = True
        for rid, w in list(self._waiters.items()):     # the child is gone: fail what it still owed
            if w[2] is proc and self._waiters.pop(rid, None) is not None:
                w[1] = (False, ("CameraError", "the camera process stopped - try again"))
                w[0].set()

    def _call(self, method, *args, timeout):
        with self._lock:
            proc = self._ensure_locked()
            rid = next(self._ids)
            w = [threading.Event(), None, proc]
            self._waiters[rid] = w
            try:
                _send(proc.stdin, (rid, method, args))
            except OSError as e:
                self._waiters.pop(rid, None)
                raise CameraError(f"the camera process is not reachable ({e}) - try again")
        if not w[0].wait(timeout):
            self._waiters.pop(rid, None)
            raise CameraError(f"the camera process did not answer '{method}' within {timeout:g} s")
        ok, val = w[1]
        if ok:
            return val
        kind, text = val
        if kind == "CameraError":
            raise CameraError(text)
        raise RuntimeError(f"{kind}: {text}")

    def status(self):
        try:
            st = self._call("status", timeout=self._call_timeout)
            self._last_status = st
        except Exception as e:
            st = dict(self._last_status, error=str(e))
        if self._recording:
            st = dict(st, state="recording")         # decided here, not by the (possibly busy) child
        return st

    def jpeg(self):
        try:
            j = self._call("jpeg", timeout=self._call_timeout)
        except Exception:
            return self._last_jpeg
        if j is not None:
            self._last_jpeg = j
        return j

    def start_preview(self):
        return self._call("start_preview", timeout=self._start_timeout)

    def depth_sample(self):
        """The child's newest depth sample or None (a stuck child = None: the hand watch then holds the machine)."""
        try:
            return self._call("depth_sample", timeout=self._start_timeout if self.pid is None else self._call_timeout)
        except Exception:
            return None

    def stop(self):
        if self.pid is None:
            return dict(OFF)                         # never start a camera process just to stop it
        return self._call("stop", timeout=self._start_timeout)

    def record(self, out_path, seconds):
        with self._lock:
            self._recording += 1
        try:
            return self._call("record", out_path, seconds, timeout=float(seconds) + self._record_extra_s)
        finally:
            with self._lock:
                self._recording -= 1

    def close(self, timeout=20.0):
        """End the child: it stops the camera (finishing a running recording first) and exits."""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.stdin.close()
            proc.wait(timeout)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    serve(sys.argv[sys.argv.index("--service") + 1] if "--service" in sys.argv else None)
