"""Whole-hand suggested outline (user 2026-10-24: "this suggested area is too small for a mehndi design machine, make
it big according to our designs"): palm, fingers and thumb cut out of the depth image, limited to the landmark hand
model (not the wrist / forearm, not things far from the hand), inset from the edge; the palm outline when the hand
shape is not trustworthy."""
import os
import sys
import unittest

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import hand_outline as ho  # noqa: E402
from henna_preview.region import polygon_mask, suggest_region  # noqa: E402

W, H, FX = 848, 480, 435.0              # D405 colour size; ~2.9 px per mm at 150 mm


def synthetic_hand():
    """Palm-up hand like the 2026-10-24 scans: fingers to the left, wrist to the right; board at 195 mm."""
    lm = np.zeros((21, 2))
    lm[0] = (700, 240)                                       # wrist
    lm[1:5] = [(660, 320), (600, 350), (540, 370), (490, 390)]   # thumb
    for f, y in zip(range(4), (300, 255, 210, 165)):         # index .. pinky: knuckle, joints, tip
        lm[5 + 4 * f: 9 + 4 * f] = [(520, y), (440, y), (390, y), (340, y)]
    d = np.full((H, W), 195.0)
    mask = np.zeros((H, W), np.uint8)
    cv2.fillPoly(mask, [np.array([[700, 140], [700, 330], [520, 330], [500, 140]], np.int32)], 1)   # palm
    for f in range(4):
        y = int(lm[5 + 4 * f, 1])
        cv2.rectangle(mask, (325, y - 23), (530, y + 23), 1, -1)                                   # touching fingers
    cv2.line(mask, (650, 320), (480, 395), 1, 48)                                                  # thumb
    cv2.rectangle(mask, (700, 140), (848, 340), 1, -1)                                             # forearm
    d[mask > 0] = 150.0
    d[20:60, 600:700] = 75.0                                 # the head hanging into the view
    return d, lm


class OutlineTests(unittest.TestCase):
    def test_whole_hand_bigger_than_the_palm_and_inside_the_hand(self):
        d, lm = synthetic_hand()
        poly, kind = ho.suggest(d, lm, FX, 0.15)
        self.assertEqual(kind, "hand")
        m = polygon_mask(poly, W, H)
        palm = polygon_mask(suggest_region(lm.tolist(), 0.15), W, H)
        self.assertGreater(m.sum(), 3 * palm.sum())
        self.assertTrue(m[255, 360])                             # a finger
        self.assertTrue(m[338, 600])                             # the thumb
        self.assertFalse(m[240, 780])                            # forearm past the wrist
        self.assertFalse(m[40, 650])                             # the head
        self.assertFalse((m & (d > 180)).any())                  # never on the board
        self.assertLessEqual(len(poly), ho.MAX_POINTS)

    def test_inset_follows_the_slider(self):
        d, lm = synthetic_hand()
        a = polygon_mask(ho.suggest(d, lm, FX, 0.0)[0], W, H).sum()
        b = polygon_mask(ho.suggest(d, lm, FX, 0.3)[0], W, H).sum()
        self.assertGreater(a, b)
        self.assertEqual((ho.inset_mm_of(0.0), ho.inset_mm_of(0.15)), (2.0, 5.0))

    def test_falls_back_to_the_palm(self):
        d, lm = synthetic_hand()
        flat = np.full((H, W), 195.0)              # hand level with the board around it (2026-10-24: in the jig)
        poly, kind = ho.suggest(flat, lm, FX, 0.15)
        self.assertEqual(kind, "model")                          # the landmark model alone, narrower
        m = polygon_mask(poly, W, H)
        hand = polygon_mask(ho.suggest(d, lm, FX, 0.15)[0], W, H)
        self.assertTrue(m[255, 360] and m[338, 600])             # fingers and thumb still in
        self.assertLess(m.sum(), hand.sum())
        self.assertFalse(m[240, 780])
        poly, kind = ho.suggest(np.zeros((H, W)), lm, FX, 0.15)   # no depth at all
        self.assertEqual(kind, "palm")

    def test_studio_route_uses_it(self):
        import webui_server_v29 as v29
        self.assertIs(v29.Handler.GET_API["/api/studio/hand"], v29.hand_view)
        d, lm = synthetic_hand()
        saved = (v29._HAND, v29.v11.v3.run_dir_from_id)
        import henna_preview.region as region
        saved_load = region.load_bundle
        v29._HAND = lambda q: {"run": "web:x", "suggestion": [[1, 1], [2, 1], [2, 2]]}
        v29.v11.v3.run_dir_from_id = lambda rid: rid
        region.load_bundle = lambda run: {"depth_raw_aligned": d, "depth_scale_m_per_unit": 0.001,
                                          "landmarks_px": lm, "colour_intrinsics": {"fx": FX}}
        try:
            out = v29.hand_view({"job": ["j"], "margin": ["0.15"]})
            self.assertEqual((out["suggestion_kind"], out["inset_mm"]), ("hand", 5.0))
            region.load_bundle = lambda run: (_ for _ in ()).throw(OSError("gone"))
            out = v29.hand_view({"job": ["j"], "margin": ["0.15"]})
            self.assertEqual((out["suggestion_kind"], out["suggestion"]), ("palm", [[1, 1], [2, 1], [2, 2]]))
        finally:
            v29._HAND, v29.v11.v3.run_dir_from_id = saved
            region.load_bundle = saved_load


if __name__ == "__main__":
    unittest.main()
