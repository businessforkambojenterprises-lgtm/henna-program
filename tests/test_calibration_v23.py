"""Studio v23 calibration section: status (read-only), daily-check comparison, reference storage, routes, page."""
import json
import os
import shutil
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import calibration_status, daily_check  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_pristine  # noqa: E402

studio_pristine.remember()           # the older modules as loaded, before any v24+ test imports a later Studio


def setUpModule():
    global _PRISTINE
    _PRISTINE = studio_pristine.apply()


def tearDownModule():
    studio_pristine.undo(_PRISTINE)


def scene(offset=0.0, shift_px=(0, 0), tilt_mm_per_px=0.0, blob=None, serial="260322278767"):
    """A textured flat bed at 200 mm, as daily_check.summarize returns it."""
    h, w = 480, 848
    rng = np.random.default_rng(7)
    tex = rng.random((h + 40, w + 40)).astype(np.float32)
    import cv2
    tex = cv2.GaussianBlur(tex, (0, 0), 3) * 255
    dx, dy = shift_px
    gray = tex[20 + dy:20 + dy + h, 20 + dx:20 + dx + w].copy()
    v, u = np.mgrid[0:h, 0:w]
    depth = (200.0 + offset + tilt_mm_per_px * (u - w / 2)).astype(np.float32)
    if blob:
        depth[blob] -= 30.0                       # something 30 mm tall on the bed
    return {"depth_mm": depth, "gray": gray, "fx": 435.41, "median_depth_mm": float(np.median(depth)), "serial": serial}


class StatusTests(unittest.TestCase):
    def test_reads_the_approved_calibration(self):
        with open(os.path.join(ROOT, "current_calibration.json"), encoding="utf-8") as f:
            cal = json.load(f)
        s = calibration_status.status(ROOT, cal)
        self.assertTrue(s["ok"])
        by = {i["key"]: i for i in s["items"]}
        self.assertEqual(by["z_calibration"]["values"]["k_mm"], 141.47)
        self.assertEqual(by["depth_scale"]["values"]["g"], 0.97919)
        self.assertEqual(by["xy_calibration"]["values"]["block_shift_rms_mm"], 0.43)
        self.assertEqual(len(by["xy_calibration"]["sha256"]), 12)

    def test_missing_file_is_reported_not_raised(self):
        s = calibration_status.status(ROOT, {"xy_calibration": "nope.json", "z_calibration": "nope.json",
                                             "depth_scale": "nope.json"})
        self.assertFalse(s["ok"])
        self.assertTrue(all(i["error"] for i in s["items"]))


class CompareTests(unittest.TestCase):
    def test_unchanged_is_ok(self):
        r = daily_check.compare(scene(), scene())
        self.assertEqual(r["verdict"], "OK")
        self.assertLess(abs(r["height_mm"]), 0.01)
        self.assertLess(r["sideways_mm"], 0.05)

    def test_height_drift_grades(self):
        self.assertEqual(daily_check.compare(scene(), scene(offset=0.8))["verdict"], "CHECK")
        r = daily_check.compare(scene(), scene(offset=-1.5))
        self.assertEqual(r["verdict"], "RECALIBRATE")
        self.assertAlmostEqual(r["height_mm"], -1.5, places=2)

    def test_sideways_shift_is_found_in_mm(self):
        r = daily_check.compare(scene(), scene(shift_px=(3, 0)))
        self.assertAlmostEqual(r["sideways_mm"], 3 * 200 / 435.41, delta=0.03)
        self.assertEqual(r["verdict"], "RECALIBRATE")
        r = daily_check.compare(scene(), scene(shift_px=(0, 1)))      # one pixel = 0.46 mm: sub-pixel accuracy
        self.assertAlmostEqual(r["sideways_mm"], 200 / 435.41, delta=0.03)
        self.assertEqual(r["verdict"], "CHECK")

    def test_tilt_is_found(self):
        mm_per_px = 200 / 435.41
        r = daily_check.compare(scene(), scene(tilt_mm_per_px=mm_per_px * np.tan(np.radians(0.25))))
        self.assertAlmostEqual(r["tilt_deg"], 0.25, delta=0.02)
        self.assertEqual(r["verdict"], "CHECK")

    def test_something_on_the_bed_is_unclear(self):
        r = daily_check.compare(scene(), scene(blob=(slice(150, 330), slice(250, 600))))
        self.assertEqual(r["verdict"], "UNCLEAR")
        self.assertIn("something is on the bed", r["reasons"][0])

    def test_a_differently_set_up_bed_is_unclear_not_recalibrate(self):
        # 2026-10-24: the jig board instead of the reference's dot sheet - the pictures do not match (0.18), the
        # "shift" found is meaningless (49.6 mm) - say so, do not ask for a recalibration
        other = scene()
        other["gray"] = np.random.default_rng(99).random(other["gray"].shape).astype(np.float32) * 255
        r = daily_check.compare(scene(), other)
        self.assertEqual(r["verdict"], "UNCLEAR")
        self.assertLess(r["picture_match"], daily_check.MIN_MATCH)
        self.assertIn("does not match the reference", r["reasons"][0])
        self.assertEqual(daily_check.compare(scene(), scene(shift_px=(3, 0)))["verdict"], "RECALIBRATE")  # a real move

    def test_other_camera_means_recalibrate(self):
        r = daily_check.compare(scene(), scene(serial="999"))
        self.assertEqual(r["verdict"], "RECALIBRATE")
        self.assertFalse(r["same_camera"])


class ReferenceTests(unittest.TestCase):
    def test_save_load_and_keep_the_old_one(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        daily_check.save_reference(d, scene(), {"created": "2026-10-13T09:00:00"})
        daily_check.save_reference(d, scene(offset=1), {"created": "2026-10-14T09:00:00"})
        ref = daily_check.load_reference(d)
        self.assertEqual(ref["created"], "2026-10-14T09:00:00")
        self.assertAlmostEqual(float(np.median(ref["depth_mm"])), 201.0, places=3)
        self.assertTrue(os.path.isfile(os.path.join(d, "old", "2026-10-13T090000", "reference.json")))
        self.assertIsNone(daily_check.load_reference(os.path.join(d, "none")))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import webui_server_v23 as v23
        cls.v23 = v23

    def test_routes_and_page(self):
        H = self.v23.Handler
        self.assertIn("/api/calibration/status", H.GET_API)
        for p in ("/api/calibration/scan_position", "/api/calibration/check", "/api/calibration/reference"):
            self.assertTrue(H.POST_API[p][1], f"{p} must hold the run lock")
        self.assertIn("/api/studio/scan", H.POST_API)           # v22 routes are all still there
        with open(self.v23.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        for s in ('id="v-calib"', 'id="btncal"', "function calibShow", "/api/calibration/check", "calib:showCalib"):
            self.assertIn(s, page)

    def test_confirmations_are_required(self):
        v23 = self.v23

        class FakeMachine:
            calls = 0

            def start_scan_position(self):
                FakeMachine.calls += 1
                return {"busy": "scan_position"}

        real = v23.machine
        v23.machine = FakeMachine()
        try:
            with self.assertRaises(ValueError):
                v23.cal_scan_position({"confirm_clear": True})
            self.assertEqual(FakeMachine.calls, 0)
            v23.cal_scan_position({"confirm_clear": True, "confirm_estop": True})
            self.assertEqual(FakeMachine.calls, 1)
            with self.assertRaises(ValueError):
                v23.cal_check({})
            with self.assertRaises(ValueError):
                v23.cal_reference({"confirm_empty": True})
        finally:
            v23.machine = real

    def test_check_needs_a_reference(self):
        v23 = self.v23
        real = v23.REF_DIR
        v23.REF_DIR = tempfile.mkdtemp()
        try:
            with self.assertRaisesRegex(ValueError, "No reference yet"):
                v23.cal_check({"confirm_empty": True})
        finally:
            shutil.rmtree(v23.REF_DIR, True)
            v23.REF_DIR = real

    def test_refused_recording_leaves_no_folder(self):
        v23 = self.v23

        class Refusing:
            def capture_guard(self):
                raise v23.MachineError("Move to scan position and verify it before recording")

        real_m, real_dir = v23.machine, v23.CHECK_DIR
        v23.machine, v23.CHECK_DIR = Refusing(), tempfile.mkdtemp()
        try:
            with self.assertRaises(v23.MachineError):
                v23._record("20261013-091748")
            self.assertEqual(os.listdir(v23.CHECK_DIR), [])
        finally:
            shutil.rmtree(v23.CHECK_DIR, True)
            v23.machine, v23.CHECK_DIR = real_m, real_dir

    def test_status_route(self):
        s = self.v23.cal_status()
        self.assertTrue(s["ok"])
        self.assertIn("limits", s)
        self.assertIn("checks", s)


if __name__ == "__main__":
    unittest.main()
