"""Daily calibration check (camera only): is the camera still where the calibration says it is?

With the bed EMPTY (no hand, the usual fixed contents) and the machine at the fixed scan position, a 3 s
recording is compared with a REFERENCE recorded the same way when the calibration was known good:
- height:  median depth difference over the bed (mm)        - camera moved up/down, or depth drift
- tilt:    plane fitted to that difference (degrees)          - camera mount knocked / bent
- sideways: shift of the picture (phase correlation, mm)     - camera mount moved or turned
- camera:  serial number                                       - a different camera = the calibration is invalid
Only the comparison is new; the recording, its repair and verification are Studio's (CameraService.record),
the depth is Studio's 15-frame median (read_frames_median). It never changes the calibration.
Thresholds are first proposals (OK / CHECK / RECALIBRATE); the known depth drift is about 0.5 mm per day.
"""
import json
import math
import os
import shutil

import numpy as np

# (OK up to, CHECK up to) - above the second = RECALIBRATE
LIMITS = {"height_mm": (0.5, 1.0), "sideways_mm": (0.3, 0.7), "tilt_deg": (0.15, 0.3)}
CHANGED_MM, CHANGED_MAX_PCT = 2.0, 5.0        # more of the bed changed than this -> the comparison is not fair
MIN_MATCH = 0.5                               # picture match below this: the bed does not look like the reference
                                              # (good checks 0.91-0.94; 2026-10-24: jig board instead of the dot
                                              # sheet of the reference -> 0.18, "sideways 49.6 mm", "RECALIBRATE")
BORDER = 0.1                                  # ignore 10 % at each picture edge


# ---------- summary of one recording ----------
def summarize(frame):
    """Studio frame (read_frames_median) -> the small data a check needs."""
    raw = frame["depth_raw_aligned"].astype(np.float32)
    depth = raw * float(frame["depth_scale_m_per_unit"]) * 1000.0
    depth[raw <= 0] = np.nan
    rgb = frame["colour_rgb"].astype(np.float32)
    gray = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    return {"depth_mm": depth, "gray": gray, "fx": float(frame["colour_intrinsics"]["fx"]),
            "median_depth_mm": float(np.nanmedian(depth))}


def recording_serial(path):
    import pyrealsense2 as rs
    cfg = rs.config()
    cfg.enable_device_from_file(path, repeat_playback=False)
    pipe = rs.pipeline()
    prof = pipe.start(cfg)
    try:
        return prof.get_device().get_info(rs.camera_info.serial_number)
    finally:
        pipe.stop()


def analyse_recording(path, cal):
    from henna_preview.recording import count_frames_db3
    from henna_preview.recording_multi import read_frames_median
    pairs = min(count_frames_db3(path).values())
    n = int(cal["frames"])
    fr = read_frames_median(path, max(0, pairs // 2 - n // 2), n, cal["min_valid_frac"], cal["max_mad_mm"])
    s = summarize(fr)
    s["serial"] = recording_serial(path)
    return s


# ---------- comparison ----------
def _grade(value, limits):
    ok, check = limits
    return "OK" if value <= ok else ("CHECK" if value <= check else "RECALIBRATE")


def _worst(grades):
    order = ["OK", "CHECK", "RECALIBRATE"]
    return max(grades, key=order.index)


def picture_shift(ref_gray, cur_gray):
    """Sideways shift (px) of the current picture against the reference: phase correlation for the rough shift,
    then sub-pixel alignment (ECC, translation only) from there - phase correlation alone is off by up to 0.4 px."""
    import cv2
    a, b = ref_gray.astype(np.float32), cur_gray.astype(np.float32)
    win = cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F)
    (dx, dy), response = cv2.phaseCorrelate(a, b, win)
    warp = np.array([[1, 0, dx], [0, 1, dy]], np.float32)
    try:
        _, warp = cv2.findTransformECC(a, b, warp, cv2.MOTION_TRANSLATION,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-6), None, 5)
        dx, dy = float(warp[0, 2]), float(warp[1, 2])
    except cv2.error:
        pass                                          # no convergence: keep the phase-correlation estimate
    return dx, dy, float(response)


def compare(ref, cur, limits=LIMITS):
    """-> {'verdict', 'reasons', 'height_mm', 'tilt_deg', 'sideways_mm', 'changed_pct', 'same_camera', ...}."""
    import cv2
    h, w = ref["depth_mm"].shape
    by, bx = int(h * BORDER), int(w * BORDER)
    sl = (slice(by, h - by), slice(bx, w - bx))
    a, b = ref["depth_mm"][sl], cur["depth_mm"][sl]
    valid = np.isfinite(a) & np.isfinite(b)
    if valid.mean() < 0.3:
        return {"verdict": "UNCLEAR", "reasons": ["less than 30 % of the bed has depth in both recordings"]}
    diff = b - a
    offset = float(np.median(diff[valid]))
    resid = diff - offset
    changed = valid & (np.abs(resid) > CHANGED_MM)
    changed_pct = 100.0 * changed.sum() / valid.sum()
    stable = valid & ~changed
    vs, us = np.nonzero(stable)
    step = max(1, len(us) // 20000)
    A = np.column_stack([us[::step], vs[::step], np.ones(len(us[::step]))]).astype(float)
    coef, *_ = np.linalg.lstsq(A, resid[vs[::step], us[::step]].astype(float), rcond=None)
    mm_per_px = ref["median_depth_mm"] / ref["fx"]
    tilt_deg = math.degrees(math.atan(math.hypot(coef[0], coef[1]) / mm_per_px))
    dx, dy, response = picture_shift(ref["gray"], cur["gray"])
    sideways_mm = math.hypot(dx, dy) * mm_per_px
    out = {"height_mm": round(offset, 2), "tilt_deg": round(tilt_deg, 3), "sideways_mm": round(sideways_mm, 2),
           "sideways_px": [round(dx, 2), round(dy, 2)], "picture_match": round(float(response), 3),
           "changed_pct": round(changed_pct, 1), "same_camera": ref.get("serial") == cur.get("serial"),
           "reference_camera": ref.get("serial"), "camera": cur.get("serial"),
           "median_depth_mm": round(cur["median_depth_mm"], 1)}
    reasons = []
    if not out["same_camera"]:
        out.update(verdict="RECALIBRATE", reasons=[f"a different camera ({cur.get('serial')}) than at the reference "
                                                   f"({ref.get('serial')}) - the calibration belongs to that camera"])
        return out
    if changed_pct > CHANGED_MAX_PCT:
        out.update(verdict="UNCLEAR", reasons=[f"{changed_pct:.0f} % of the bed changed by more than {CHANGED_MM:g} mm - "
                                               "something is on the bed (a hand? an object?) or was moved; "
                                               "clear the bed as at the reference and check again"])
        return out
    if response < MIN_MATCH:
        out.update(verdict="UNCLEAR", reasons=[f"the picture of the bed does not match the reference (match "
                                               f"{response:.2f}, a good check is above 0.9) - the bed is set up "
                                               "differently (another board, sheet or jig, or moved); set it up as at "
                                               "the reference and check again. If it is exactly as at the reference, "
                                               "the camera moved a lot: recalibrate"])
        return out
    grades = {"height_mm": _grade(abs(offset), limits["height_mm"]),
              "tilt_deg": _grade(tilt_deg, limits["tilt_deg"]),
              "sideways_mm": _grade(sideways_mm, limits["sideways_mm"])}
    words = {"height_mm": f"height {offset:+.2f} mm", "tilt_deg": f"tilt {tilt_deg:.2f} deg",
             "sideways_mm": f"sideways {sideways_mm:.2f} mm"}
    for k, g in grades.items():
        if g != "OK":
            reasons.append(f"{words[k]} ({g}; OK up to {limits[k][0]:g}, check up to {limits[k][1]:g})")
    out.update(verdict=_worst(list(grades.values())), grades=grades, reasons=reasons or ["all within the OK limits"])
    return out


# ---------- reference storage ----------
def save_reference(ref_dir, summary, meta, recording=None):
    """Keep the old reference under ref_dir/old/<its time>; store the new one (+ its recording if given)."""
    if os.path.isfile(os.path.join(ref_dir, "reference.json")):
        with open(os.path.join(ref_dir, "reference.json"), encoding="utf-8") as f:
            old = json.load(f).get("created", "unknown").replace(":", "")
        dest = os.path.join(ref_dir, "old", old)
        os.makedirs(dest, exist_ok=True)
        for name in os.listdir(ref_dir):
            if name != "old":
                shutil.move(os.path.join(ref_dir, name), os.path.join(dest, name))
    os.makedirs(ref_dir, exist_ok=True)
    np.savez_compressed(os.path.join(ref_dir, "reference.npz"), depth_mm=summary["depth_mm"], gray=summary["gray"])
    meta = dict(meta, fx=summary["fx"], median_depth_mm=round(summary["median_depth_mm"], 1), serial=summary.get("serial"))
    with open(os.path.join(ref_dir, "reference.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    if recording:
        shutil.move(recording, os.path.join(ref_dir, "reference.db3"))
    return meta


def load_reference(ref_dir, with_arrays=True):
    p = os.path.join(ref_dir, "reference.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        meta = json.load(f)
    if not with_arrays:
        return meta
    with np.load(os.path.join(ref_dir, "reference.npz")) as z:
        return dict(meta, depth_mm=z["depth_mm"], gray=z["gray"])
