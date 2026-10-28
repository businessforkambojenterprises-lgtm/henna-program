"""Camera alignment, live (user 2026-10-28: the camera was loose; "add this tool in admin section"): the shift of the
live picture against the daily-check reference in mm, the OK / close / move grade and which way to aim the lens; the
Admin route and card."""
import os
import sys
import unittest

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import camera_align as ca  # noqa: E402


def scene(dx=0.0, dy=0.0):
    rng = np.random.default_rng(1)
    base = cv2.GaussianBlur(rng.random((480, 848)).astype(np.float32), (0, 0), 3) * 255
    for x, y in ((200, 120), (420, 240), (640, 360)):
        cv2.circle(base, (x, y), 9, 0, -1)
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(base, m, (848, 480), borderMode=cv2.BORDER_REFLECT)


class MeasureTests(unittest.TestCase):
    def test_a_shift_down_is_found_in_mm_with_the_way_back(self):
        ref = scene()
        cur = cv2.resize(scene(0.0, 3.5), (424, 240), interpolation=cv2.INTER_AREA)      # half size, like the preview
        r = ca.measure(ref, cur)
        self.assertAlmostEqual(r["y_px"], 3.5, delta=0.5)
        self.assertAlmostEqual(r["shift_mm"], 3.5 * ca.MM_PER_PX, delta=0.3)
        self.assertEqual(r["state"], "move")
        self.assertIn("toward +X", " ".join(r["tips"]))

    def test_in_place_is_ok_without_directions(self):
        r = ca.measure(scene(), cv2.resize(scene(0.2, -0.2), (424, 240), interpolation=cv2.INTER_AREA))
        self.assertEqual((r["state"], r["tips"]), ("ok", []))

    def test_grades(self):
        self.assertEqual(ca.judge(0.0, 1.0)["state"], "close")                          # 0.51 mm
        self.assertEqual(ca.judge(-3.0, 0.0)["tips"], ["aim the lens a little toward -Y"])


class AdminTests(unittest.TestCase):
    """Read from the files: importing webui_server_pc here would change Studio's route table for later tests."""

    def test_route_and_card(self):
        with open(os.path.join(ROOT, "webui_server_pc.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertIn('"/api/admin/camera_align": camera_align_view', src)
        with open(os.path.join(ROOT, "webui", "pc_admin.js"), encoding="utf-8") as f:
            js = f.read()
        for bit in ("id = 'pialign'", "api('/api/admin/camera_align')", "alignStop();"):
            self.assertIn(bit, js)
        with open(os.path.join(ROOT, "webui", "pc_admin.css"), encoding="utf-8") as f:
            self.assertIn("body.pi-daily #pialign", f.read())                    # not in the operators' daily check


if __name__ == "__main__":
    unittest.main()
