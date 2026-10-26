"""Air run pauses with the bed empty (user 2026-10-26: "this is an error nothing is in design area"): the in-between
depths along the nozzle's edge are left out of an air run's check (the head's outline grown AIR_HEAD_GROW_PX), real-hand
runs keep HEAD_GROW_PX, and every watch pause saves the picture it judged."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import hand_watch as hw  # noqa: E402

K = 140.0


def picture():
    """A flat bed 220 mm away, the nozzle reaching in from the top edge at the tip's depth, and a 6 px fringe of
    in-between depths round it (how the D405 reads an edge)."""
    import cv2
    h, w = 120, 160
    d = np.full((h, w), 220.0)
    head = np.zeros((h, w), np.uint8)
    head[0:40, 70:90] = 1
    d[head > 0] = K + 1.0
    ring = cv2.dilate(head, np.ones((13, 13), np.uint8)) - head
    d[ring > 0] = 180.0
    return (d * 10).astype(np.uint16)


def rays(shape):
    h, w = shape
    v, u = np.mgrid[0:h, 0:w].astype(float)
    return (u - w / 2) / 400.0, (v - h / 2) / 400.0


class HeadEdgeTests(unittest.TestCase):
    def points(self, grow):
        raw = picture()
        return hw.machine_points(raw, 0.0001, rays(raw.shape), [100.0, 100.0, -20.0], np.eye(2), np.zeros(2), K,
                                 lambda x: x, head_grow_px=grow)

    def test_the_fringe_counts_for_a_real_hand_but_not_an_air_run(self):
        bed_z = -20.0 - (220.0 - K)
        tall = lambda pts: int(np.sum(pts[:, 2] > bed_z + hw.EMPTY_MM))      # noqa: E731
        self.assertGreater(tall(self.points(hw.HEAD_GROW_PX)), 50)
        self.assertEqual(tall(self.points(hw.AIR_HEAD_GROW_PX)), 0)

    def test_check_sample_picks_the_grow_by_run_kind(self):
        import webui_server_v28 as v28
        seen = []

        def fake_points(*a, head_grow_px=None, **kw):
            seen.append(head_grow_px)
            return np.zeros((0, 3))
        sample = {"depth": picture(), "scale": 0.0001, "intr": {"fx": 1}, "stride": 1, "t": 0.0}
        with patch.object(v28.hw, "machine_points", fake_points), patch.object(v28, "rays_for", return_value=None), \
                patch.object(v28, "_parts", return_value={"R": np.eye(2), "t": np.zeros(2), "k": K,
                                                          "depth_fn": lambda x: x}), \
                patch.object(v28.hw, "evaluate", return_value={"verdict": "ok"}):
            ref = type("R", (), {})()
            v28.check_sample(ref, sample, [0, 0, 0])
            ref.expect_clear = True
            v28.check_sample(ref, sample, [0, 0, 0])
        self.assertEqual(seen, [hw.HEAD_GROW_PX, hw.AIR_HEAD_GROW_PX])

    def test_a_pause_saves_the_picture(self):
        import webui_server_v28 as v28
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        sample = {"depth": picture(), "scale": 0.0001, "intr": {"fx": 1, "width": 160, "height": 120}, "stride": 4,
                  "t": 12.5}
        with patch.dict(v28.LIVE, {"last_sample": (sample, [1, 2, 3], {"verdict": "moved", "share_off": 0.03})}), \
                patch.object(v28.base, "ROOT", d):
            path = v28._save_pause_picture({"id": "j"}, "something is in the design area")
        with np.load(path) as z:
            np.testing.assert_array_equal(z["depth"], sample["depth"])
            meta = json.loads(str(z["meta_json"]))
        self.assertEqual((meta["head"], meta["result"]["share_off"], meta["stride"]), ([1, 2, 3], 0.03, 4))


if __name__ == "__main__":
    unittest.main()
