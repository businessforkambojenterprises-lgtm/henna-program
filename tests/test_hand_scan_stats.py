"""Hand scans with the D405 on a stand (user 2026-10-26: "for this scan i'm only mounting d405 on a stand" -> "yes
build it"): heights above the table from a fitted table plane, the hand's sizes in 3D, warnings for poor scans, and
the collection tool's file names and index."""
import csv
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import hand_scan_stats as hs  # noqa: E402

W, H = 848, 480
INTR = {"fx": 435.4, "fy": 434.7, "ppx": 424.0, "ppy": 240.0}


def scene(table_mm=215.0, palm_mm=40.0, finger_mm=20.0, tilt_y=0.0):
    """A flat table 215 mm from the camera, a palm block 40 mm high and fingers 20 mm high (palm up, fingers left)."""
    v, u = np.mgrid[0:H, 0:W].astype(float)
    depth = np.full((H, W), table_mm) + tilt_y * (v - H / 2)
    lm = np.zeros((21, 2))
    lm[0] = (650, 240)
    lm[1:5] = [(620, 300), (580, 320), (540, 330), (500, 335)]
    for f, y in zip(range(4), (285, 255, 225, 195)):
        lm[5 + 4 * f: 9 + 4 * f] = [(500, y), (450, y), (410, y), (370, y)]
    palm = (u >= 490) & (u <= 660) & (v >= 180) & (v <= 300)
    fingers = (u >= 355) & (u < 490) & (v >= 180) & (v <= 300)
    depth[palm] -= palm_mm                                       # the hand tilts with the table (one camera tilt)
    depth[fingers] -= finger_mm
    return depth, lm


class StatsTests(unittest.TestCase):
    def test_heights_above_the_table_and_sizes(self):
        depth, lm = scene()
        m = hs.measure(depth, INTR, lm)
        self.assertTrue(m["ok"], m["problems"])
        self.assertAlmostEqual(m["camera_to_table_mm"], 215.0, delta=0.5)
        self.assertLess(m["table_tilt_deg"], 0.5)
        self.assertAlmostEqual(m["palm_centre_mm"], 40.0, delta=0.5)
        self.assertAlmostEqual(m["fingertips_mm"]["middle"], 20.0, delta=0.5)
        self.assertAlmostEqual(m["finger_bases_mm"]["index"], 40.0, delta=0.5)
        self.assertGreater(m["hand_length_mm"], 100)                 # 280 px at ~195 mm: about 125 mm in 3D
        self.assertGreater(m["palm_width_mm"], 30)
        self.assertEqual(m["missing_depth_in_hand_pct"], 0.0)

    def test_a_slightly_tilted_camera_still_measures_a_steep_one_is_warned(self):
        depth, lm = scene(tilt_y=0.03)                                # about 3.5 deg: still measured right
        m = hs.measure(depth, INTR, lm)
        self.assertAlmostEqual(m["palm_centre_mm"], 40.0, delta=1.5)
        self.assertNotIn("tilted", " ".join(m["problems"]))
        steep, lm2 = scene(tilt_y=0.2)                                # about 20 deg: warned
        m = hs.measure(steep, INTR, lm2)
        self.assertIn("tilted", " ".join(m["problems"]))

    def test_a_tip_dot_just_past_the_finger_end_still_reads_the_finger(self):
        depth, lm = scene()
        lm[12] = (350, 225)                                           # 5 px past the middle finger's end, on the table
        m = hs.measure(depth, INTR, lm)
        self.assertAlmostEqual(m["fingertips_mm"]["middle"], 20.0, delta=0.5)

    def test_missing_depth_is_warned(self):
        depth, lm = scene()
        depth[200:280, 520:640] = 0
        m = hs.measure(depth, INTR, lm)
        self.assertGreater(m["missing_depth_in_hand_pct"], 5)
        self.assertFalse(m["ok"])


class CollectToolTests(unittest.TestCase):
    def test_file_names_and_the_index(self):
        import hand_scan_collect as tool
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        saved = tool.OUT
        tool.OUT = d
        try:
            depth, lm = scene()
            m = hs.measure(depth, INTR, lm)
            hand = {"landmarks_px": lm.tolist(), "handedness_label": "Left", "handedness_score": 0.98}
            raw = (depth * 10).astype(np.uint16)
            for _ in range(2):
                tool.save(tool.free_name("P07", "L"), "P07", "L", "test", raw, np.zeros((H, W, 3), np.uint8), 0.0001,
                          dict(INTR, width=W, height=H), "260322278767", hand, m)
        finally:
            tool.OUT = saved
        self.assertEqual(sorted(os.listdir(d)), ["P07_L.npz", "P07_L_2.npz", "index.csv"])
        with open(os.path.join(d, "index.csv"), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["palm_centre_mm"], "40.0")
        z = np.load(os.path.join(d, "P07_L.npz"))
        self.assertEqual(z["depth_raw"].shape, (H, W))
        self.assertTrue(tool.ID_RE.match("P07") and not tool.ID_RE.match("Harsh Kumar"))


if __name__ == "__main__":
    unittest.main()
