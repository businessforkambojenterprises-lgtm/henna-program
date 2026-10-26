"""Median depth over several consecutive frames of a recording (.bag or .db3).

Why: on a flat block one frame's depth wobbles by about +/-1 mm from pixel to pixel,
and the nozzle path followed that wobble. The per-pixel median over N frames removes
most of it.

Rules (nothing is filled, interpolated or smoothed across pixels):
  * per pixel, only frames that measured it count (raw 0 = missing);
  * measured in fewer than min_valid_frac of the frames -> UNSTABLE -> unusable;
  * frame-to-frame spread (median absolute deviation) above max_mad_mm -> NOISY -> unusable;
  * unusable pixels come back as 0 in depth_raw_aligned, exactly like missing depth.

Offline playback only: the file is opened read-only and no live camera is used.
"""
import os
import time

import numpy as np

from henna_preview.depth_surface import VALID, classify_depth, raw_to_mm, status_counts
from henna_preview.recording import (RECORDING_EXT, RecordingError, check_frame_index, intrinsics_dict,
                                     require_streams)


def nanmedian0(a):
    """np.nanmedian(a, axis=0), the same numbers (NaN where a pixel has none), ~3x faster: one sort along the
    frames (NaN sorts last), then the middle value or the mean of the two middle ones (2026-10-26: numpy's masked
    median was ~2.6 s of a 4 s scan read)."""
    srt = np.sort(a, axis=0)
    n = np.isfinite(a).sum(axis=0)
    lo = np.take_along_axis(srt, np.maximum((n - 1) // 2, 0)[None], 0)[0]
    hi = np.take_along_axis(srt, np.maximum(n // 2, 0)[None], 0)[0]
    out = np.where(n % 2 == 1, lo, (lo + hi) / 2.0)
    out[n == 0] = np.nan
    return out


def median_depth(stack_raw, depth_scale_m, min_valid_frac=0.8, max_mad_mm=1.0):
    """stack_raw (N, H, W) raw units, 0 = missing -> (median raw (H, W), 0 where unusable; status; info)."""
    s = np.asarray(stack_raw, dtype=np.float64)
    if s.ndim != 3 or s.shape[0] < 1:
        raise RecordingError("need a stack of depth frames shaped (N, H, W)")
    m = np.where(s > 0, s, np.nan)
    valid_frac = np.isfinite(m).mean(axis=0)
    med = nanmedian0(m)
    mad = nanmedian0(np.abs(m - med))
    status = classify_depth(raw_to_mm(med, depth_scale_m), valid_frac, raw_to_mm(mad, depth_scale_m),
                            min_valid_frac, max_mad_mm)
    out = np.where(status == VALID, med, 0.0)
    usable = status == VALID
    info = {"frames": int(s.shape[0]), "min_valid_frac": min_valid_frac, "max_mad_mm": max_mad_mm,
            "pixel_status": status_counts(status),
            "mad_mm_median_of_usable": (round(float(np.median(raw_to_mm(mad[usable], depth_scale_m))), 3)
                                        if usable.any() else None)}
    return out, status, info


# The same median read is needed by several steps of one job (Studio's path check, the design check, the program,
# the hand watch) - each read the recording again (2026-10-26: "read the file only once ... share"). The result is
# kept in CACHE_DIR, named by the recording's path, size and modification time and the window and thresholds, so a
# different or changed file is never mixed up; separate processes (step 22, step 23) share it the same way.
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs", "frame_cache")
CACHE_KEEP = 40
_ARRAYS = ("depth_raw_aligned", "depth_status", "colour_rgb", "depth_raw_single_middle")


def _cache_path(path, start, count, min_valid_frac, max_mad_mm):
    import hashlib
    st = os.stat(path)
    key = f"v1|{os.path.normcase(path)}|{st.st_size}|{st.st_mtime_ns}|{start}|{count}|{min_valid_frac!r}|{max_mad_mm!r}"
    return os.path.join(CACHE_DIR, hashlib.sha1(key.encode("utf-8")).hexdigest() + ".npz")


def _cache_load(cpath):
    import json
    try:
        with np.load(cpath, allow_pickle=False) as z:
            out = {k: z[k] for k in _ARRAYS}
            out.update(json.loads(str(z["meta_json"])))
        return out
    except (OSError, KeyError, ValueError):
        return None


def _cache_save(cpath, fr):
    import json
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        meta = {k: v for k, v in fr.items() if k not in _ARRAYS}
        tmp = cpath[:-4] + f".{os.getpid()}.tmp.npz"
        np.savez(tmp, meta_json=json.dumps(meta), **{k: fr[k] for k in _ARRAYS})
        os.replace(tmp, cpath)
        old = sorted((os.path.join(CACHE_DIR, f) for f in os.listdir(CACHE_DIR) if f.endswith(".npz")),
                     key=os.path.getmtime)
        for f in old[:-CACHE_KEEP]:
            os.remove(f)
    except (OSError, TypeError, ValueError):
        pass                                             # no cache: the next read plays the file again


def read_frames_median(path, start, count, min_valid_frac=0.8, max_mad_mm=1.0):
    """Play the recording (not real-time) and median `count` aligned depth frames from pair `start`.

    Returns a frame dict shaped like recording.read_frame (colour and metadata of the middle
    frame) whose depth_raw_aligned is the median depth, plus depth_raw_single_middle (that one
    frame's own depth, for comparison) and 'multi' (window, thresholds, pixel status counts).
    A read of the same file, window and thresholds done before comes from CACHE_DIR.
    """
    path = os.path.abspath(path)
    if not path.lower().endswith(RECORDING_EXT):
        raise RecordingError("only .bag or .db3 recordings are accepted")
    if not os.path.isfile(path):
        raise RecordingError("recording not found")
    check_frame_index(start)
    if not isinstance(count, int) or count < 1:
        raise RecordingError("frame count must be a whole number of 1 or more")
    cpath = _cache_path(path, start, count, min_valid_frac, max_mad_mm)
    fr = _cache_load(cpath) if os.path.isfile(cpath) else None
    if fr is not None:
        return fr
    fr = _read_frames_median(path, start, count, min_valid_frac, max_mad_mm)
    _cache_save(cpath, fr)
    return fr


def _read_frames_median(path, start, count, min_valid_frac, max_mad_mm):
    import pyrealsense2 as rs

    t0 = time.perf_counter()
    cfg = rs.config()
    cfg.enable_device_from_file(path, repeat_playback=False)
    pipe = rs.pipeline()
    profile = pipe.start(cfg)
    try:
        dev = profile.get_device()
        dev.as_playback().set_real_time(False)
        require_streams([s.stream_name() for s in profile.get_streams()])
        depth_scale = float(dev.first_depth_sensor().get_depth_scale())
        align = rs.align(rs.stream.color)
        mid = start + count // 2
        stack, numbers, middle = [], [], None
        k = -1
        while len(stack) < count:
            ok, fs = pipe.try_wait_for_frames(3000)
            if not ok:
                raise RecordingError(f"recording ended after {k + 1} frame pairs; "
                                     f"frames {start}..{start + count - 1} not available")
            d_raw, c_raw = fs.get_depth_frame(), fs.get_color_frame()
            if not d_raw or not c_raw:
                continue
            k += 1
            if k < start:
                continue
            aligned = align.process(fs)
            stack.append(np.asanyarray(aligned.get_depth_frame().get_data()).copy())
            numbers.append([int(d_raw.get_frame_number()), int(c_raw.get_frame_number())])
            if k == mid:
                c_prof = c_raw.get_profile().as_video_stream_profile()
                fmt = c_prof.format()
                colour = np.asanyarray(aligned.get_color_frame().get_data()).copy()
                if fmt == rs.format.bgr8:
                    colour = colour[:, :, ::-1].copy()
                elif fmt != rs.format.rgb8:
                    raise RecordingError(f"colour format {fmt} is not supported (need RGB8 or BGR8)")
                middle = {"colour_rgb": colour, "colour_intrinsics": intrinsics_dict(c_prof.get_intrinsics()),
                          "depth_raw_single_middle": stack[-1].copy(),
                          "frame": {"depth_frame_number": int(d_raw.get_frame_number()),
                                    "colour_frame_number": int(c_raw.get_frame_number()),
                                    "timestamp_domain": str(d_raw.get_frame_timestamp_domain()).split(".")[-1]}}
    finally:
        pipe.stop()
    if len({tuple(n) for n in numbers}) != len(numbers):
        raise RecordingError("the same frame was delivered twice; cannot build an honest median")
    med, status, info = median_depth(np.stack(stack), depth_scale, min_valid_frac, max_mad_mm)
    info.update({"start_pair": start, "middle_pair": mid, "frame_numbers_depth_colour": numbers})
    return {"path": path, "frame_index": mid, "depth_raw_aligned": med, "depth_status": status,
            "depth_scale_m_per_unit": depth_scale, **middle, "multi": info,
            "read_time_s": time.perf_counter() - t0}
