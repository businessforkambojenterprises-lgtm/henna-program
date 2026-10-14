"""Calibration session (Studio v26) for a machine in calibration mode: X/Y from dots, Z from block touches.

The maths is the belt-drive machine's own, unchanged: henna_preview.calibration.solve (X/Y: tool_XY =
head_XY_at_capture + R @ camera_xy_mm + t, plain or mirrored, rms, scale check) and henna_preview.zcal.solve_k
(Z: k = camera depth - (Z_capture - Z_contact), touch spots found through the X/Y result, measured depth only).
New here: the dots are found automatically in the dot-sheet scan (dark round blobs; only those with measured
depth), and the session keeps scans, nozzle positions and touches in outputs/calibration/<machine>/session/.
A fit is only a DRAFT: it becomes the machine's calibration only after the user approves it.
"""
import datetime as dt
import json
import math
import os
import shutil

import numpy as np

from henna_preview.calibration import CalibrationError, dot_camera_mm, solve

DOT_MM = 8.0                      # printed dot diameter on the dot sheet
XY_OK_RMS_MM, XY_MIN_DOTS = 1.0, 5
Z_OK_SPREAD_MM, Z_MIN_TOUCHES = 1.0, 3
PAPER_MAX_SAT = 50                # HSV saturation (0-255) around a dot: paper below, wood above


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


# ---------- dots in a scan ----------
def find_dots(frame, dot_mm=DOT_MM):
    """Dark round blobs of about dot_mm in the colour picture -> [{'label','pixel_uv','camera_mm','depth_pixels'}]."""
    import cv2
    rgb, raw = frame["colour_rgb"], frame["depth_raw_aligned"]
    scale, intr = frame["depth_scale_m_per_unit"], frame["colour_intrinsics"]
    d = raw[raw > 0].astype(float) * scale * 1000.0
    if not d.size:
        raise CalibrationError("no measured depth in the scan")
    z = float(np.median(d))
    want = math.pi * (intr["fx"] * dot_mm / z / 2.0) ** 2              # expected dot area in pixels
    gray = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2GRAY)
    sat = cv2.cvtColor(np.ascontiguousarray(rgb), cv2.COLOR_RGB2HSV)[:, :, 1]
    mask = cv2.adaptiveThreshold(cv2.GaussianBlur(gray, (5, 5), 0), 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY_INV, 51, 15)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    n, lab, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    found = []
    H, W = gray.shape
    paper = float(np.percentile(gray, 90))                              # the brightest paper in this light
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        # hand-drawn dots are often 4-6 mm: accept small dots, but only round, solid ones
        if not max(16.0, 0.15 * want) <= area <= 2.5 * want or min(w, h) / max(w, h) < 0.7 or area / (w * h) < 0.6:
            continue
        # a dot is dark ink on bright paper: wood grain, tape labels and the nozzle are not
        x0, y0, x1, y1 = max(0, x - 8), max(0, y - 8), min(W, x + w + 8), min(H, y + h + 8)
        own = lab[y0:y1, x0:x1] == i
        near = cv2.dilate(own.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
        ring = ~near & (lab[y0:y1, x0:x1] == 0)
        patch = gray[y0:y1, x0:x1]
        if not ring.any():
            continue
        bg, fg = float(patch[ring].mean()), float(patch[own].mean())
        # relative to this picture's light (measured 2026-10-13: dots 0.32-0.53 of their paper, also in shadow;
        # tape-label text 0.71-0.83). Paper is grey-white, wood is brown: around the dot, saturation paper 29-30,
        # wood 69-85 (wood brightness 0.6 of the paper, as bright as paper in the head's shadow at 0.65)
        if bg < 0.5 * paper or fg > 0.6 * bg or float(np.median(sat[y0:y1, x0:x1][ring])) > PAPER_MAX_SAT:
            continue
        uv = (float(cents[i][0]), float(cents[i][1]))
        p, used = None, 0
        for win in (3, 6, 10):                                          # dark ink can lack depth: widen the window
            p, used = dot_camera_mm(raw, scale, intr, uv, window=win)
            if p is not None:
                break
        if p is None:
            continue
        found.append({"pixel_uv": [round(uv[0], 1), round(uv[1], 1)], "camera_mm": [round(float(c), 2) for c in p],
                      "depth_pixels": int(used), "area_px": int(area)})
    # reading order in the picture: rows (a new row where the picture height jumps), each row left to right
    rows = []
    for f in sorted(found, key=lambda f: f["pixel_uv"][1]):
        if rows and f["pixel_uv"][1] - rows[-1][-1]["pixel_uv"][1] <= 25:
            rows[-1].append(f)
        else:
            rows.append([f])
    found = [f for r in rows for f in sorted(r, key=lambda f: f["pixel_uv"][0])]
    for k, f in enumerate(found):
        f["label"] = f"D{k + 1}"
    return found


def overlay(frame, dots, path, title):
    import cv2
    img = np.ascontiguousarray(frame["colour_rgb"][:, :, ::-1]).copy()
    for f in dots:
        u, v = (int(round(c)) for c in f["pixel_uv"])
        cv2.circle(img, (u, v), 14, (0, 220, 255), 2)
        cv2.putText(img, f["label"], (u + 16, v - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4)
        cv2.putText(img, f["label"], (u + 16, v - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 220, 255), 2)
    cv2.putText(img, title, (10, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cv2.imwrite(path, img)


# ---------- fits ----------
def fit_xy(scan, points):
    """scan: {'head': {x,y,z}, 'dots': [...]}; points: {label: {'x','y',...}} -> calibration result (draft)."""
    used = [d for d in scan["dots"] if d["label"] in points]
    if len(used) < 3:
        raise CalibrationError("save the nozzle position over at least 3 dots (5 or more recommended)")
    head = scan["head"]
    r = solve([d["camera_mm"][:2] for d in used], [[points[d["label"]]["x"], points[d["label"]]["y"]] for d in used],
              [head["x"], head["y"]])
    dots = []
    for d, res in zip(used, r["residuals"]):
        dots.append({"label": d["label"], "pixel_uv": d["pixel_uv"], "camera_mm": d["camera_mm"],
                     "machine_xy": [points[d["label"]]["x"], points[d["label"]]["y"]],
                     "residual_mm": [round(float(res[0]), 2), round(float(res[1]), 2)],
                     "residual_len_mm": round(float(np.hypot(*res)), 2)})
    zs = [d["camera_mm"][2] for d in used]
    out = {"dots": dots, "R": np.asarray(r["R"]).round(6).tolist(), "t_mm": [round(float(v), 3) for v in r["t"]],
           "rotation_deg": round(r["rotation_deg"], 2), "mirrored": r["mirrored"], "rms_mm": round(r["rms_mm"], 2),
           "max_residual_mm": round(r["max_residual_mm"], 2), "scale_check": round(r["scale_check"], 4),
           "dots_used": len(used), "target_depth_mm_range": [round(min(zs), 1), round(max(zs), 1)]}
    problems = []
    if len(used) < XY_MIN_DOTS:
        problems.append(f"only {len(used)} dots (at least {XY_MIN_DOTS} recommended)")
    if out["rms_mm"] > XY_OK_RMS_MM:
        problems.append(f"rms {out['rms_mm']} mm is above {XY_OK_RMS_MM:g} mm - re-check the worst dots")
    if not 0.98 <= out["scale_check"] <= 1.02:
        problems.append(f"scale check {out['scale_check']} is far from 1 - camera mm and machine mm disagree")
    out["problems"] = problems
    out["verdict"] = "GOOD" if not problems else "CHECK"
    return out


def fit_z(z_scan_frame, z_head, touches, xy_fit):
    from henna_preview.zcal import solve_k
    if len(touches) < 2:
        raise CalibrationError("save at least 2 touches (3 or more recommended)")
    r = solve_k([{"label": t["label"], "x": t["x"], "y": t["y"], "z_contact": t["z_contact"]} for t in touches],
                z_scan_frame, z_head, {"R": xy_fit["R"], "t_mm": xy_fit["t_mm"]})
    problems = []
    if len(touches) < Z_MIN_TOUCHES:
        problems.append(f"only {len(touches)} touches (at least {Z_MIN_TOUCHES} recommended)")
    if r["k_spread_mm"] > Z_OK_SPREAD_MM:
        problems.append(f"k spread {r['k_spread_mm']} mm is above {Z_OK_SPREAD_MM:g} mm - repeat the odd touch")
    r["problems"] = problems
    r["verdict"] = "GOOD" if not problems else "CHECK"
    return r


# ---------- the session on disk ----------
class Session:
    def __init__(self, machine_root):
        self.dir = os.path.join(machine_root, "session")
        self.path = os.path.join(self.dir, "session.json")

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"started": now(), "xy_scan": None, "xy_points": {}, "xy_fit": None,
                    "z_scan": None, "z_touches": [], "z_fit": None}

    def save(self, s):
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(s, f, indent=2)
        os.replace(tmp, self.path)
        return s

    def archive(self):
        if os.path.isdir(self.dir):
            dest = os.path.join(os.path.dirname(self.dir), "old_sessions", dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(self.dir, dest)
