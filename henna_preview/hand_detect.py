"""Pretrained MediaPipe Hand Landmarker on one colour image.

Landmarks are image-space guides, not the hand boundary.
'World landmarks' are model estimates, not measured skin height and not
machine coordinates. Measured depth is looked up separately from the
aligned depth image and labelled as such.
"""
import hashlib
import os

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(ROOT, "models", "hand_landmarker.task")
MODEL_SHA256 = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"

LANDMARK_NAMES = [
    "wrist", "thumb_cmc", "thumb_mcp", "thumb_ip", "thumb_tip",
    "index_mcp", "index_pip", "index_dip", "index_tip",
    "middle_mcp", "middle_pip", "middle_dip", "middle_tip",
    "ring_mcp", "ring_pip", "ring_dip", "ring_tip",
    "pinky_mcp", "pinky_pip", "pinky_dip", "pinky_tip",
]
CONNECTIONS = [(0, 1), (1, 2), (2, 3), (3, 4), (0, 5), (5, 6), (6, 7), (7, 8), (5, 9), (9, 10), (10, 11),
               (11, 12), (9, 13), (13, 14), (14, 15), (15, 16), (13, 17), (0, 17), (17, 18), (18, 19), (19, 20)]


class ModelError(ValueError):
    """Raised when the model file is missing or not the approved one."""


def verify_model(path=MODEL_PATH, expected=MODEL_SHA256):
    if not os.path.isfile(path):
        raise ModelError(f"model file not found: {path}")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    digest = h.hexdigest()
    if digest != expected:
        raise ModelError("model file checksum does not match the approved model")
    return digest


def detect_hands(rgb, model_path=MODEL_PATH, num_hands=1, min_confidence=0.5):
    """Return a list of detected hands with landmark pixel positions (u right, v down)."""
    import mediapipe as mp
    from mediapipe.tasks import python as mpt
    from mediapipe.tasks.python import vision

    rgb = np.ascontiguousarray(rgb, dtype=np.uint8)
    if rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("expected an RGB image of shape (H, W, 3)")
    h, w = rgb.shape[:2]
    opts = vision.HandLandmarkerOptions(
        base_options=mpt.BaseOptions(model_asset_path=model_path),
        running_mode=vision.RunningMode.IMAGE,
        num_hands=num_hands,
        min_hand_detection_confidence=min_confidence,
        min_hand_presence_confidence=min_confidence,
    )
    with vision.HandLandmarker.create_from_options(opts) as lm:
        res = lm.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb))
    hands = []
    for i, lms in enumerate(res.hand_landmarks):
        cat = res.handedness[i][0]
        hands.append({
            "handedness_label": cat.category_name,
            "handedness_score": float(cat.score),
            "landmarks_px": [[float(l.x * w), float(l.y * h)] for l in lms],
            "world_landmarks_model_estimate_m": [[float(l.x), float(l.y), float(l.z)]
                                                 for l in res.hand_world_landmarks[i]],
        })
    return hands


def measured_depth_at(landmarks_px, depth_mm):
    """Measured depth (mm) at each landmark pixel, or None where depth is missing or off-image."""
    h, w = depth_mm.shape
    out = []
    for u, v in landmarks_px:
        iu, iv = int(round(u)), int(round(v))
        if 0 <= iu < w and 0 <= iv < h:
            z = float(depth_mm[iv, iu])
            out.append(z if np.isfinite(z) and z > 0 else None)
        else:
            out.append(None)
    return out
