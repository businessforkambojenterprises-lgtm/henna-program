"""Hand top never counts the machine head's own pixels (nozzle, dispenser, cable entering from the picture's edge)."""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from step19_hand_path_avg import hand_top_from_raw, head_pixels  # noqa: E402

K = 141.5
LANDMARKS = np.array([[300 + 20 * (i % 7), 20 + 30 * (i // 7)] for i in range(21)], float)   # hand high in the picture


def frame(nozzle=True, hand_mm=175.0, u0=330):
    raw = np.full((480, 848), 2000, np.uint16)                    # bed 200 mm from the camera
    raw[30:110, 290:440] = int(hand_mm * 10)                       # the hand, high in the picture
    if nozzle:
        raw[0:60, u0:u0 + 50] = int((K - 15) * 10)                 # dispenser body, from the top edge
        raw[60:76, u0 + 20:u0 + 28] = int((K + 1.4) * 10)          # the tip: 1.4 mm below its own plane (noise)
    return raw


class HandTopTests(unittest.TestCase):
    def top(self, raw):
        return hand_top_from_raw(raw, 0.0001, LANDMARKS, K, -5.0, lambda d: d)

    def test_nozzle_inside_the_hand_outline_is_not_the_hand_top(self):
        with_nozzle, without = self.top(frame(True)), self.top(frame(False))
        self.assertAlmostEqual(with_nozzle["hand_top_contact_z"], -5.0 - (175.0 - K), places=1)   # the hand, not the tip
        self.assertEqual(with_nozzle["hand_top_contact_z"], without["hand_top_contact_z"])
        self.assertGreater(with_nozzle["head_pixels_excluded"], 0)
        self.assertEqual(without["head_pixels_excluded"], 0)

    def test_a_tall_hand_away_from_the_head_still_counts(self):
        raw = frame(True, hand_mm=K + 2.0, u0=700)                  # 2 mm below the tip plane, apart from the head
        self.assertAlmostEqual(self.top(raw)["hand_top_contact_z"], -7.0, places=1)

    def test_head_mask_is_only_what_touches_the_edge(self):
        raw = frame(True)
        raw[300:320, 600:620] = int((K - 5) * 10)                   # something high but loose in the picture
        head = head_pixels(raw, 0.0001, K, lambda d: d)
        self.assertTrue(head[10, 350] and head[70, 354])           # dispenser and tip
        self.assertFalse(head[310, 610])                            # not connected to the edge: not the head

    def test_a_cable_starting_just_inside_the_edge_is_the_head(self):
        raw = frame(False)
        raw[0:4, :] = 0                                             # no depth in the top rows (D405 edge)
        raw[4:90, 700:712] = int(68.5 * 10)                         # cable hanging in, 68 mm from the camera
        raw[300:320, 600:620] = int((K - 5) * 10)                   # still: a loose thing high in the picture
        head = head_pixels(raw, 0.0001, K, lambda d: d)
        self.assertTrue(head[4, 705] and head[80, 705])
        self.assertFalse(head[310, 610])


if __name__ == "__main__":
    unittest.main()
