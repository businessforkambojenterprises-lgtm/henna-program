"""The "too near" pause needs a share of the skin at the tip (2026-10-27: two runs paused "skin within 0.7 / 1.4 mm of the
nozzle tip" while dropping to the first stroke; the saved pictures had 2-3 points at the tip's own height - the tip
itself - and the skin 6-11 mm below). Skin really coming up to the tip still pauses."""
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import hand_watch as hw  # noqa: E402

TIP = (120.0, 100.0, -34.1)


def skin(z, x=(100, 140), y=(80, 120)):
    xs, ys = np.meshgrid(np.arange(x[0], x[1], 0.5), np.arange(y[0], y[1], 0.5))
    return np.column_stack([xs.ravel(), ys.ravel(), np.full(xs.size, float(z))])


class NearShareTests(unittest.TestCase):
    def setUp(self):
        self.ref = hw.Reference(skin(-40.5))

    def test_the_tips_own_pixels_do_not_pause(self):
        live = skin(-40.5)
        d = np.hypot(live[:, 0] - TIP[0], live[:, 1] - TIP[1])
        tip_px = np.flatnonzero(d <= 1.0)[:2]                       # two points at the tip's own height
        live[tip_px, 2] = TIP[2] - 0.5
        r = hw.evaluate(self.ref, live, TIP)
        self.assertEqual(r["verdict"], "ok", r)
        self.assertGreater(r["near_gap_mm"], 5.0)

    def test_skin_coming_up_to_the_tip_pauses(self):
        live = skin(-40.5)
        d = np.hypot(live[:, 0] - TIP[0], live[:, 1] - TIP[1])
        live[d <= 6.0, 2] = TIP[2] - 1.0                            # a bump right under the tip, 1 mm away
        self.assertEqual(hw.evaluate(self.ref, live, TIP)["verdict"], "near")
        half = skin(-40.5)
        close = np.flatnonzero(np.hypot(half[:, 0] - TIP[0], half[:, 1] - TIP[1]) <= hw.NEAR_XY_MM)
        half[close[: len(close) // 2], 2] = TIP[2] - 0.8                 # half the skin at the tip
        self.assertEqual(hw.evaluate(self.ref, half, TIP)["verdict"], "near")


if __name__ == "__main__":
    unittest.main()
