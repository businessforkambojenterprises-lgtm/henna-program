"""Collect hand scans with the D405 on a STAND - no machine (user 2026-10-26: "What if i give you hand scan of 40
hands?" -> "for this scan i'm only mounting d405 on a stand" -> "yes build it").

Set-up: D405 looking straight down, about 20-22 cm above the table (as at the machine's scan height), the hand palm up
on the same jig piece or a similar board, steady light. CLOSE HENNA STUDIO first (only one program can use the camera).
Ask every person first; the scans stay on this laptop (outputs/hand_scans/).

  .venv\\Scripts\\python.exe hand_scan_collect.py

For each hand: type an ID (no names, e.g. P07), L or R, an optional note. A window shows the camera live:
  SPACE  record (2 s, averaged)      Q / Esc  back to the next ID (or quit)
Then it shows the result - Y keep, R record again, N discard. Kept scans: outputs/hand_scans/<ID>_<L|R>[_n].npz
(averaged aligned depth, colour, intrinsics, landmarks, measurements) and one row in outputs/hand_scans/index.csv.
Measurements (henna_preview/hand_scan_stats.py): heights above the table (fingertips, finger bases, palm, heel),
hand length, palm width, missing depth, the table fit and the camera's tilt - with warnings when a scan is poor.
"""
import csv
import datetime as dt
import json
import os
import re
import sys

import numpy as np

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "outputs", "hand_scans")
FRAMES = 60                                               # 2 s at 30 fps
ID_RE = re.compile(r"^[A-Za-z0-9-]{1,20}$")
INDEX_COLS = ["file", "id", "side", "time", "note", "ok", "problems", "hand_length_mm", "palm_width_mm",
              "fingertip_min_mm", "fingertip_max_mm", "palm_centre_mm", "heel_mm", "hand_min_mm", "hand_max_mm",
              "missing_depth_pct", "camera_to_table_mm", "table_tilt_deg"]


def open_camera():
    import pyrealsense2 as rs
    from henna_preview.web_recorder import EXPECTED_DEPTH_UNITS, FPS, HEIGHT, WIDTH
    if not rs.context().query_devices():
        raise SystemExit("No RealSense camera found - check the USB cable (and that Henna Studio is closed).")
    cfg = rs.config()
    cfg.enable_stream(rs.stream.depth, WIDTH, HEIGHT, rs.format.z16, FPS)
    cfg.enable_stream(rs.stream.color, WIDTH, HEIGHT, rs.format.rgb8, FPS)
    pipe = rs.pipeline()
    try:
        prof = pipe.start(cfg)
    except RuntimeError as e:
        raise SystemExit(f"The camera could not start ({e}) - close Henna Studio and RealSense Viewer.")
    dev = prof.get_device()
    units = dev.first_depth_sensor().get_depth_scale()
    if abs(units - EXPECTED_DEPTH_UNITS) > EXPECTED_DEPTH_UNITS * 1e-3:
        pipe.stop()
        raise SystemExit(f"Depth units are {units:g} m, expected {EXPECTED_DEPTH_UNITS:g} - set them in RealSense Viewer.")
    intr = prof.get_stream(rs.stream.color).as_video_stream_profile().get_intrinsics()
    intr_d = {"width": intr.width, "height": intr.height, "fx": intr.fx, "fy": intr.fy, "ppx": intr.ppx,
              "ppy": intr.ppy, "model": str(intr.model).split(".")[-1], "coeffs": list(intr.coeffs)}
    serial = dev.get_info(rs.camera_info.serial_number)
    return pipe, rs.align(rs.stream.color), units, intr_d, serial


def grab(pipe, align):
    fs = align.process(pipe.wait_for_frames())
    return np.asanyarray(fs.get_depth_frame().get_data()).copy(), np.asanyarray(fs.get_color_frame().get_data()).copy()


def record(pipe, align, n=FRAMES):
    """n aligned frames -> (median depth raw uint16 over the frames with depth at each pixel, the middle colour)."""
    depths, colours = [], []
    for _ in range(n):
        d, c = grab(pipe, align)
        depths.append(d)
        colours.append(c)
    stack = np.stack(depths).astype(np.float32)
    stack[stack == 0] = np.nan
    with np.errstate(all="ignore"):
        med = np.nanmedian(stack, axis=0)
    return np.nan_to_num(med, nan=0).astype(np.uint16), colours[n // 2]


def preview(depth_raw, colour, units, text):
    import cv2
    d = depth_raw.astype(float) * units * 1000.0
    col = np.zeros(d.shape + (3,), np.uint8)
    ok = d > 0
    if ok.any():
        lo, hi = np.percentile(d[ok], [2, 98])
        col[ok] = cv2.applyColorMap(np.clip(255 * (1 - (d[ok] - lo) / max(1.0, hi - lo)), 0, 255).astype(np.uint8)
                                    .reshape(-1, 1), cv2.COLORMAP_VIRIDIS).reshape(-1, 3)
    img = np.hstack([cv2.cvtColor(colour, cv2.COLOR_RGB2BGR), col])
    for i, line in enumerate(text):
        cv2.putText(img, line, (12, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
        cv2.putText(img, line, (12, 28 + 26 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return img


def summary_lines(m):
    tips = [v for v in m["fingertips_mm"].values() if v is not None]
    lines = [f"length {m['hand_length_mm']} mm, palm width {m['palm_width_mm']} mm",
             f"above the table: fingertips {min(tips) if tips else '-'}-{max(tips) if tips else '-'} mm, "
             f"palm {m['palm_centre_mm']} mm, heel {m['heel_mm']} mm",
             f"missing depth {m['missing_depth_in_hand_pct']} %, camera {m['camera_to_table_mm']} mm above the table, "
             f"tilt {m['table_tilt_deg']} deg"]
    return lines + ([f"WARNING: {p}" for p in m["problems"]] or ["scan OK"])


def free_name(ident, side):
    base = f"{ident}_{side}"
    name, n = base, 1
    while os.path.exists(os.path.join(OUT, name + ".npz")):
        n += 1
        name = f"{base}_{n}"
    return name


def save(name, ident, side, note, depth_raw, colour, units, intr, serial, hand, m):
    os.makedirs(OUT, exist_ok=True)
    when = dt.datetime.now().isoformat(timespec="seconds")
    meta = {"id": ident, "side": side, "note": note, "time": when, "serial": serial, "depth_units_m": units,
            "intrinsics": intr, "handedness": hand["handedness_label"], "handedness_score": hand["handedness_score"],
            "measurements": m, "setup": "D405 on a stand, looking down; no machine (hand_scan_collect.py)"}
    np.savez_compressed(os.path.join(OUT, name + ".npz"), depth_raw=depth_raw, colour_rgb=colour,
                        landmarks_px=np.asarray(hand["landmarks_px"], float), meta_json=json.dumps(meta))
    tips = [v for v in m["fingertips_mm"].values() if v is not None]
    row = {"file": name + ".npz", "id": ident, "side": side, "time": when, "note": note, "ok": m["ok"],
           "problems": "; ".join(m["problems"]), "hand_length_mm": m["hand_length_mm"],
           "palm_width_mm": m["palm_width_mm"], "fingertip_min_mm": min(tips) if tips else "",
           "fingertip_max_mm": max(tips) if tips else "", "palm_centre_mm": m["palm_centre_mm"], "heel_mm": m["heel_mm"],
           "hand_min_mm": m["hand_height_mm"]["min"], "hand_max_mm": m["hand_height_mm"]["max"],
           "missing_depth_pct": m["missing_depth_in_hand_pct"], "camera_to_table_mm": m["camera_to_table_mm"],
           "table_tilt_deg": m["table_tilt_deg"]}
    index = os.path.join(OUT, "index.csv")
    new = not os.path.isfile(index)
    with open(index, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=INDEX_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


def ask_person():
    while True:
        ident = input("\nID for this hand (e.g. P07, no names) - Enter alone to quit: ").strip()
        if not ident:
            return None
        if not ID_RE.match(ident):
            print("  letters, digits and - only, up to 20")
            continue
        side = input("  L or R hand: ").strip().upper()[:1]
        if side not in ("L", "R"):
            print("  type L or R")
            continue
        note = input("  note (optional, e.g. rings, curled fingers): ").strip()[:120]
        return ident, side, note


def main():
    import cv2
    from henna_preview import hand_detect, hand_scan_stats
    hand_detect.verify_model()
    pipe, align, units, intr, serial = open_camera()
    print(f"Camera {serial} ready. Scans go to {OUT}")
    try:
        while True:
            person = ask_person()
            if person is None:
                break
            ident, side, note = person
            while True:
                d, c = grab(pipe, align)
                cv2.imshow("hand scan", preview(d, c, units, [f"{ident} {side}: SPACE record, Q next/quit"]))
                k = cv2.waitKey(1) & 0xFF
                if k in (ord("q"), 27):
                    break
                if k != ord(" "):
                    continue
                cv2.imshow("hand scan", preview(d, c, units, ["recording - keep the hand still ..."]))
                cv2.waitKey(1)
                depth_raw, colour = record(pipe, align)
                hands = hand_detect.detect_hands(colour)
                if not hands:
                    lines, m, hand = ["no hand found - R record again, N skip"], None, None
                else:
                    hand = hands[0]
                    try:
                        m = hand_scan_stats.measure(depth_raw.astype(float) * units * 1000.0, intr, hand["landmarks_px"])
                        lines = summary_lines(m) + ["Y keep, R record again, N discard"]
                    except ValueError as e:
                        lines, m = [f"cannot measure: {e}", "R record again, N discard"], None
                img = preview(depth_raw, colour, units, lines)
                if hand:
                    for u, v in hand["landmarks_px"]:
                        cv2.circle(img, (int(u), int(v)), 3, (0, 255, 255), -1)
                cv2.imshow("hand scan", img)
                for line in lines:
                    print("  " + line)
                while True:
                    k = cv2.waitKey(0) & 0xFF
                    if k in (ord("y"), ord("r"), ord("n"), 27):
                        break
                if k == ord("y") and m is not None:
                    name = free_name(ident, side)
                    save(name, ident, side, note, depth_raw, colour, units, intr, serial, hand, m)
                    print(f"  saved {name}.npz")
                    break
                if k == ord("r"):
                    continue
                print("  discarded")
                break
    finally:
        pipe.stop()
        cv2.destroyAllWindows()
    print("Done.")


if __name__ == "__main__":
    main()
