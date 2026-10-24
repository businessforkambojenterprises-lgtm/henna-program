"""Suggested outline = the whole hand (palm, fingers, thumb), not just the middle of the palm.

User 2026-10-24: "this suggested area is too small for a mehndi design machine, make it big according to our designs"
- the palm hull of region.suggest_region left out the palm's edges, the fingers and the thumb (finger bels, trails).

A hand model is built from the 21 landmarks: the palm polygon grown to the palm's edge, a capsule along each finger
and the thumb, cut at the wrist line - so the bangle / threads past the wrist, the fingertip holder and the head
hanging into the view are left out.
- "hand": the hand stands clear of the surface around it (2026-10-24 scan: hand 137-166 mm, board 190-200 mm) - the
  model is cut to the depth silhouette, so the outline follows the real edge of the hand.
- "model": the hand lies level with what is around it (2026-10-24: fingers in the jig's cut-out, level with the board
  top) - depth cannot tell hand from board, so the outline is the landmark model alone, drawn narrower so it stays on
  the skin. The operator checks its edges on the photo.
Either is pulled in from the edge by inset_mm (the steep sides of the fingers and the palm edge are no place for a
stroke) and traced into one polygon. A suggestion only - the operator still checks, edits and accepts it."""
import cv2
import numpy as np

HAND_DEPTH_SLACK_MM = 10.0          # deepest hand landmark + this = farthest hand pixel
MIN_STEP_MM = 12.0                  # "hand": the surface around must be at least this much farther than the hand
PALM = (0, 1, 2, 5, 9, 13, 17)      # wrist, thumb base + knuckle, finger knuckles
FINGERS = ((1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16), (17, 18, 19, 20))
SIZES = {"hand": {"finger_mm": 11.0, "palm_grow_mm": 12.0},     # generous: the depth silhouette makes the real edge
         "model": {"finger_mm": 8.5, "palm_grow_mm": 7.0}}      # landmarks only: half a finger's width, palm edge
TIP_EXTRA_MM = 6.0                  # the tip landmark is a little short of the fingertip
WRIST_BACK_MM = 3.0                 # keep up to just past the wrist landmark, not the forearm
MAX_POINTS = 60                     # each point is a handle to drag on the page (region.check_polygon allows 200)
REACH_MARGIN_MM = 0.5               # inside the reach limits, for depth noise
MIN_PALM_COVER = 0.8                # share of the old palm suggestion the hand outline must cover


class OutlineError(ValueError):
    """No usable hand shape - the caller falls back to the palm suggestion."""


def inset_mm_of(margin_frac):
    """The page's margin slider (0-0.5, default 0.15) as mm in from the hand's edge (2-12 mm, default 5)."""
    return 2.0 + 20.0 * float(margin_frac)


def _depth_at(depth_mm, u, v, r=3):
    h, w = depth_mm.shape
    win = depth_mm[max(0, v - r):min(h, v + r + 1), max(0, u - r):min(w, u + r + 1)]
    win = win[win > 0]
    return float(np.median(win)) if len(win) else None


def _disk(r_px):
    r = max(1, int(round(r_px)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def _model(lm, shape, px_mm, finger_mm, palm_grow_mm):
    model = np.zeros(shape, np.uint8)
    palm = cv2.convexHull(lm[list(PALM)].astype(np.float32)).reshape(-1, 2)
    cv2.fillPoly(model, [np.round(palm).astype(np.int32)], 1)
    model = cv2.dilate(model, _disk(palm_grow_mm * px_mm))
    rad = max(1, int(round(finger_mm * px_mm)))
    for f in FINGERS:
        pts = lm[list(f)]
        d = pts[-1] - pts[-2]
        pts = np.vstack([pts, pts[-1] + d / max(np.hypot(*d), 1e-6) * TIP_EXTRA_MM * px_mm])
        for a, b in zip(pts[:-1], pts[1:]):
            cv2.line(model, tuple(int(round(x)) for x in a), tuple(int(round(x)) for x in b), 1, 2 * rad)
            cv2.circle(model, tuple(int(round(x)) for x in b), rad, 1, -1)
    return model > 0


def hand_mask(depth_mm, landmarks_px, fx):
    """(mask, px_per_mm, kind) of the hand before the inset; kind "hand" (depth silhouette) or "model"."""
    lm = np.asarray(landmarks_px, float)
    if lm.shape != (21, 2):
        raise OutlineError("need 21 landmarks")
    h, w = depth_mm.shape
    ds = [_depth_at(depth_mm, int(round(u)), int(round(v))) for u, v in lm]
    ds = [d for d in ds if d]
    if len(ds) < 15:
        raise OutlineError("too little depth on the hand")
    px_mm = fx / float(np.median(ds))                       # pixels per mm at the hand's depth
    axis = lm[9] - lm[0]                                    # wrist -> middle knuckle
    axis = axis / max(np.hypot(*axis), 1e-6)
    v, u = np.mgrid[0:h, 0:w]
    hand_side = (u - lm[0, 0]) * axis[0] + (v - lm[0, 1]) * axis[1] >= -WRIST_BACK_MM * px_mm

    big = _model(lm, (h, w), px_mm, **SIZES["hand"])
    ring = cv2.dilate(big.astype(np.uint8), _disk(15 * px_mm)) > 0
    ring &= ~big & (depth_mm > 0) & hand_side
    clear = ring.sum() >= 100 and np.median(depth_mm[ring]) >= max(ds) + MIN_STEP_MM
    if clear:
        kind, mask = "hand", big & (depth_mm > 0) & (depth_mm < max(ds) + HAND_DEPTH_SLACK_MM)
    else:
        kind, mask = "model", _model(lm, (h, w), px_mm, **SIZES["model"]) & (depth_mm > 0)
    mask &= hand_side
    # close the small depth holes inside the hand (they stay excluded later: region keeps "missing depth excluded")
    mask = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_CLOSE, _disk(2)) > 0
    n, lab = cv2.connectedComponents(mask.astype(np.uint8))
    cu, cv_ = np.round(lm[[0, 5, 9, 13, 17]].mean(axis=0)).astype(int)
    keep = lab[min(max(cv_, 0), h - 1), min(max(cu, 0), w - 1)]
    if keep == 0:                                           # palm centre on a hole: the biggest piece
        sizes = np.bincount(lab.ravel())
        sizes[0] = 0
        keep = int(sizes.argmax())
    return lab == keep, px_mm, kind


def reach_mask(skin_z, ok, floor_z, max_standoff_mm, top_z):
    """Skin the nozzle can work on (user 2026-10-24: "make suggested outline only cover reachable area"):
    not too low - the nozzle max_standoff_mm above it stays at or above the Z travel's floor (fingers in the jig's
    cut-out were at Z -46 with the floor at -40) - and not too tall - at or below top_z, the highest skin the travel
    moves may cross (travel Z - max(standoff, hop) - margin, the path check's rule). REACH_MARGIN_MM inside both."""
    low = skin_z + max_standoff_mm < floor_z + REACH_MARGIN_MM
    tall = skin_z > top_z - REACH_MARGIN_MM
    return ok & ~low & ~tall, ok & low, ok & tall


def suggest_hand_outline(depth_mm, landmarks_px, fx, inset_mm, reach=None):
    """(polygon in colour pixels around the whole hand, inset_mm in from its edge; kind). reach: boolean mask of
    the skin the nozzle can work on - the outline then keeps to it (no inset at that border: it is not an edge)."""
    mask, px_mm, kind = hand_mask(depth_mm, landmarks_px, fx)
    if inset_mm > 0:
        mask = cv2.erode(mask.astype(np.uint8), _disk(inset_mm * px_mm)) > 0
    if reach is not None:
        r = cv2.morphologyEx(reach.astype(np.uint8), cv2.MORPH_OPEN, _disk(1.0 * px_mm))
        r = cv2.morphologyEx(r, cv2.MORPH_CLOSE, _disk(2.0 * px_mm)) > 0
        mask &= r
    cs, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not cs:
        raise OutlineError("no hand left after the inset")
    c = max(cs, key=cv2.contourArea)
    if cv2.contourArea(c) < (20 * px_mm) ** 2:              # smaller than 20 x 20 mm: not a hand
        raise OutlineError("hand shape too small")
    eps = 1.0
    poly = cv2.approxPolyDP(c, eps, True)
    while len(poly) > MAX_POINTS:
        eps *= 1.4
        poly = cv2.approxPolyDP(c, eps, True)
    poly = poly.reshape(-1, 2).astype(float)
    if len(poly) < 3:
        raise OutlineError("hand shape too small")
    return poly.round(1).tolist(), kind


def suggest(depth_mm, landmarks_px, fx, margin_frac, reach=None):
    """(polygon, kind): the whole-hand outline ("hand" / "model"), kept to the reachable skin when reach is given,
    or the old palm suggestion ("palm") when there is no hand shape or it does not cover at least MIN_PALM_COVER of
    the (reachable part of the) palm suggestion (2026-10-14 prosthetic scan: landmarks off the hand)."""
    from henna_preview.region import polygon_mask, suggest_region
    palm = suggest_region(np.asarray(landmarks_px, float).tolist(), margin_frac)
    try:
        poly, kind = suggest_hand_outline(depth_mm, landmarks_px, fx, inset_mm_of(margin_frac), reach)
    except OutlineError:
        return palm, "palm"
    h, w = depth_mm.shape
    pm = polygon_mask(palm, w, h)
    if reach is not None:
        pm &= reach
    if (pm & polygon_mask(poly, w, h)).sum() < MIN_PALM_COVER * max(1, pm.sum()):
        return palm, "palm"
    return poly, kind
