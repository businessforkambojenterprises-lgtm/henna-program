"""The machine head itself in a camera picture (2026-10-14).

The camera rides on the head and sees the nozzle tip (at depth k, the tip's own plane), the dispenser body and the
cable entering the picture from its edge. None of that is skin. head_pixels() = everything nearer than the tip plane
+ HEAD_NEAR_MM that is connected to the picture's edge, grown by a few pixels for the blurred depth at its border.
Used by the hand top (step19_hand_path_avg.hand_top_from_raw, so steps 21-23 and the v27 travel clearance) and by the
v28 hand watch. Found on the Pi: a hand lying high in the picture made its outline overlap the nozzle and the tip
(Z -6.4 at a tip height of Z -5) was taken as the hand top. A hand part that high AND touching that blob is excluded
too - it would be touching the nozzle.
"""
import numpy as np

HEAD_NEAR_MM = 3.0


def head_pixels(raw, scale, k_mm, depth_fn, grow_px=5):
    import cv2
    d = raw.astype(float) * float(scale) * 1000.0
    near = (raw > 0) & (np.asarray(depth_fn(d), float) < k_mm + HEAD_NEAR_MM)
    _, lab = cv2.connectedComponents(near.astype(np.uint8), connectivity=8)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]).tolist())) - {0}
    if not edge:
        return np.zeros(raw.shape, bool)
    head = np.isin(lab, sorted(edge)).astype(np.uint8)
    return cv2.dilate(head, np.ones((grow_px, grow_px), np.uint8)).astype(bool)
