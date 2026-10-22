"""The machine head itself in a camera picture (2026-10-14).

The camera rides on the head and sees the nozzle tip (at depth k, the tip's own plane), the dispenser body and the
cable entering the picture from its edge. None of that is skin. head_pixels() = everything nearer than the tip plane
+ HEAD_NEAR_MM that is connected to the picture's edge, grown by a few pixels for the blurred depth at its border.
Used by the hand top (step19_hand_path_avg.hand_top_from_raw, so steps 21-23 and the v27 travel clearance) and by the
v28 hand watch. Found on the Pi: a hand lying high in the picture made its outline overlap the nozzle and the tip
(Z -6.4 at a tip height of Z -5) was taken as the hand top. A hand part that high AND touching that blob is excluded
too - it would be touching the nozzle.
EDGE BAND (2026-10-22): the D405 often has a few rows / columns without depth along the picture's edge, so a cable or
tube hanging in from the edge can start a few pixels inside it - found on a real-hand job: a blob 68 mm from the camera
from row 4 was taken as something 69 mm tall on the nozzle's path. A near blob that reaches within EDGE_BAND_PX of
the edge counts as the head; one loose in the picture (e.g. a hand held above the bed) still does not.
"""
import numpy as np

HEAD_NEAR_MM = 3.0
EDGE_BAND_PX = 16


def head_pixels(raw, scale, k_mm, depth_fn, grow_px=5):
    import cv2
    d = raw.astype(float) * float(scale) * 1000.0
    near = (raw > 0) & (np.asarray(depth_fn(d), float) < k_mm + HEAD_NEAR_MM)
    _, lab = cv2.connectedComponents(near.astype(np.uint8), connectivity=8)
    b = EDGE_BAND_PX
    band = np.concatenate([lab[:b].ravel(), lab[-b:].ravel(), lab[:, :b].ravel(), lab[:, -b:].ravel()])
    edge = set(np.unique(band).tolist()) - {0}
    if not edge:
        return np.zeros(raw.shape, bool)
    head = np.isin(lab, sorted(edge)).astype(np.uint8)
    return cv2.dilate(head, np.ones((grow_px, grow_px), np.uint8)).astype(bool)
