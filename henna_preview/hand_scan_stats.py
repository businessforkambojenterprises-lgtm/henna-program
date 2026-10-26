"""Measurements of one hand scanned with the D405 on a STAND (no machine; user 2026-10-26: "for this scan i'm only
mounting d405 on a stand so no machine movements") - for collecting many people's hands (hand_scan_collect.py).

Heights are above the TABLE: a plane fitted to the depth around the hand (outside a grown landmark hull), with a
robust refit (points within PLANE_KEEP_MM of the first fit). Every pixel's 3D point comes from the colour camera's
pinhole model (fx, fy, ppx, ppy; the D405's lens distortion is small at these angles and is left out here).
Per hand: fingertip, finger-base, palm and heel heights, hand length (wrist -> middle fingertip, 3D), palm width
(index -> little finger knuckle, 3D), the missing-depth share inside the hand and the table fit's quality.
Pure functions: numpy only (the landmarks come from henna_preview.hand_detect).
"""
import numpy as np

PLANE_KEEP_MM = 3.0
HULL_GROW_PX = 25
WINDOW_PX = 4
TIPS = {"thumb": 4, "index": 8, "middle": 12, "ring": 16, "little": 20}
BASES = {"index": 5, "middle": 9, "ring": 13, "little": 17}
TIP_INWARD = 0.35                       # fingertip heights are read this share of the way from the tip dot to the next
                                        # joint: the dot often sits just past the finger end, on the table (2026-10-26:
                                        # "fingertips 1.2 mm")


def points(depth_mm, intr):
    """(H, W, 3) camera-frame millimetres (NaN where no depth)."""
    h, w = depth_mm.shape
    v, u = np.mgrid[0:h, 0:w].astype(float)
    z = np.where(depth_mm > 0, depth_mm, np.nan)
    x = (u - float(intr["ppx"])) / float(intr["fx"]) * z
    y = (v - float(intr["ppy"])) / float(intr["fy"]) * z
    return np.dstack([x, y, z])


def hand_mask(shape, landmarks_px, grow_px=HULL_GROW_PX):
    import cv2
    m = np.zeros(shape, np.uint8)
    hull = cv2.convexHull(np.round(np.asarray(landmarks_px, float)).astype(np.int32))
    cv2.fillConvexPoly(m, hull, 1)
    if grow_px:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow_px + 1,) * 2))
    return m > 0


def fit_table(pts, keep, tries=400, max_tilt_deg=30.0, rng=None):
    """The table: the BIGGEST flat surface around the hand facing the camera (a jig board and the bed below it are two
    surfaces - 2026-10-26: one plane through both read as a 27 deg tilt). RANSAC over planes whose normal is within
    max_tilt_deg of the camera axis, inliers within PLANE_KEEP_MM, then a least-squares refit on the inliers.
    -> (normal facing the camera, d, rms_mm, n_points) with normal . p = d on the table."""
    p = pts[keep]
    p = p[np.isfinite(p).all(axis=1)]
    if len(p) < 500:
        raise ValueError("too little table around the hand to find the table")
    rng = rng or np.random.default_rng(0)
    if len(p) > 40000:
        p = p[rng.choice(len(p), 40000, replace=False)]
    cos_max = np.cos(np.radians(max_tilt_deg))
    best, best_n = None, 0
    for _ in range(tries):
        a, b, c = p[rng.choice(len(p), 3, replace=False)]
        n = np.cross(b - a, c - a)
        ln = np.linalg.norm(n)
        if ln < 1e-6:
            continue
        n = n / ln
        if n[2] > 0:
            n = -n
        if -n[2] < cos_max:
            continue
        inl = np.abs(p @ n - n @ a) <= PLANE_KEEP_MM
        k = int(inl.sum())
        if k > best_n:
            best, best_n = inl, k
    if best is None or best_n < 300:
        raise ValueError("no flat table found around the hand")
    q = p[best]
    for _ in range(2):
        cq = q.mean(axis=0)
        _, _, vt = np.linalg.svd(q - cq, full_matrices=False)
        n = vt[-1] if vt[-1][2] < 0 else -vt[-1]
        d = float(n @ cq)
        keep_q = np.abs(p @ n - d) <= PLANE_KEEP_MM
        q = p[keep_q]
    rms = float(np.sqrt(np.mean((q @ n - d) ** 2)))
    return n, d, rms, len(q)


def heights(pts, normal, d):
    """(H, W) height above the table plane in mm (NaN where no depth)."""
    return pts @ normal - d


def _at(hmap, u, v, r=WINDOW_PX):
    h, w = hmap.shape
    win = hmap[max(0, int(v) - r):min(h, int(v) + r + 1), max(0, int(u) - r):min(w, int(u) + r + 1)]
    win = win[np.isfinite(win)]
    return round(float(np.median(win)), 1) if len(win) else None


def _p3(pts, u, v, r=WINDOW_PX):
    h, w = pts.shape[:2]
    win = pts[max(0, int(v) - r):min(h, int(v) + r + 1), max(0, int(u) - r):min(w, int(u) + r + 1)].reshape(-1, 3)
    win = win[np.isfinite(win).all(axis=1)]
    return np.median(win, axis=0) if len(win) else None


def measure(depth_mm, intr, landmarks_px):
    """-> dict of the hand's measurements (see the module text) and the quality checks."""
    lm = np.asarray(landmarks_px, float)
    if lm.shape != (21, 2):
        raise ValueError("need the 21 hand landmarks")
    pts = points(depth_mm, intr)
    grown = hand_mask(depth_mm.shape, lm)
    n, d, rms, npts = fit_table(pts, ~grown)
    hmap = heights(pts, n, d)
    inside = hand_mask(depth_mm.shape, lm, grow_px=0)
    hh = hmap[inside & np.isfinite(hmap)]
    missing = float(np.mean(~np.isfinite(hmap[inside]))) if inside.any() else 1.0
    palm_c = lm[[0, 5, 9, 13, 17]].mean(axis=0)
    heel = lm[0] + 0.25 * (lm[9] - lm[0])                  # a quarter of the way from the wrist to the middle knuckle
    out = {
        "table_fit_rms_mm": round(rms, 2), "table_points": int(npts),
        "camera_to_table_mm": round(float(d / n[2]), 1),          # along the optical axis
        "table_tilt_deg": round(float(np.degrees(np.arccos(min(1.0, abs(n[2]))))), 2),
        "missing_depth_in_hand_pct": round(100 * missing, 2),
        "hand_height_mm": {"min": round(float(np.percentile(hh, 2)), 1) if len(hh) else None,
                           "max": round(float(np.percentile(hh, 98)), 1) if len(hh) else None},
        "fingertips_mm": {k: _at(hmap, *(lm[i] + TIP_INWARD * (lm[i - 1] - lm[i]))) for k, i in TIPS.items()},
        "finger_bases_mm": {k: _at(hmap, *lm[i]) for k, i in BASES.items()},
        "palm_centre_mm": _at(hmap, *palm_c, r=10),
        "heel_mm": _at(hmap, *heel, r=10),
        "wrist_mm": _at(hmap, *lm[0]),
    }
    w, tip = _p3(pts, *lm[0]), _p3(pts, *lm[12])
    i5, i17 = _p3(pts, *lm[5]), _p3(pts, *lm[17])
    out["hand_length_mm"] = None if w is None or tip is None else round(float(np.linalg.norm(tip - w)), 1)
    out["palm_width_mm"] = None if i5 is None or i17 is None else round(float(np.linalg.norm(i17 - i5)), 1)
    problems = []
    if rms > 1.5:
        problems.append(f"the table is not flat or not seen well (fit {rms:.1f} mm)")
    if missing > 0.05:
        problems.append(f"{100 * missing:.0f} % of the hand has no depth")
    if out["table_tilt_deg"] > 5:
        problems.append(f"the camera is tilted {out['table_tilt_deg']:.0f} deg against the table - point it straight down")
    out["problems"] = problems
    out["ok"] = not problems
    return out
