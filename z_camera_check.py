"""Z check with the camera as the ruler (ball-screw, user request 2026-10-13: "do everything from here").

The D405 rides on the head, so its depth to the paper changes exactly with the real Z movement. This script, with
Studio's page and server CLOSED (it needs the port and the camera), uses the Studio v26 machine link as a library -
the same checked commands as the page: Home (Z first), Move to scan position, calibration jog (Z only, F300 / F100,
never below the machine's floor). Z never goes below Z_LOW. Measurements (median depth of a paper patch, 30 frames):
  d1  after Home + scan position            d2  after a second Home + scan position   -> homing repeatability
  d3  after 3 round trips Z -5 -> Z_LOW -> -5 at F300                                  -> lost steps
  d4  at Z_LOW                              d5  back at -5                              -> real mm for the commanded mm
Camera depth changes are about 2 % short (belt-drive study: g = 0.979, Z verified by caliper); both raw and corrected
numbers are reported. Result: printed + outputs/machine_checks/<machine>/<time>_z_camera.json + log rows.
  python z_camera_check.py
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

Z_LOW = -25.0
G_DEPTH = 1.0                         # 2026-10-14: depth sensor absolute matches the A4 paper width here (182 vs 181 mm); the belt g = 0.979 did not hold on this machine
ROI = (slice(250, 380), slice(320, 500))   # paper between the dots D3-D7, away from the nozzle and the wood


def depth_mm(pipe, scale, frames=30):
    per = []
    for _ in range(frames + 5):
        fs = pipe.wait_for_frames(3000)
        d = np.asanyarray(fs.get_depth_frame().get_data())[ROI].astype(float) * scale * 1000.0
        v = d[d > 0]
        if v.size > 0.3 * d.size:
            per.append(float(np.median(v)))
    per = per[-frames:]
    if len(per) < frames // 2:
        raise RuntimeError("too little depth on the paper patch")
    return round(float(np.median(per)), 2), round(float(np.std(per)), 2)


def main():
    import pyrealsense2 as rs
    import webui_server_v26 as v26
    m = v26.machine
    vals = v26.apply_active()
    mid = v26.profiles.active_id()
    if mid != "ball-screw" or vals is None or Z_LOW < m.cal_floor + 10:
        print("STOP: active machine must be ball-screw in calibration mode with a floor below", Z_LOW - 10)
        return 1
    print(f"machine {mid}: scan position {list(vals['POSE'])}, floor {m.cal_floor}, lowest Z here {Z_LOW}")

    def wait(what, timeout=120):
        end = time.monotonic() + timeout
        time.sleep(0.3)
        while time.monotonic() < end:
            st = m.status()
            if not st.get("busy") and st.get("state") == "Idle":
                return st
            time.sleep(0.2)
        raise RuntimeError(f"{what}: not Idle after {timeout} s - check the machine")

    def jog(direction, step):
        m.start_cal_jog(direction, step)
        return wait(f"jog {direction * step:+g}")

    def go_down_to_low():
        while m.status()["mpos"][2] - 10 >= Z_LOW - 1e-6:
            jog(-1, 10.0)

    def go_up_to_travel():
        while m.status()["mpos"][2] + 10 <= vals["TRAVEL_Z"] + 1e-6:
            jog(1, 10.0)

    readings = {}
    pipe = rs.pipeline()
    cfg = rs.config()
    cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 30)
    prof = pipe.start(cfg)
    scale = prof.get_device().first_depth_sensor().get_depth_scale()
    try:
        for _ in range(30):
            pipe.wait_for_frames(3000)                     # let the exposure settle
        m.connect("COM4", wait_s=3.0)
        for label in ("d1", "d2"):
            m.start_homing()
            wait("home", 180)
            m.start_scan_position()
            st = wait("scan position")
            readings[label] = (st["mpos"], *depth_mm(pipe, scale))
            print(label, readings[label])
        for trip in range(3):
            go_down_to_low()
            go_up_to_travel()
            quick = depth_mm(pipe, scale, frames=10)[0]
            print(f"round trip {trip + 1}: depth {quick}")
            if abs(quick - readings["d2"][1]) > 2.0:           # Z creeping: stop before the tip can reach the paper
                raise RuntimeError(f"Z moved {quick - readings['d2'][1]:+.2f} mm after round trip {trip + 1} - stopped")
        readings["d3"] = (m.status()["mpos"], *depth_mm(pipe, scale))
        print("d3", readings["d3"])
        go_down_to_low()
        readings["d4"] = (m.status()["mpos"], *depth_mm(pipe, scale))
        print("d4", readings["d4"])
        go_up_to_travel()
        readings["d5"] = (m.status()["mpos"], *depth_mm(pipe, scale))
        print("d5", readings["d5"])
    finally:
        pipe.stop()
        try:
            if not m.status().get("busy"):
                m.disconnect()
        except Exception:
            pass

    d = {k: v[1] for k, v in readings.items()}
    commanded = readings["d5"][0][2] - readings["d4"][0][2]
    real_raw = d["d5"] - d["d4"]
    real = real_raw / G_DEPTH
    steps_now = float(sys.argv[sys.argv.index("--steps") + 1]) if "--steps" in sys.argv else 400.0
    res = {"time": dt.datetime.now().isoformat(timespec="seconds"), "machine": mid, "z_low": Z_LOW,
           "readings": {k: {"mpos": v[0], "depth_mm": v[1], "frame_std_mm": v[2]} for k, v in readings.items()},
           "homing_repeat_mm": round(d["d2"] - d["d1"], 2), "after_round_trips_mm": round(d["d3"] - d["d2"], 2),
           "return_mm": round(d["d5"] - d["d3"], 2), "commanded_mm": commanded, "real_mm_raw": round(real_raw, 2),
           "real_mm_corrected": round(real, 2), "z_steps_now": steps_now,
           "z_steps_suggested": round(steps_now * commanded / real, 1) if real else None}
    out_dir = os.path.join(ROOT, "outputs", "machine_checks", mid)
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "_z_camera.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({k: v for k, v in res.items() if k != "readings"}, indent=1))
    print("saved:", out)
    v26._log("Z camera check (homing repeat, lost steps, real Z)", readings["d5"][0],
             f"z_camera_check.py: 2 x Home + scan position, 3 round trips -5 -> {Z_LOW:g} at F300, then {Z_LOW:g} and back",
             "homing and round trips within 0.3 mm; real = commanded",
             "depth d1..d5 " + ", ".join(f"{k} {v}" for k, v in d.items()) +
             f"; homing repeat {res['homing_repeat_mm']}, after round trips {res['after_round_trips_mm']}, "
             f"real {res['real_mm_corrected']} for {commanded:g} commanded (raw {res['real_mm_raw']}, g {G_DEPTH}) -> "
             f"$102 about {res['z_steps_suggested']}", result="recorded",
             recording=os.path.relpath(out, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
