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

    def test_keeps_to_the_reachable_skin(self):
        d, lm = synthetic_hand()
        skin = np.where(d < 180, -30.0, -80.0)                   # head Z -5, k 140: depth 150 -> skin Z -15...
        skin[:, :450] = np.where(d[:, :450] < 180, -47.0, -80.0)  # ...the fingers lying low: under -40 - 6
        reach, low, tall = ho.reach_mask(skin, d > 0, -40.0, 6.0, -10.0)
        self.assertTrue(low[255, 360] and not reach[255, 360])
        self.assertFalse(tall.any())
        poly, kind = ho.suggest(d, lm, FX, 0.15, reach)
        self.assertEqual(kind, "hand")
        m = polygon_mask(poly, W, H)
        self.assertFalse(m[255, 360])                            # the low finger is left out
        self.assertTrue(m[240, 600])                             # the palm stays
        self.assertFalse((m & ~reach).any())
        skin[200:280, 600:690] = -9.0                            # a tall heel by the wrist
        reach, low, tall = ho.reach_mask(skin, d > 0, -40.0, 6.0, -10.0)
        self.assertTrue(tall[240, 650])
        self.assertFalse(polygon_mask(ho.suggest(d, lm, FX, 0.15, reach)[0], W, H)[240, 650])

    def test_reach_limits_follow_the_design_check(self):
        import webui_server_v29 as v29
        saved = (v29.v10.cal, v29.v28._parts, v29.v27._paper_z)
        v29.v10.cal = lambda: {"standoff_mm_allowed": [1.0, 6.0], "travel_z": -1.0, "firmware_z_floor": -40.0}
        v29.v28._parts = lambda: {"k": 140.0, "depth_fn": lambda dd: dd}
        v29.v27._paper_z = lambda c, k: -73.5
        try:
            depth = np.array([[150.0, 182.0, 128.0, 0.0]])   # skin Z -15, -47, -3, no depth (head Z -5)
            job = {"subject": "realhand", "steps": {"scan": {"mpos": [65.7, 80.0, -5.0]}}}
            reach, info = v29._reach(job, depth)
            self.assertEqual(reach.tolist(), [[True, False, False, False]])
            self.assertEqual(info, {"lowest_skin_z": -46.0, "highest_skin_z": -10.0, "bed_z": -73.5})
            job["subject"] = "prosthetic"                    # smallest height 1 mm -> hop 5 + 3 below travel
            self.assertEqual(v29._reach(job, depth)[1]["highest_skin_z"], -9.0)
            self.assertEqual(v29._reach(dict(job, subject="person"), depth), (None, None))
        finally:
            v29.v10.cal, v29.v28._parts, v29.v27._paper_z = saved

    def test_studio_route_uses_it(self):
        import webui_server_v29 as v29
        self.assertIs(v29.Handler.GET_API["/api/studio/hand"], v29.hand_view)
        d, lm = synthetic_hand()
        saved = (v29._HAND, v29.v11.v3.run_dir_from_id, v29._reach, v29.v11.job_load)
        v29._reach = lambda job, depth: (None, None)
        v29.v11.job_load = lambda jid: {"id": jid, "subject": "person"}
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
            v29._HAND, v29.v11.v3.run_dir_from_id, v29._reach, v29.v11.job_load = saved
            region.load_bundle = saved_load


if __name__ == "__main__":
    unittest.main()
