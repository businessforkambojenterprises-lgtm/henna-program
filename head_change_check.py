"""Check a calibrated machine after a mechanical change of the head (2026-10-14: ball-screw arm bracket raised).

No ruler, nothing touched: Home (Z first), Move to scan position, one camera picture of the dot sheet that is still
taped where the calibration's nozzle positions were saved. Then
  X/Y: each dot found now -> tool X/Y through the APPROVED X/Y calibration -> compared with the nozzle position saved
       over that dot at calibration time (same sheet, same place). Within ~1 mm = X/Y calibration still valid.
  bed: camera depth of the paper -> machine Z of the paper through the approved Z calibration (k) -> how far the bed
       moved in machine Z (the raise). Valid only if camera and nozzle moved together (same arm).
Studio's page and server CLOSED (port + camera). Uses the Studio v26 machine link as a library.
Result: printed + outputs/machine_checks/<machine>/<time>_head_change.json + one log row.
  python head_change_check.py
"""
import datetime as dt
import json
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from z_optical_check import grab  # noqa: E402

XY_OK_MM = 1.5            # calibration rms 0.77 mm by eye: a dot within 1.5 mm is as good as at calibration


def main():
    import pyrealsense2 as rs
    import webui_server_v26 as v26
    from henna_preview import calib_session
    m = v26.machine
    vals = v26.apply_active()
    prof = v26.profiles.active()
    if not v26.profiles.calibrated(prof):
        print("STOP: the active machine has no approved calibration")
        return 1
    cal = v26.profiles.cal_of(prof)
    with open(os.path.join(ROOT, cal["xy_calibration"]), encoding="utf-8") as f:
        xy = json.load(f)
    with open(os.path.join(ROOT, cal["z_calibration"]), encoding="utf-8") as f:
        zc = json.load(f)
    R, t, k = np.array(xy["result"]["R"]), np.array(xy["result"]["t_mm"]), float(zc["result"]["k_mm"])
    saved = xy["inputs"]["nozzle_points"]
    old_paper_z = float(zc["inputs"]["head"]["z"]) - (float(np.median([d["camera_mm"][2] for d in xy["inputs"]["dots"]])) - k)

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
    p = pipe.start(cfg)
    align = rs.align(rs.stream.color)
    scale = p.get_device().first_depth_sensor().get_depth_scale()
    ci = p.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    intr = {"fx": ci.fx, "fy": ci.fy, "ppx": ci.ppx, "ppy": ci.ppy, "width": ci.width, "height": ci.height,
            "model": str(ci.model).split(".")[-1], "coeffs": list(ci.coeffs)}
    try:
        for _ in range(30):
            pipe.wait_for_frames(3000)
        m.connect("COM4", wait_s=3.0)
        m.start_homing()
        wait("home")
        m.start_scan_position()
        head = wait("scan position")["mpos"]
        col, dep = grab(pipe, align)
    finally:
        pipe.stop()
        try:
            if not m.status().get("busy"):
                m.disconnect()
        except Exception:
            pass
    frame = {"colour_rgb": col, "depth_raw_aligned": dep, "depth_scale_m_per_unit": scale, "colour_intrinsics": intr}
    dots = calib_session.find_dots(frame)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    od = os.path.join(ROOT, "outputs", "machine_checks", prof["id"])
    os.makedirs(od, exist_ok=True)
    calib_session.overlay(frame, dots, os.path.join(od, f"{stamp}_head_change.png"), f"after the head change: {len(dots)} dots")
    rows = []
    for d in dots:
        tool = np.array(head[:2]) + R @ np.array(d["camera_mm"][:2]) + t
        best = min(saved.items(), key=lambda kv: np.hypot(kv[1]["x"] - tool[0], kv[1]["y"] - tool[1]))
        err = np.array([tool[0] - best[1]["x"], tool[1] - best[1]["y"]])
        if np.hypot(*err) < 10:
            rows.append({"dot": best[0], "predicted": [round(float(v), 2) for v in tool], "saved": [best[1]["x"], best[1]["y"]],
                         "error_mm": [round(float(v), 2) for v in err], "error_len_mm": round(float(np.hypot(*err)), 2),
                         "camera_depth_mm": d["camera_mm"][2]})
    paper_z = float(head[2]) - (float(np.median([d["camera_mm"][2] for d in dots])) - k) if dots else None
    errs = [r["error_len_mm"] for r in rows]
    res = {"time": dt.datetime.now().isoformat(timespec="seconds"), "machine": prof["id"], "head": head,
           "dots_found": len(dots), "dots_matched": rows, "xy_rms_mm": round(float(np.sqrt(np.mean(np.square(errs)))), 2) if errs else None,
           "xy_max_mm": max(errs) if errs else None, "mean_shift_mm": [round(float(np.mean([r["error_mm"][i] for r in rows])), 2) for i in (0, 1)] if rows else None,
           "paper_z_now": round(paper_z, 2) if paper_z is not None else None, "paper_z_at_calibration": round(old_paper_z, 2),
           "raise_mm": round(old_paper_z - paper_z, 2) if paper_z is not None else None}
    res["xy_verdict"] = "OK" if errs and len(errs) >= 3 and max(errs) <= XY_OK_MM else "CHECK"
    with open(os.path.join(od, f"{stamp}_head_change.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(json.dumps({k2: v for k2, v in res.items() if k2 != "dots_matched"}, indent=1))
    for r in rows:
        print(r["dot"], "predicted", r["predicted"], "saved", r["saved"], "error", r["error_mm"], r["error_len_mm"])
    v26._log("Head change check (after raising the arm bracket)", head,
             "head_change_check.py: Home, scan position, one dot-sheet picture through the approved calibration",
             f"dots within {XY_OK_MM:g} mm of their saved nozzle positions; paper Z lower by the raise",
             f"{len(rows)} dots matched, rms {res['xy_rms_mm']} max {res['xy_max_mm']} mm, mean shift {res['mean_shift_mm']}; "
             f"paper Z now {res['paper_z_now']} vs {res['paper_z_at_calibration']} at calibration -> raised {res['raise_mm']} mm",
             result=res["xy_verdict"], recording=f"outputs/machine_checks/{prof['id']}/{stamp}_head_change.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
