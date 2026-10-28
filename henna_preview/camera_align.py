"""Live camera alignment against the daily-check reference (user 2026-10-28: the camera was loose - "which direction
to move camera?", then "add this tool in admin section"). Used by Admin -> Daily check -> Camera alignment and by
camera_align_live.py. Read-only: a picture in, a shift out.

The reference is the daily check's grey picture (outputs/calibration/<machine>/reference/reference.npz, full size).
The live picture is Studio's preview JPEG (colour left, depth right, half size). Shift: phase correlation, then
sub-pixel ECC (translation only), in full-size pixels and in mm at the bed.
Picture down = machine +X, picture right = machine +Y: the lens has to aim toward +X when the picture is shifted
down (positive y), toward +Y when it is shifted right.
"""
import os

import cv2
import numpy as np

MM_PER_PX = 223.6 / 435.4                 # at the bed from the scan position, full-size pixels
OK_MM = 0.3                               # the daily check's OK limit
CLOSE_MM = 0.7                            # the daily check's CHECK limit
TIP_MM = 0.15                             # a direction is given for an axis off by more than this


def reference_path(root, machine_id):
    return os.path.join(root, "outputs", "calibration", machine_id, "reference", "reference.npz")


def load_reference(path):
    with np.load(path) as z:
        return z["gray"].astype(np.float32)


def preview_gray(jpeg):
    """Studio's preview JPEG -> the colour half as grey (float32)."""
    im = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    if im is None:
        raise ValueError("the camera picture could not be read")
    return cv2.cvtColor(im[:, : im.shape[1] // 2], cv2.COLOR_BGR2GRAY).astype(np.float32)


def shift(a, b):
    """(dx, dy) of b against a, pixels of a / b."""
    a = (a - a.mean()) / (a.std() + 1e-6)
    b = (b - b.mean()) / (b.std() + 1e-6)
    (dx, dy), _ = cv2.phaseCorrelate(a, b, cv2.createHanningWindow(a.shape[::-1], cv2.CV_32F))
    warp = np.array([[1, 0, dx], [0, 1, dy]], np.float32)
    try:
        _, warp = cv2.findTransformECC(a, b, warp, cv2.MOTION_TRANSLATION,
                                       (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), None, 5)
    except cv2.error:
        pass                                  # no convergence: keep the phase-correlation estimate
    return float(warp[0, 2]), float(warp[1, 2])


def measure(ref_full, cur):
    """Reference (full size) vs the live grey picture (any size) -> {shift_mm, x_px, y_px (full size), state, tips}."""
    k = ref_full.shape[1] / cur.shape[1]
    ref = cv2.resize(ref_full, (cur.shape[1], cur.shape[0]), interpolation=cv2.INTER_AREA) if k != 1 else ref_full
    dx, dy = shift(ref, cur)
    x, y = dx * k, dy * k
    return judge(x, y)


def judge(x, y):
    mm = float(np.hypot(x, y)) * MM_PER_PX
    state = "ok" if mm <= OK_MM else ("close" if mm <= CLOSE_MM else "move")
    tips = []
    if state != "ok":
        if abs(y) * MM_PER_PX > TIP_MM:
            tips.append("aim the lens a little toward +X (away from X home)" if y > 0 else
                        "aim the lens a little toward -X (toward X home)")
        if abs(x) * MM_PER_PX > TIP_MM:
            tips.append("aim the lens a little toward +Y" if x > 0 else "aim the lens a little toward -Y")
    return {"shift_mm": round(mm, 2), "x_px": round(x, 1), "y_px": round(y, 1), "state": state, "tips": tips}
