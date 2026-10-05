"""Read one colour + depth frame pair from a RealSense recording (.bag or .db3).

Offline playback only: the file is opened read-only and no live camera is used.
Depth is aligned to the colour image with the RealSense SDK's own align
processing block, so both arrays share the colour pixel grid.
"""
import os
import pathlib
import sqlite3
import time

import numpy as np

RECORDING_EXT = (".bag", ".db3")


class RecordingError(ValueError):
    """Raised for unusable recordings or frame requests."""


def check_frame_index(index, count=None):
    if not isinstance(index, int) or isinstance(index, bool):
        raise RecordingError("frame index must be a whole number")
    if index < 0:
        raise RecordingError("frame index must be 0 or more")
    if count is not None and index >= count:
        raise RecordingError(f"frame {index} is past the end (this recording has {count} frames: 0..{count - 1})")


def require_streams(stream_names):
    missing = [s for s in ("Depth", "Color") if s not in stream_names]
    if missing:
        raise RecordingError("recording has no " + " and no ".join(missing) + " stream")


def count_frames_db3(path):
    """Exact per-stream image counts for a .db3 recording, read-only. Returns {stream: count}."""
    uri = pathlib.Path(path).resolve().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        rows = con.execute(
            "select t.name, count(*) from messages m join topics t on t.id = m.topic_id "
            "where t.name like '%/image/data' group by t.name").fetchall()
    finally:
        con.close()
    out = {}
    for name, n in rows:
        stream = name.split("/")[3].split("_")[0]  # /device_0/sensor_0/Color_0/image/data -> Color
        out[stream] = out.get(stream, 0) + n
    return out


def frame_pair_count(path):
    """Number of usable colour+depth pairs if it can be known without playback, else None."""
    if path.lower().endswith(".db3"):
        c = count_frames_db3(path)
        return min(c.get("Depth", 0), c.get("Color", 0))
    return None


def intrinsics_dict(i):
    return {"width": i.width, "height": i.height, "fx": i.fx, "fy": i.fy, "ppx": i.ppx, "ppy": i.ppy,
            "model": str(i.model).split(".")[-1], "coeffs": [float(c) for c in i.coeffs]}


def read_frame(path, index):
    """Play the recording (not real-time) up to colour+depth pair `index` and return copies of it."""
    import pyrealsense2 as rs

    path = os.path.abspath(path)
    if not path.lower().endswith(RECORDING_EXT):
        raise RecordingError("only .bag or .db3 recordings are accepted")
    if not os.path.isfile(path):
        raise RecordingError("recording not found")
    check_frame_index(index)

    t0 = time.perf_counter()
    cfg = rs.config()
    cfg.enable_device_from_file(path, repeat_playback=False)
    pipe = rs.pipeline()
    profile = pipe.start(cfg)
    try:
        dev = profile.get_device()
        dev.as_playback().set_real_time(False)
        streams = [s.stream_name() for s in profile.get_streams()]
        require_streams(streams)
        depth_scale = float(dev.first_depth_sensor().get_depth_scale())
        info = {}
        for key in ("name", "serial_number", "firmware_version"):
            ci = getattr(rs.camera_info, key)
            if dev.supports(ci):
                info[key] = dev.get_info(ci)
        align = rs.align(rs.stream.color)

        k = -1
        while True:
            ok, fs = pipe.try_wait_for_frames(3000)
            if not ok:
                raise RecordingError(f"recording ended after {k + 1} frame pairs; frame {index} not available")
            d_raw, c_raw = fs.get_depth_frame(), fs.get_color_frame()
            if not d_raw or not c_raw:
                continue
            k += 1
            if k < index:
                continue
            d_prof = d_raw.get_profile().as_video_stream_profile()
            c_prof = c_raw.get_profile().as_video_stream_profile()
            ext = d_prof.get_extrinsics_to(c_prof)
            aligned = align.process(fs)
            d, c = aligned.get_depth_frame(), aligned.get_color_frame()
            colour = np.asanyarray(c.get_data()).copy()
            fmt = c_prof.format()
            if fmt == rs.format.bgr8:
                colour = colour[:, :, ::-1].copy()
            elif fmt != rs.format.rgb8:
                raise RecordingError(f"colour format {fmt} is not supported (need RGB8 or BGR8)")
            depth_raw = np.asanyarray(d.get_data()).copy()
            return {
                "path": path,
                "frame_index": index,
                "colour_rgb": colour,
                "depth_raw_aligned": depth_raw,
                "depth_scale_m_per_unit": depth_scale,
                "colour_intrinsics": intrinsics_dict(c_prof.get_intrinsics()),
                "depth_intrinsics_before_alignment": intrinsics_dict(d_prof.get_intrinsics()),
                "extrinsics_depth_to_colour": {"rotation": [float(v) for v in ext.rotation],
                                               "translation_m": [float(v) for v in ext.translation]},
                "frame": {
                    "depth_frame_number": int(d_raw.get_frame_number()),
                    "colour_frame_number": int(c_raw.get_frame_number()),
                    "depth_timestamp_ms": float(d_raw.get_timestamp()),
                    "colour_timestamp_ms": float(c_raw.get_timestamp()),
                    "timestamp_domain": str(d_raw.get_frame_timestamp_domain()).split(".")[-1],
                },
                "streams": {"depth": f"{d_prof.width()}x{d_prof.height()} {str(d_prof.format()).split('.')[-1]} @ {d_prof.fps()} fps",
                            "colour": f"{c_prof.width()}x{c_prof.height()} {str(fmt).split('.')[-1]} @ {c_prof.fps()} fps"},
                "device": info,
                "read_time_s": time.perf_counter() - t0,
            }
    finally:
        pipe.stop()
