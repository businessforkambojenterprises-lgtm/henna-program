"""Raspberry Pi port - stage 1 camera check (camera only - never the CNC).

Same stream settings as the laptop's Studio recorder: 848x480 depth z16 + colour rgb8 at 30 fps.
1. Finds the D405 and its USB link (must be USB 3).
2. Streams for 5 s: frame rate, depth units, share of pixels with depth, median depth of the centre.
3. Records 5 s to ~/henna_pi_check/<time>.db3 (like Studio) and plays it back: frame pairs, depth units.
Prints a summary and saves it as JSON next to the recording. Send the printed summary back.
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np
import pyrealsense2 as rs

WIDTH, HEIGHT, FPS = 848, 480, 30
EXPECTED_UNITS = 0.0001
OUT = os.path.join(os.path.expanduser("~"), "henna_pi_check")


def config(record_to=None):
    cfg = rs.config()
    cfg.enable_stream(rs.stream.depth, WIDTH, HEIGHT, rs.format.z16, FPS)
    cfg.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.rgb8, FPS)
    if record_to:
        cfg.enable_record_to_file(record_to)
    return cfg


def main():
    result = {"time": dt.datetime.now().isoformat(timespec="seconds"), "pyrealsense2": rs.__version__, "checks": {}}
    devs = rs.context().query_devices()
    if not devs:
        print("FAIL: no RealSense camera found - check the cable and use a BLUE (USB 3) port")
        return 1
    d = devs[0]
    info = {k: d.get_info(getattr(rs.camera_info, k)) for k in ("name", "serial_number", "firmware_version",
                                                                 "usb_type_descriptor") if d.supports(getattr(rs.camera_info, k))}
    result["camera"] = info
    print("Camera:", info)
    usb3 = str(info.get("usb_type_descriptor", "")).startswith("3")
    result["checks"]["usb3"] = usb3

    pipe = rs.pipeline()
    prof = pipe.start(config())
    units = prof.get_device().first_depth_sensor().get_depth_scale()
    n, t0, valid, medians = 0, time.time(), [], []
    while time.time() - t0 < 5.0:
        fs = pipe.wait_for_frames(3000)
        dep = fs.get_depth_frame()
        if not dep or not fs.get_color_frame():
            continue
        n += 1
        a = np.asanyarray(dep.get_data())
        valid.append(float((a > 0).mean()))
        c = a[HEIGHT // 3: 2 * HEIGHT // 3, WIDTH // 3: 2 * WIDTH // 3]
        c = c[c > 0]
        if c.size:
            medians.append(float(np.median(c)) * units * 1000)
    pipe.stop()
    fps = n / 5.0
    live = {"frame_pairs_5s": n, "fps": round(fps, 1), "depth_units_m": units,
            "valid_depth_share": round(float(np.mean(valid)), 3) if valid else 0.0,
            "centre_median_depth_mm": round(float(np.median(medians)), 1) if medians else None}
    result["live"] = live
    result["checks"]["fps_ok"] = fps >= 25
    result["checks"]["depth_units_ok"] = abs(units - EXPECTED_UNITS) < 1e-7
    print("Live stream:", live)

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, dt.datetime.now().strftime("%Y%m%d_%H%M%S") + ".db3")
    pipe = rs.pipeline()
    pipe.start(config(path))
    t0 = time.time()
    while time.time() - t0 < 5.0:
        pipe.wait_for_frames(3000)
    pipe.stop()
    del pipe
    time.sleep(1.0)

    cfg = rs.config()
    cfg.enable_device_from_file(path, repeat_playback=False)
    pipe = rs.pipeline()
    prof = pipe.start(cfg)
    playback = prof.get_device().as_playback()
    playback.set_real_time(False)
    p_units = prof.get_device().first_depth_sensor().get_depth_scale()
    pairs = 0
    while True:
        ok, fs = pipe.try_wait_for_frames(2000)
        if not ok:
            break
        if fs.get_depth_frame() and fs.get_color_frame():
            pairs += 1
    pipe.stop()
    rec = {"file": path, "size_mb": round(os.path.getsize(path) / 1e6, 1), "frame_pairs": pairs,
           "playback_depth_units_m": p_units}
    result["recording"] = rec
    result["checks"]["recording_frames_ok"] = pairs >= 60
    result["checks"]["recording_units_ok"] = abs(p_units - EXPECTED_UNITS) < 1e-7
    print("Recording:", rec)
    if abs(p_units - 0.001) < 1e-7:
        print("Note: playback says 0.001 - the known recorder issue Studio's recorder already fixes (stage 2).")

    passed = all(result["checks"].values())
    result["verdict"] = "PASS" if passed else "CHECK"
    with open(path.replace(".db3", "_check.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    print("\n=== STAGE 1 RESULT:", result["verdict"], "===")
    for k, v in result["checks"].items():
        print(f"  {'OK ' if v else 'NO '} {k}")
    print(json.dumps(result))
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
