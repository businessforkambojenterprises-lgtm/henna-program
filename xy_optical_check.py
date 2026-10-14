"""Real X and Y movement measured by the camera on the printed dot sheet (ball-screw, 2026-10-14).

The camera rides on the head. When the head moves in X or Y at a fixed height, every dot on the paper shifts in the
picture by the same real distance. The camera-to-paper distance D comes from the sheet itself (true dot spacing /
ray angle, as in z_optical_check.py), so shift_mm = D x |ray shift| - no depth sensor and no ruler.
Moves (Studio v26 machine link as a library, the same checked commands as the page; Studio's page and server CLOSED):
Home (Z first), Move to scan position, then normal Jog at travel Z only: X +DX and back, Y +DY and back.
Result: printed + outputs/machine_checks/<machine>/<time>_xy_optical.json + one log row.
  python xy_optical_check.py
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from z_optical_check import distance, grab  # noqa: E402

STEPS = (403.5, 409.6)       # $100, $101 on the controller now (2026-10-14)
DX, DY = 20.0, 40.0          # machine X moves the dots along the picture's short side; Y along its long side


def shift_mm(rs, intr, d_mm, a, b):
    """Most common ray shift between two dot lists (the dots that stayed in view), times D."""
    ra = [rs.rs2_deproject_pixel_to_point(intr, d["pixel_uv"], 1.0)[:2] for d in a]
    rb = [rs.rs2_deproject_pixel_to_point(intr, d["pixel_uv"], 1.0)[:2] for d in b]
    cands = [np.subtract(q, p) for p in ra for q in rb]
    tol = 2.0 / d_mm                                           # 2 mm on the paper
    best = max(cands, key=lambda c: sum(np.hypot(*(c - o)) < tol for o in cands))
    near = [c for c in cands if np.hypot(*(c - best)) < tol]
    v = np.mean(near, axis=0) * d_mm
    return float(np.hypot(*v)), [round(float(x), 2) for x in v], len(near)


def main():
    import pyrealsense2 as rs
    import webui_server_v26 as v26
    from henna_preview import calib_session
    m = v26.machine
    vals = v26.apply_active()
    if v26.profiles.active_id() != "ball-screw" or vals is None:
        print("STOP: active machine must be ball-screw")
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

    def jog(axis, mm):
        for _ in range(int(round(abs(mm) / 10))):
            m.start_jog(axis, 1 if mm > 0 else -1, 10.0)
            wait(f"jog {axis}")

    pipe, cfg = rs.pipeline(), rs.config()
    cfg.enable_stream(rs.stream.depth, 848, 480, rs.format.z16, 30)
    cfg.enable_stream(rs.stream.color, 848, 480, rs.format.rgb8, 30)
    prof = pipe.start(cfg)
    align = rs.align(rs.stream.color)
    scale = prof.get_device().first_depth_sensor().get_depth_scale()
    ci = prof.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    intr = {"fx": ci.fx, "fy": ci.fy, "ppx": ci.ppx, "ppy": ci.ppy, "width": ci.width, "height": ci.height,
            "model": str(ci.model).split(".")[-1], "coeffs": list(ci.coeffs)}
    caps = {}

    def capture(label):
        col, dep = grab(pipe, align)
        frame = {"colour_rgb": col, "depth_raw_aligned": dep, "depth_scale_m_per_unit": scale, "colour_intrinsics": intr}
        dots = calib_session.find_dots(frame)
        caps[label] = {"mpos": m.status()["mpos"], "dots": dots}
        print(label, caps[label]["mpos"], len(dots), "dots")

    try:
        for _ in range(30):
            pipe.wait_for_frames(3000)
        m.connect("COM4", wait_s=3.0)
        m.start_homing()
        wait("home")
        m.start_scan_position()
        wait("scan position")
        capture("start")
        jog("X", DX)
        capture("x_moved")
        jog("X", -DX)
        capture("x_back")
        jog("Y", DY)
        capture("y_moved")
        jog("Y", -DY)
        capture("y_back")
    finally:
        pipe.stop()
        try:
            if not m.status().get("busy"):
                m.disconnect()
        except Exception:
            pass

    pairs = distance(rs, ci, caps["start"]["dots"])
    if len(caps["start"]["dots"]) != 7 or not pairs:
        print("STOP: the start picture needs all 7 dots for the camera distance")
        return 2
    d_print = float(np.mean([p[3] for p in pairs]))
    # 2026-10-14: the printed sheet is ~3 % small (A4 paper width), so the yardstick is the depth sensor (matches the paper)
    d_mm = float(np.median([d["camera_mm"][2] for d in caps["start"]["dots"]]))
    out = {"time": dt.datetime.now().isoformat(timespec="seconds"), "camera_to_paper_mm": round(d_mm, 2),
           "camera_to_paper_from_print_mm": round(d_print, 2), "yardstick": "depth sensor", "moves": {}}
    for name, a, b, cmd in (("X", "start", "x_moved", DX), ("X back", "x_moved", "x_back", DX),
                            ("Y", "start", "y_moved", DY), ("Y back", "y_moved", "y_back", DY)):
        real, vec, n = shift_mm(rs, ci, d_mm, caps[a]["dots"], caps[b]["dots"])
        out["moves"][name] = {"commanded_mm": cmd, "real_mm": round(real, 2), "camera_vector_mm": vec, "dots_matched": n}
        print(f"{name}: commanded {cmd:g}, real {real:.2f} mm (camera {vec}, {n} dots)")
    now = {100: STEPS[0], 101: STEPS[1]}
    for axis, k in (("X", 100), ("Y", 101)):
        reals = [out["moves"][axis]["real_mm"], out["moves"][axis + " back"]["real_mm"]]
        cmd = out["moves"][axis]["commanded_mm"]
        out[f"${k}_now"] = now[k]
        out[f"${k}_suggested"] = round(now[k] * cmd / float(np.mean(reals)), 1)
    out["return_error_mm"] = {"X": round(shift_mm(rs, ci, d_mm, caps["start"]["dots"], caps["x_back"]["dots"])[0], 2),
                              "Y": round(shift_mm(rs, ci, d_mm, caps["start"]["dots"], caps["y_back"]["dots"])[0], 2)}
    od = os.path.join(ROOT, "outputs", "machine_checks", "ball-screw")
    os.makedirs(od, exist_ok=True)
    path = os.path.join(od, f"{dt.datetime.now():%Y%m%d-%H%M%S}_xy_optical.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(json.dumps({k: v for k, v in out.items() if k != "moves"}, indent=1))
    print("saved:", path)
    mv = out["moves"]
    v26._log("X/Y optical check (dot-sheet shift)", caps["y_back"]["mpos"],
             f"xy_optical_check.py: Home, scan position, Jog X +{DX:g} and back, Y +{DY:g} and back at travel Z",
             "real = commanded; back within 0.1 mm",
             f"camera-to-paper {out['camera_to_paper_mm']} mm; X {mv['X']['real_mm']}/{mv['X back']['real_mm']} for {DX:g}, "
             f"Y {mv['Y']['real_mm']}/{mv['Y back']['real_mm']} for {DY:g}; return error {out['return_error_mm']}; "
             f"$100 about {out['$100_suggested']}, $101 about {out['$101_suggested']} (now {STEPS[0]:g} / {STEPS[1]:g}; yardstick depth sensor)",
             result="recorded", recording=os.path.relpath(path, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
