"""Live D405 preview and recording for the web page (record only - no controller, no G-code).

Recordings are written like RealSense Viewer recordings (848x480 depth z16 + colour rgb8, 30 fps,
.db3 in the approved Documents folder) so every existing step reads them unchanged.

Why fix_late_option_repeats exists: pyrealsense2 2.58 writes all 29 sensor options at the start
of a recording (depth units 0.0001 included), then re-writes 'Error Polling Enabled' with the same
value when the camera stops. Playback then exposes only that one option and falls back to depth
units 0.001 - every depth would read 10x too far. Removing late option messages that only REPEAT
the starting value makes the file read exactly like a Viewer recording. A late message with a
DIFFERENT value is a real change and is never removed (the recording is refused instead).
verify_recording then checks the file before anything may use it.
"""
import gc
import os
import sqlite3
import threading
import time

import numpy as np

EXPECTED_DEPTH_UNITS = 0.0001
MIN_OPTIONS = 20
WIDTH, HEIGHT, FPS = 848, 480, 30
PREVIEW_IDLE_STOP_S = 30.0


class CameraError(RuntimeError):
    """Camera busy, missing, or a recording that cannot be trusted."""


def fix_late_option_repeats(path):
    """Delete option value messages written after the first message that repeat that option's start value.

    -> list of (option name, seconds after start) removed. Raises CameraError for a real change.
    """
    con = sqlite3.connect(path)
    try:
        t0 = con.execute("select min(timestamp) from messages").fetchone()[0]
        if t0 is None:
            raise CameraError("recording is empty")
        topics = con.execute("select id, name from topics where name like '/device_%/sensor_%/option/%/value'").fetchall()
        removed, ids = [], []
        for tid, name in topics:
            rows = con.execute("select id, timestamp, data from messages where topic_id=? order by timestamp, id",
                               (tid,)).fetchall()
            if not rows:
                continue
            first = bytes(rows[0][2])
            for mid, ts, data in rows[1:]:
                if bytes(data) != first:
                    raise CameraError(f"camera option {name.split('/')[-2]} changed during the recording - not used")
                ids.append(mid)
                removed.append((name.split("/")[-2], round((ts - t0) / 1e9, 3)))
        con.executemany("delete from messages where id=?", [(i,) for i in ids])
        con.commit()
        return removed
    finally:
        con.close()


def verify_recording(path, expected_units=EXPECTED_DEPTH_UNITS):
    """Playback check: depth units, options present, frame counts and plausible depths. Raises CameraError."""
    import pyrealsense2 as rs
    from henna_preview.recording import count_frames_db3, read_frame

    cfg = rs.config()
    cfg.enable_device_from_file(path, repeat_playback=False)
    pipe = rs.pipeline()
    prof = pipe.start(cfg)
    try:
        ds = prof.get_device().first_depth_sensor()
        n_opts = len(ds.get_supported_options())
        units = float(ds.get_depth_scale())
    finally:
        pipe.stop()
        # the playback device holds the file open until it is freed - free it now, or a refused scan cannot be
        # renamed to .rejected on Windows (2026-10-26: WinError 32 hid "only 19 frame pairs recorded")
        ds = prof = pipe = cfg = None
        gc.collect()
    if abs(units - expected_units) > expected_units * 1e-3:
        raise CameraError(f"recording says depth units {units:g} m, expected {expected_units:g} m - not used")
    if n_opts < MIN_OPTIONS:
        raise CameraError(f"recording exposes only {n_opts} camera settings - not used")
    counts = count_frames_db3(path)
    pairs = min(counts.values()) if counts else 0
    if pairs < 60:
        raise CameraError(f"only {pairs} frame pairs recorded - record longer")
    fr = read_frame(path, pairs // 2)
    d = fr["depth_raw_aligned"]
    v = d[d > 0].astype(float) * fr["depth_scale_m_per_unit"] * 1000.0
    if v.size < 0.2 * d.size:
        raise CameraError("less than 20% of the frame has depth - check the light and the view")
    med = float(np.median(v))
    if not 100.0 <= med <= 400.0:
        raise CameraError(f"median depth {med:.0f} mm is not plausible for the capture pose - not used")
    return {"depth_units_m": units, "camera_settings": n_opts, "frame_counts": counts, "frame_pairs": pairs,
            "median_depth_mm": round(med, 1), "middle_frame": pairs // 2}


def reject(path):
    """A refused recording -> path + '.rejected' (kept for inspection, never listed). Never raises: a file Windows
    still holds open is retried once after freeing objects, then left as it is - the scan's own error must be the
    message the operator sees, not the rename's. -> the new path, or None."""
    if not os.path.exists(path):
        return None
    for attempt in range(2):
        try:
            os.replace(path, path + ".rejected")
            return path + ".rejected"
        except OSError:
            gc.collect()
            time.sleep(0.5)
    return None


def _depth_rgb(depth, lo=120.0, hi=230.0, units=EXPECTED_DEPTH_UNITS):
    """Depth (raw) -> RGB for the preview only (near = yellow, far = purple, black = no depth)."""
    mm = depth.astype(np.float32) * units * 1000.0
    t = np.clip((hi - mm) / (hi - lo), 0, 1)
    stops = np.array([[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]], np.float32)
    x = t * (len(stops) - 1)
    k = np.minimum(x.astype(int), len(stops) - 2)
    f = (x - k)[..., None]
    rgb = stops[k] * (1 - f) + stops[k + 1] * f
    rgb[depth == 0] = 0
    return rgb.astype(np.uint8)


class CameraService:
    """One camera user at a time: a preview loop, or a recording. Thread-safe."""

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._jpeg = None
        self._last_view = 0.0
        self._error = None
        self.state = "off"   # off | preview | recording

    # ---- preview ----
    def start_preview(self):
        with self._lock:
            if self.state != "off":
                self._last_view = time.time()
                return self.status()
            self._error = None
            self._stop.clear()
            self._last_view = time.time()
            self._thread = threading.Thread(target=self._preview_loop, daemon=True)
            self.state = "preview"
            self._thread.start()
        time.sleep(1.0)
        return self.status()

    def stop(self):
        t = None
        with self._lock:
            if self.state == "preview":
                self._stop.set()
                t = self._thread
        if t:
            t.join(timeout=5)
        return self.status()

    def jpeg(self):
        self._last_view = time.time()
        return self._jpeg

    def status(self):
        return {"state": self.state, "error": self._error, "has_frame": self._jpeg is not None}

    def _pipeline(self, record_to=None):
        import pyrealsense2 as rs
        if not rs.context().query_devices():
            raise CameraError("no RealSense camera found - check the USB cable")
        cfg = rs.config()
        cfg.enable_stream(rs.stream.depth, WIDTH, HEIGHT, rs.format.z16, FPS)
        cfg.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.rgb8, FPS)
        if record_to:
            cfg.enable_record_to_file(record_to)
        pipe = rs.pipeline()
        try:
            prof = pipe.start(cfg)
        except RuntimeError as e:
            raise CameraError(f"camera could not start ({e}) - close RealSense Viewer and try again")
        units = prof.get_device().first_depth_sensor().get_depth_scale()
        if abs(units - EXPECTED_DEPTH_UNITS) > EXPECTED_DEPTH_UNITS * 1e-3:
            pipe.stop()
            raise CameraError(f"camera depth units are {units:g} m, expected {EXPECTED_DEPTH_UNITS:g} - "
                              "set 'Depth Units' to 0.0001 in RealSense Viewer")
        return pipe

    def _encode(self, fs):
        import cv2
        d, c = fs.get_depth_frame(), fs.get_color_frame()
        if not d or not c:
            return
        rgb = np.asanyarray(c.get_data())
        dep = _depth_rgb(np.asanyarray(d.get_data()))
        both = np.hstack([rgb, dep])[:, :, ::-1]
        both = cv2.resize(both, (both.shape[1] // 2, both.shape[0] // 2))
        ok, buf = cv2.imencode(".jpg", both, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            self._jpeg = buf.tobytes()

    def _preview_loop(self):
        pipe = None
        try:
            pipe = self._pipeline()
            while not self._stop.is_set():
                if time.time() - self._last_view > PREVIEW_IDLE_STOP_S:
                    break  # nobody is watching: free the camera for RealSense Viewer
                ok, fs = pipe.try_wait_for_frames(2000)
                if ok:
                    self._encode(fs)
        except Exception as e:  # reported through status()
            self._error = str(e)
        finally:
            if pipe:
                try:
                    pipe.stop()
                except RuntimeError:
                    pass
            del pipe
            gc.collect()
            with self._lock:
                if self.state == "preview":
                    self.state = "off"

    # ---- recording ----
    def record(self, out_path, seconds):
        """Stop the preview, record `seconds` to out_path, fix and verify the file. -> verification dict."""
        if not 3.0 <= seconds <= 10.0:
            raise CameraError("record between 3 and 10 seconds")
        if os.path.exists(out_path):
            raise CameraError("a recording with this name already exists")
        self.stop()
        with self._lock:
            if self.state != "off":
                raise CameraError("camera is busy")
            self.state = "recording"
        pipe = None
        try:
            pipe = self._pipeline(record_to=out_path)
            t0, n = time.time(), 0
            while time.time() - t0 < seconds:
                ok, fs = pipe.try_wait_for_frames(2000)
                if ok:
                    n += 1
                    if n % 6 == 0:
                        self._encode(fs)
            pipe.stop()
            del pipe
            pipe = None
            gc.collect()
            time.sleep(0.5)  # let the recorder close the file
        finally:
            if pipe:
                try:
                    pipe.stop()
                except RuntimeError:
                    pass
            with self._lock:
                self.state = "off"
        removed = fix_late_option_repeats(out_path)
        info = verify_recording(out_path)
        info["late_repeated_option_messages_removed"] = removed
        info["frames_seen_while_recording"] = n
        return info
