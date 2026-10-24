"""Travel clearance over the nozzle's path (henna_preview/path_clearance.py + step_path_clearance.py, user 2026-10-18):
a tall forearm beside the path no longer refuses, the same forearm on the path does, and a path without enough
depth refuses instead of guessing; steps 22 / 23 get the path top only for their own recording."""
import json
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import path_clearance as pc  # noqa: E402

K = 141.5
HEAD = [75.0, 100.0, -5.0]           # scan position, tool X/Y/Z
I3 = np.eye(2)


def scene(forearm_x=(160, 200), forearm_z=-2.0, blind_patch=False):
    """A 1 mm/px top view as a camera of unit rays would see it: bed at Z -62.5, hand (design region) at Z -35 for
    X 110-140, a forearm at Z forearm_z for X in forearm_x. Returns (raw, rays, region pixel mask)."""
    h, w = 120, 240
    vs, us = np.mgrid[0:h, 0:w]
    x = us.astype(float) + 40.0                      # machine X of each pixel (40..279)
    y = vs.astype(float) + 40.0                      # machine Y (40..159)
    z = np.full((h, w), -62.5)
    hand = (x >= 110) & (x <= 140) & (y >= 80) & (y <= 120)
    z[hand] = -35.0
    arm = (x >= forearm_x[0]) & (x <= forearm_x[1]) & (y >= 70) & (y <= 130)
    z[arm] = forearm_z
    depth = HEAD[2] - z + K                          # Z = head_z - (depth - k)
    raw = np.round(depth * 10).astype(np.uint16)     # scale 0.0001 m -> 0.1 mm units
    if blind_patch:
        raw[(x >= 100) & (x <= 140) & (y >= 85) & (y <= 115)] = 0
    rays = ((x - HEAD[0]) / depth, (y - HEAD[1]) / depth)   # camera xy = ray * depth = machine offset (R = I, t = 0)
    return raw, rays, hand


def run(raw, rays, region, holes=False):
    keep = np.ones(raw.shape, bool)
    xy, z, xyb, ok = pc.surface(raw, 0.0001, rays, HEAD, I3, [0, 0], K, lambda d: d, keep)
    if not holes:
        return pc.corridor_top(xy, z, xyb, region[ok], HEAD[:2])
    return pc.corridor_top(xy, z, xyb, region[ok], HEAD[:2], blind_mask=keep & (raw == 0), mm_per_px=1.0, ok_mask=ok)


def holed(raw, x0, x1, y0, y1):
    """Blank out machine X x0..x1, Y y0..y1 (1 mm per pixel, X = col + 40, Y = row + 40)."""
    raw = raw.copy()
    raw[y0 - 40:y1 - 40 + 1, x0 - 40:x1 - 40 + 1] = 0
    return raw


class CorridorTests(unittest.TestCase):
    def test_forearm_beside_the_path_is_ignored(self):
        r = run(*scene(forearm_x=(170, 220)))
        self.assertAlmostEqual(r["top_z"], -35.0, places=1)          # the hand, not the forearm at -2
        self.assertLess(r["whole_view_top_z"], 0)
        self.assertGreater(r["whole_view_top_z"], r["top_z"])          # the whole view does see the forearm

    def test_forearm_on_the_path_counts(self):
        r = run(*scene(forearm_x=(141, 150)))                          # next to the design area, inside the margin
        self.assertAlmostEqual(r["top_z"], -2.0, places=1)
        r = run(*scene(forearm_x=(95, 105)))                           # between the scan position (X75) and the design
        self.assertAlmostEqual(r["top_z"], -2.0, places=1)
        r = run(*scene(forearm_x=(40, 58)))                            # behind the scan position: the tip never goes there
        self.assertAlmostEqual(r["top_z"], -35.0, places=1)

    def test_head_footprint_around_the_scan_position_is_left_out(self):
        r = run(*scene(forearm_x=(70, 82)))                            # within 15 mm of the tip: the head itself
        self.assertAlmostEqual(r["top_z"], -35.0, places=1)

    def test_unmeasured_path_is_refused(self):
        raw, rays, region = scene(forearm_x=(200, 220), blind_patch=True)
        region2 = region.copy()
        region2[:, :] = False
        region2[40:80, 70:100] = True                                   # region still has some measured skin
        with self.assertRaisesRegex(pc.PathClearanceError, "no depth"):
            run(raw, rays, region2)

    def test_a_hole_takes_the_height_of_its_edge(self):
        raw, rays, region = scene(forearm_x=(200, 220))
        r = run(holed(raw, 85, 100, 90, 110), rays, region, holes=True)      # plain bed around it: bed height
        self.assertAlmostEqual(r["top_z"], -35.0, places=1)
        self.assertEqual(r["holes"], 1)
        raw, rays, region = scene(forearm_x=(88, 108))
        r = run(holed(raw, 92, 104, 85, 115), rays, region, holes=True)     # a hole inside a tall forearm on the path
        self.assertAlmostEqual(r["top_z"], -2.0, places=1)

    def test_too_big_or_unbounded_holes_are_refused(self):
        raw, rays, region = scene(forearm_x=(200, 220))
        saved = pc.MAX_HOLE_MM2
        pc.MAX_HOLE_MM2 = 100.0
        try:
            with self.assertRaisesRegex(pc.PathClearanceError, "has no depth"):
                run(holed(raw, 85, 100, 90, 110), rays, region, holes=True)
        finally:
            pc.MAX_HOLE_MM2 = saved

    def test_too_little_region(self):
        raw, rays, region = scene()
        with self.assertRaisesRegex(pc.PathClearanceError, "too little measured depth"):
            run(raw, rays, np.zeros_like(region))


class StepRunnerTests(unittest.TestCase):
    def test_path_top_only_for_the_same_recording(self):
        import step_path_clearance as spc
        cfg = {"hand_capture": {"recording": "20261018_084815_543873.db3"}}
        env = {"HENNA_PATH_TOP": json.dumps({"recording": "20261018_084815_543873.db3", "top_z": -30.5, "basis": "path"})}
        info = spc.path_top(cfg, env)
        self.assertEqual(info["top_z"], -30.5)
        self.assertIsNone(spc.path_top({"hand_capture": {"recording": "other.db3"}}, env))
        self.assertIsNone(spc.path_top(cfg, {}))
        wrapped = spc.wrap(lambda *a: {"hand_top_contact_z": -8.3, "pixels": 9}, info)
        top = wrapped()
        self.assertEqual((top["hand_top_contact_z"], top["whole_hand_top_contact_z"], top["pixels"]), (-30.5, -8.3, 9))
        self.assertEqual(spc.wrap(lambda *a: {"hand_top_contact_z": -8.3}, None)()["hand_top_contact_z"], -8.3)


class StudioInstallTests(unittest.TestCase):
    def test_installed_at_start_from_source_only(self):
        import webui_server_v29 as v29
        saved = (v29.v27.clearance, v29.v10.STEP22, v29.v17.STEP23)
        self.assertIs(v29.v27.clearance, v29._WHOLE_HAND_CLEARANCE)        # importing changes nothing
        try:
            self.assertTrue(v29.install_path_clearance())
            self.assertIs(v29.v27.clearance, v29.path_clearance)
            self.assertTrue(v29.v10.STEP22.endswith("step22_path_clearance.py"))
            self.assertTrue(v29.v17.STEP23.endswith("step23_path_clearance.py"))
            sys.frozen = True
            v29.v27.clearance = saved[0]
            self.assertFalse(v29.install_path_clearance())                  # packaged app: whole-hand rule
            self.assertIs(v29.v27.clearance, saved[0])
        finally:
            if hasattr(sys, "frozen"):
                del sys.frozen
            v29.v27.clearance, v29.v10.STEP22, v29.v17.STEP23 = saved


class TravelLineTests(unittest.TestCase):
    """User 2026-10-22 ("do b"): with the design's strokes known, only the travel lines (start -> first stroke, then
    between strokes) + margin count - a tall wrist beside them no longer refuses, on them it still does."""

    def test_segments_in_program_order(self):
        segs = pc.travel_segments([(65.7, 80)], [((110, 90), (120, 90)), ((125, 95), (125, 95))])
        self.assertEqual(segs, [((65.7, 80.0), (110.0, 90.0)), ((120.0, 90.0), (125.0, 95.0))])
        self.assertEqual(pc.travel_segments([(0, 0)], []), [])

    def test_forearm_beside_the_lines_is_ignored_on_them_counted(self):
        raw, rays, region = scene(forearm_x=(95, 105))           # between the scan position and the design
        keep = np.ones(raw.shape, bool)
        xy, z, xyb, ok = pc.surface(raw, 0.0001, rays, HEAD, I3, [0, 0], K, lambda d: d, keep)
        hull = pc.corridor_top(xy, z, xyb, region[ok], HEAD[:2])
        self.assertAlmostEqual(hull["top_z"], -2.0, places=1)    # the triangle rule sees the forearm
        # travel line that goes round it: scan (75, 100) -> (75, 60) -> design start (115, 85) is two lines here
        beside = [((75, 100), (90, 50)), ((90, 50), (120, 50)), ((120, 50), (125, 100))]
        r = pc.corridor_top(xy, z, xyb, region[ok], HEAD[:2], segments=beside)
        self.assertLess(r["top_z"], -30)
        self.assertIsNone(r["corridor_xy"])
        self.assertIn("travel lines", r["basis"])
        across = [((75, 100), (125, 100))]                       # straight through the forearm at X 95-105
        r = pc.corridor_top(xy, z, xyb, region[ok], HEAD[:2], segments=across)
        self.assertAlmostEqual(r["top_z"], -2.0, places=1)


class StudioTravelLineTests(unittest.TestCase):
    def test_design_strokes_and_the_pending_placement(self):
        import webui_server_v29 as v29
        d = {"svg": "designs/arabic_finger_bel.svg", "fit_mm": 30.0, "rotation_deg": 0.0, "centre_machine_xy": [120, 100]}
        st = v29.design_strokes(d)
        self.assertGreater(len(st), 10)
        for a, b in st:
            self.assertTrue(100 <= a[0] <= 140 and 80 <= a[1] <= 120, a)
        job = {"steps": {"scan": {"mpos": [65.7, 80.0, -5.0]}}}
        saved = v29.machine.status
        v29.machine.status = lambda: {"connected": True, "mpos": [70.0, 90.0, -5.0]}
        try:
            segs = v29.travel_lines(job, d)
        finally:
            v29.machine.status = saved
        self.assertEqual(segs[0][0], (65.7, 80.0))
        self.assertEqual(segs[1][0], (70.0, 90.0))                 # the head's current position too
        self.assertEqual(len(segs), 2 + len(st) - 1)
        self.assertIsNone(v29.travel_lines(job, {"svg": "designs/none.svg"}))   # unreadable: hull rule
        self.assertIsNone(v29.travel_lines(job, None))
        seen = []
        saved_inner = v29._CHECK_DESIGN
        v29._CHECK_DESIGN = (lambda p: seen.append(dict(v29._PENDING.design)) or {"ok": True}, True)
        try:
            v29.check_design(dict(d, job="x", standoff_mm=6))
        finally:
            v29._CHECK_DESIGN = saved_inner
        self.assertEqual(seen[0]["svg"], d["svg"])
        self.assertIsNone(v29._PENDING.design)
        self.assertIs(v29.Handler.POST_API["/api/studio/check_design"][0], v29.check_design)


class HeadIsNotSkinTests(unittest.TestCase):
    """2026-10-24: "the straight way to the scan position crosses skin at Z 64.9" - the head's own cable in the view.
    Points more than HEAD_ZONE_MM nearer to the camera than the tip are the head / a cable, never skin: the path
    check's surface leaves them out; something only a little above the tip (a forearm beside the head) still counts."""

    def test_the_head_is_left_out_a_tall_forearm_is_not(self):
        import numpy as np
        from henna_preview import path_clearance as pc
        raw = np.array([[1500, 750, 1380, 0]], np.uint16)       # 150 mm skin, 75 mm cable, 138 mm (2.7 mm above tip)
        rays = (np.zeros((1, 4)), np.zeros((1, 4)))
        keep = np.ones((1, 4), bool)
        xy, z, xyb, ok = pc.surface(raw, 0.0001, rays, [65.7, 80.0, -0.5], np.eye(2), np.zeros(2), 140.73,
                                    lambda d: d, keep)
        self.assertEqual(ok.tolist(), [[True, False, True, False]])
        np.testing.assert_allclose(z, [-0.5 - (150.0 - 140.73), -0.5 - (138.0 - 140.73)])
        self.assertEqual(len(xy), 2)
        self.assertEqual(len(xyb), 1)                          # the unmeasured pixel stays a blind point


if __name__ == "__main__":
    unittest.main()
