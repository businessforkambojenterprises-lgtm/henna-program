"""Faster preparing (user 2026-10-26: "suggest ways to wind up everything in 2 to 3 minutes" -> "build 1 and 2"):
the array / plain-float deprojection agrees with the RealSense SDK, the pixel search finds the same pixels with the
per-frame point cloud, Scan & start for a setup prepares and then starts, and every step's seconds are reported."""
import os
import sys
import unittest
from unittest.mock import patch

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import region, zcal  # noqa: E402

D405 = {"width": 848, "height": 480, "fx": 435.4101257324219, "fy": 434.7133483886719, "ppx": 424.37677001953125,
        "ppy": 233.95225524902344, "model": "inverse_brown_conrady",
        "coeffs": [-0.05318453907966614, 0.0563274547457695, 0.0002754419401753694, 0.0005459064850583673,
                   -0.018623869866132736]}

try:
    import pyrealsense2  # noqa: F401
    HAVE_SDK = True
except ImportError:
    HAVE_SDK = False


@unittest.skipUnless(HAVE_SDK, "the RealSense SDK is not installed here")
class DeprojectTests(unittest.TestCase):
    def test_arrays_and_single_pixels_agree_with_the_sdk(self):
        rng = np.random.default_rng(3)
        us, vs, z = rng.uniform(0, 847, 400), rng.uniform(0, 479, 400), rng.uniform(0.1, 0.3, 400)
        sdk = region._deproject_sdk(us, vs, z, D405) * 1000
        self.assertTrue(region._fast_ok(D405))
        self.assertLess(np.abs(region.deproject_mm(us, vs, z, D405) - sdk).max(), 1e-3)          # arrays
        one = np.vstack([region.deproject_mm([u], [v], [d], D405) for u, v, d in zip(us[:50], vs[:50], z[:50])])
        self.assertLess(np.abs(one - sdk[:50]).max(), 1e-3)                                    # one pixel

    def test_an_unknown_lens_model_uses_the_sdk(self):
        lens = dict(D405, model="kannala_brandt4")
        self.assertIsNone(region._deproject_numpy([1.0], [2.0], [0.2], lens))
        self.assertFalse(region._fast_ok(lens))


@unittest.skipUnless(HAVE_SDK, "the RealSense SDK is not installed here")
class FindPixelTests(unittest.TestCase):
    def test_same_pixels_as_one_by_one(self):
        rng = np.random.default_rng(5)
        v, u = np.mgrid[0:480, 0:848]
        raw = (2000 + 300 * np.sin(u / 40.0) + 200 * np.cos(v / 30.0)).astype(np.uint16)
        raw[rng.random(raw.shape) < 0.05] = 0
        frame = {"depth_raw_aligned": raw, "depth_scale_m_per_unit": 0.0001, "colour_intrinsics": D405}
        for cam in rng.uniform([-60, -40], [60, 40], (40, 2)):
            got = zcal.find_pixel(frame, cam)
            with patch.object(zcal, "_cloud", lambda r, s, i: _one_by_one(r, s, i)):
                want = zcal.find_pixel(frame, cam)
            self.assertEqual(got, want)


def _one_by_one(raw, scale, intr):
    """The old way: every pixel through the SDK on its own."""
    h, w = raw.shape
    c = np.full((h, w, 3), np.nan)
    vs, us = np.nonzero(raw > 0)
    c[vs, us] = region._deproject_sdk(us, vs, raw[vs, us].astype(float) * scale, intr) * 1000.0
    return c


class SetupStartTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.job = {"id": "20261026-160000-job", "subject": "realhand", "setup": {"id": "s-1", "name": "Bel"},
                    "steps": {"scan": {"name": "x.db3"}}}
        self.prep = {"followed": {"shift_mm": 2.0, "turn_deg": 1.0, "fit_mm": 0.3}, "done": ["scan", "program written"],
                     "timing_s": {"the scan": 9.0, "the design check": 5.1, "writing the program": 7.2, "total": 25.0}}

    def _patches(self, run_side=None):
        v29 = self.v29
        ps = [patch.object(v29.v11, "job_load", return_value=self.job),
              patch.object(v29, "_load_setup", return_value={"design": {}}),
              patch.object(v29, "_follow_and_prepare", return_value=self.prep),
              patch.object(v29, "run", side_effect=run_side, return_value={"state": "Run", "job": {"id": "j"}}),
              patch.object(v29.machine, "status", return_value={"mpos": [0, 0, 0]}),
              patch.object(v29, "_log")]
        mocks = [p.start() for p in ps]
        for p in ps:
            self.addCleanup(p.stop)
        return mocks

    def test_prepares_without_moving_then_starts(self):
        mocks = self._patches()
        out = self.v29.setup_start({"job": self.job["id"], "confirm_clear": True, "confirm_estop": True})
        self.assertFalse(mocks[2].call_args.kwargs["allow_move"])        # the head is at the scan position already
        self.assertTrue(mocks[2].call_args.kwargs["write_program"])
        mocks[3].assert_called_once()
        self.assertEqual(out["prepared"]["timing_s"]["total"], 25.0)

    def test_needs_a_setup_job_and_the_ticks(self):
        self._patches()
        with self.assertRaisesRegex(ValueError, "emergency stop"):
            self.v29.setup_start({"job": self.job["id"]})
        self.job.pop("setup")
        with self.assertRaisesRegex(ValueError, "not made from a setup"):
            self.v29.setup_start({"job": self.job["id"], "confirm_clear": True, "confirm_estop": True})


if __name__ == "__main__":
    unittest.main()
