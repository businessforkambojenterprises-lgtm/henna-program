"""Real Z movement from the printed dot sheet's size in the picture - no depth sensor, no ruler (ball-screw 2026-10-13).

The printed sheet's dots are a true 100 mm (rows) and 160 mm (columns) apart (user ruler). For two dots on the
paper, distance_to_paper = true_spacing / |ray1 - ray2| (camera rays from the factory lens model, distortion included),
so the camera-to-paper distance at two head heights gives the real Z movement for the commanded one. The same pictures
also give the depth sensor's own error on this machine (the dot sheet read 4 % small at 13:53).
Studio's page and server CLOSED (port + camera). Uses the Studio v26 machine link as a library: Home (Z first),
Move to scan position, calibration jog Z only, never below Z_LOW.
  python z_optical_check.py
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

Z_LOW = -23.0
PAIRS = (("D1", "D2", 100.0), ("D3", "D5", 100.0), ("D6", "D7", 100.0), ("D1", "D6", 160.0), ("D2", "D7", 160.0),
         ("D3", "D4", 50.0), ("D4", "D5", 50.0))


def grab(pipe, align, frames=15):
    import cv2  # noqa: F401
    cols, deps = [], []
    for _ in range(frames + 10):
        fs = align.process(pipe.wait_for_frames(3000))
        c, d = fs.get_color_frame(), fs.get_depth_frame()
        if c and d:
            cols.append(np.asanyarray(c.get_data()).copy())
            deps.append(np.asanyarray(d.get_data()).copy())
    cols, deps = cols[-frames:], deps[-frames:]
    col = np.median(np.stack(cols), axis=0).astype(np.uint8)
    st = np.stack(deps).astype(float)
    st[st == 0] = np.nan
    dep = np.nan_to_num(np.nanmedian(st, axis=0)).astype(np.uint16)
    return col, dep


def distance(rs, intr, dots):
    by = {d["label"]: d for d in dots}
    out = []
    for a, b, s in PAIRS:
        if a in by and b in by:
            ra = rs.rs2_deproject_pixel_to_point(intr, by[a]["pixel_uv"], 1.0)
            rb = rs.rs2_deproject_pixel_to_point(intr, by[b]["pixel_uv"], 1.0)
            out.append((a, b, s, s / float(np.hypot(ra[0] - rb[0], ra[1] - rb[1]))))
    return out


def main():
    import pyrealsense2 as rs
    import webui_server_v26 as v26
    from henna_preview import calib_session
    m = v26.machine
    vals = v26.apply_active()
    if v26.profiles.active_id() != "ball-screw" or vals is None or Z_LOW < m.cal_floor + 10:
        print("STOP: active machine must be ball-screw in calibration mode")
        return 1

    def wait(what, timeout=180):
        end = time.monotonic() + timeout
        time.sleep(0.3)
        while time.monotonic() < end:
            st = m.status()
            if not st.get("busy") and st.get("state") == "Idle":
                return st
            time.sleep(0.2)
        raise RuntimeError(f"{what}: not Idle after {timeout} s")

    pipe, cfg = rs.pipeline(), rs.config()
    cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 30)
    cfg.enable_stream(rs.stream.color, 848, 480, rs.format.rgb8, 30)
    prof = pipe.start(cfg)
    align = rs.align(rs.stream.color)
    scale = prof.get_device().first_depth_sensor().get_depth_scale()
    ci = prof.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    intr = {"fx": ci.fx, "fy": ci.fy, "ppx": ci.ppx, "ppy": ci.ppy, "width": ci.width, "height": ci.height,
            "model": str(ci.model).split(".")[-1], "coeffs": list(ci.coeffs)}
    res = {"time": dt.datetime.now().isoformat(timespec="seconds"), "heights": {}}
    try:
        for _ in range(30):
            pipe.wait_for_frames(3000)
        m.connect("COM4", wait_s=3.0)
        m.start_homing()
        wait("home")
        m.start_scan_position()
        wait("scan position")
        for z_label in ("high", "low", "high_again"):
            if z_label == "low":
                while m.status()["mpos"][2] - 10 >= Z_LOW - 1e-6:
                    m.start_cal_jog(-1, 10.0)
                    wait("jog down")
            if z_label == "high_again":
                while m.status()["mpos"][2] + 10 <= vals["TRAVEL_Z"] + 1e-6:
                    m.start_cal_jog(1, 10.0)
                    wait("jog up")
            mpos = m.status()["mpos"]
            col, dep = grab(pipe, align)
            frame = {"colour_rgb": col, "depth_raw_aligned": dep, "depth_scale_m_per_unit": scale,
                     "colour_intrinsics": intr}
            dots = calib_session.find_dots(frame)
            pairs = distance(rs, ci, dots)
            dist = [p[3] for p in pairs]
            sensor = float(np.median([d["camera_mm"][2] for d in dots])) if dots else None
            h = {"mpos": mpos, "dots": len(dots), "pairs": [[a, b, s, round(z, 2)] for a, b, s, z in pairs],
                 "optical_distance_mm": round(float(np.mean(dist)), 2) if dist else None,
                 "optical_spread_mm": round(float(np.ptp(dist)), 2) if dist else None,
                 "sensor_depth_at_dots_mm": round(sensor, 2) if sensor else None}
            res["heights"][z_label] = h
            print(z_label, {k: v for k, v in h.items() if k != "pairs"})
            calib_session.overlay(frame, dots, os.path.join(ROOT, "outputs", "machine_checks", "ball-screw",
                                                             f"{dt.datetime.now():%Y%m%d-%H%M%S}_{z_label}.png"),
                                  f"Z {mpos[2]:g}: {len(dots)} dots, optical {h['optical_distance_mm']} mm")
    finally:
        pipe.stop()
        try:
            if not m.status().get("busy"):
                m.disconnect()
        except Exception:
            pass
    H, L = res["heights"]["high"], res["heights"]["low"]
    commanded = H["mpos"][2] - L["mpos"][2]
    real = H["optical_distance_mm"] - L["optical_distance_mm"]
    res.update(commanded_mm=commanded, real_mm_optical=round(real, 2),
               sensor_scale=round(H["optical_distance_mm"] / H["sensor_depth_at_dots_mm"], 4),
               z_steps_now=390.0, z_steps_suggested=round(390.0 * commanded / real, 1))
    out_dir = os.path.join(ROOT, "outputs", "machine_checks", "ball-screw")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, f"{dt.datetime.now():%Y%m%d-%H%M%S}_z_optical.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({k: v for k, v in res.items() if k != "heights"}, indent=1))
    print("saved:", out)
    v26._log("Z optical check (dot-sheet size at two heights)", res["heights"]["high_again"]["mpos"],
             f"z_optical_check.py: Home, scan position, {Z_LOW:g} and back; distance = true dot spacing / ray angle",
             "real Z = commanded", f"optical distance high {H['optical_distance_mm']} (spread {H['optical_spread_mm']}), "
             f"low {L['optical_distance_mm']} (spread {L['optical_spread_mm']}), back {res['heights']['high_again']['optical_distance_mm']}; "
             f"real {res['real_mm_optical']} mm for {commanded:g} commanded -> $102 about {res['z_steps_suggested']}; "
             f"depth sensor reads x{res['sensor_scale']} short of optical", result="recorded",
             recording=os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
