"""Studio v26: calibration jog, dot finding + fits, and the production flow (add, set up, approve, recalibrate)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from henna_preview import machine_motion  # noqa: E402
from henna_preview import calib_session  # noqa: E402
from henna_preview.grbl_program_link_v26 import GrblProgramLinkV26, cal_jog_target, nudge_target  # noqa: E402
from henna_preview.grbl_program_link import MachineError  # noqa: E402
import webui_server as base  # noqa: E402
import webui_server_v10 as v10  # noqa: E402
from test_grbl_scan_link import FakeSerial  # noqa: E402

INTR = {"width": 848, "height": 480, "fx": 435.41, "fy": 434.71, "ppx": 424.38, "ppy": 233.95, "model": "none",
        "coeffs": [0, 0, 0, 0, 0]}
_saved = {}


def sheet_frame(depth_mm=200.0):
    """The printed dot sheet as the camera would see it flat at depth_mm (pinhole), with measured depth everywhere."""
    import cv2
    sheet = cv2.imread(os.path.join(ROOT, "machines", "calibration_dot_sheet_A4.png"), cv2.IMREAD_COLOR)
    px_per_mm = INTR["fx"] / depth_mm
    mm_per_sheet_px = 297.0 / sheet.shape[1]
    k = px_per_mm * mm_per_sheet_px
    small = cv2.resize(sheet, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
    img = np.full((480, 848, 3), 255, np.uint8)
    h, w = small.shape[:2]
    y0, x0 = int(INTR["ppy"] - h / 2), int(INTR["ppx"] - w / 2)
    ys, xs = max(0, y0), max(0, x0)
    img[ys:min(480, y0 + h), xs:min(848, x0 + w)] = small[ys - y0:min(480, y0 + h) - y0, xs - x0:min(848, x0 + w) - x0]
    raw = np.full((480, 848), int(depth_mm * 10), np.uint16)               # 0.0001 m units
    return {"colour_rgb": img[:, :, ::-1].copy(), "depth_raw_aligned": raw, "depth_scale_m_per_unit": 0.0001,
            "colour_intrinsics": INTR}


def setUpModule():
    _saved.update(cal=v10.cal, log_row=v10.log_row, out_root=base.OUT_ROOT, root=base.ROOT, log_file=v10.LOG_FILE)
    global v26
    import webui_server_v26
    v26 = webui_server_v26
    v10.cal, v10.log_row = v26.cal, v26.v24.log_row                   # this server's own state, set explicitly
    v26.machine._calibration = v26.link_cfg
    tmp = tempfile.mkdtemp()
    from test_machines_v24 import _temp_log, _test_registry
    v10.LOG_FILE = _temp_log(tmp)
    _saved["tmp"] = tmp
    _test_registry(tmp)                                   # ball-screw back in calibration mode (uncalibrated)
    shutil.copy(os.path.join(ROOT, "current_calibration.json"), tmp)
    shutil.copytree(os.path.join(ROOT, "validation"), os.path.join(tmp, "validation"))
    base.ROOT = tmp
    base.OUT_ROOT = os.path.join(tmp, "outputs", "webui")
    os.makedirs(base.OUT_ROOT)
    p = v26.profiles
    _saved["profiles"] = (p.root, p.registry_path, p.state_path)
    p.root, p.registry_path = tmp, os.path.join(tmp, "machines", "registry.json")
    p.state_path = os.path.join(tmp, "outputs", "active_machine.json")
    v26.v24._log_row = lambda *a, **k: None


def tearDownModule():
    machine_motion.apply(machine_motion.BELT_DRIVE)
    p = v26.profiles
    p.root, p.registry_path, p.state_path = _saved["profiles"]
    v10.cal, v10.log_row = _saved["cal"], _saved["log_row"]
    base.OUT_ROOT, base.ROOT, v10.LOG_FILE = _saved["out_root"], _saved["root"], _saved["log_file"]
    v26.machine._calibration = _saved["cal"]
    v26.machine.cal_floor = None
    shutil.rmtree(_saved["tmp"], True)


def choose(mid):
    v26.machines_select({"id": mid, "confirm_machine": True})


class CalJogTests(unittest.TestCase):
    def test_target_rules(self):
        self.assertEqual(cal_jog_target((80, 80, -20), -1, 1.0, -76), (80, 80, -21.0))
        self.assertEqual(cal_jog_target((80, 80, -75.5), -1, 0.1, -76), (80, 80, -75.6))
        self.assertEqual(cal_jog_target((80, 80, -20), -1, 10.0, -76), (80, 80, -30.0))
        self.assertEqual(cal_jog_target((80, 80, -56), -1, 10.0, -76), (80, 80, -66.0))     # exactly 10 above the floor
        self.assertEqual(cal_jog_target((80, 80, -70), 1, 10.0, -76), (80, 80, -60.0))      # up in 10s from anywhere
        for bad in ((-1, 1.0, -76, -75.5), (-1, 10.0, -76, -60), (-1, 10.0, -76, -56.1), (1, 10.0, -76, -5),
                    (-1, 5.0, -76, -20), (1, 1.0, -76, -0.5), (-1, 1.0, None, -20), (2, 1.0, -76, -20)):
            d, step, floor, z = bad
            with self.assertRaises(MachineError):
                cal_jog_target((80, 80, z), d, step, floor)

    def test_up_from_below_the_floor_and_nudge_rules(self):
        self.assertEqual(cal_jog_target((43.7, 49.3, -38), 1, 1.0, -36), (43.7, 49.3, -37.0))  # touchscreen left it lower
        with self.assertRaises(MachineError):
            cal_jog_target((43.7, 49.3, -38), -1, 0.1, -36)
        self.assertEqual(nudge_target((43.7, 49.3, -35), "Y", 1, 1.0, -36, 225, 176), (43.7, 50.3, -35))
        self.assertEqual(nudge_target((43.7, 49.3, -36), "X", -1, 0.1, -36, 225, 176), (43.6, 49.3, -36))
        for bad in (((43.7, 49.3, -38), "X", 1, 0.1, -36),            # below the floor: raise Z first
                    ((0.05, 49.3, -20), "X", -1, 0.1, -36),           # outside the work area
                    ((224.5, 49.3, -20), "X", 1, 1.0, -36),
                    ((43.7, 49.3, -20), "Z", 1, 1.0, -36), ((43.7, 49.3, -20), "X", 1, 10.0, -36),
                    ((43.7, 49.3, -20), "Y", 1, 1.0, None)):
            with self.assertRaises(MachineError):
                nudge_target(*bad, 225, 176)

    def test_link_moves_z_only_and_xy_stays_locked_below_travel(self):
        fake = FakeSerial(state="Alarm")
        cfg = {"capture_pose_mpos": [145.8, 42.6, -40.0], "travel_z": -20.0, "draw_feed_mm_min": 300.0,
               "travel_feed_mm_min": 1000.0}
        link = GrblProgramLinkV26(lambda: cfg, opener=lambda p: fake, poll_s=0.01, fresh_s=0.3,
                                  homing_timeout=1.0, move_timeout=1.0, status_timeout=0.3)
        link.cal_floor = -23.0
        try:
            link.connect("FAKE", wait_s=0.5)
            link.start_homing()
            self.assertTrue(self._wait(lambda: link.status()["homed"]))
            for _ in range(20):                                    # from Z -3 down to the floor -23
                link.start_cal_jog(-1, 1.0)
                self.assertTrue(self._wait(lambda: not link.status()["busy"]))
            self.assertEqual(link.status()["mpos"][2], -23.0)
            with self.assertRaisesRegex(MachineError, "floor"):
                link.start_cal_jog(-1, 0.1)
            with self.assertRaisesRegex(MachineError, "Raise Z to travel height"):
                link.start_jog("X", 1, 1.0)
            link.start_cal_nudge("X", 1, 1.0)                      # X/Y nudge at the floor (calibration only)
            self.assertTrue(self._wait(lambda: not link.status()["busy"]))
            self.assertEqual([c for c in fake.commands() if c.startswith("$J")][-1], "$J=G21 G90 G53 X1.000 F300")
            with self.assertRaisesRegex(MachineError, "Raise Z to travel height"):
                link.start_jog("Y", 1, 1.0)                        # the normal jog keeps its rule
            sent = [c for c in fake.commands() if c.startswith("$J")]
            self.assertTrue(all(c.startswith("$J=G21 G90 G53 Z") and c.endswith(" F100") or c == "$J=G21 G90 G53 X1.000 F300"
                                for c in sent), sent)
            link.start_cal_jog(1, 10.0)                            # 10 mm steps at the Z max rate
            self.assertTrue(self._wait(lambda: not link.status()["busy"]))
            self.assertEqual([c for c in fake.commands() if c.startswith("$J")][-1], "$J=G21 G90 G53 Z-13.000 F300")
        finally:
            if not link.status()["busy"]:
                link.disconnect()

    @staticmethod
    def _wait(pred, seconds=3.0):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if pred():
                return True
            time.sleep(0.005)
        return pred()


class FitTests(unittest.TestCase):
    def test_dots_found_and_xy_fit_recovers_a_known_mapping(self):
        frame = sheet_frame()
        dots = calib_session.find_dots(frame)
        self.assertEqual(len(dots), 7)
        cam = np.array([d["camera_mm"][:2] for d in dots])
        span = cam.max(0) - cam.min(0)
        self.assertAlmostEqual(span[0], 160.0, delta=2.0)                  # the printed 160 x 100 mm pattern
        self.assertAlmostEqual(span[1], 100.0, delta=2.0)
        head = {"x": 80.0, "y": 80.0, "z": -20.0}
        R = np.array([[0.0, -1.0], [-1.0, 0.0]])                            # mirrored, like the belt-drive camera
        t = np.array([-50.0, 30.0])
        pts = {d["label"]: dict(zip("xy", (np.array([head["x"], head["y"]]) + R @ np.array(d["camera_mm"][:2]) + t)),
                                z=-75.0) for d in dots}
        fit = calib_session.fit_xy({"head": head, "dots": dots}, pts)
        self.assertEqual(fit["verdict"], "GOOD")
        self.assertLess(fit["rms_mm"], 0.05)
        self.assertTrue(fit["mirrored"])
        np.testing.assert_allclose(fit["t_mm"], t, atol=0.05)

    def test_small_hand_drawn_dots_in_shadow_but_not_wood_or_label_text(self):
        """Like the real 2026-10-13 scan: ~4.5 mm dots, a shadow over the top row, wood and a taped label beside."""
        import cv2
        img = np.full((480, 848, 3), 150, np.uint8)                       # paper at 150, in this light
        img[:, 620:] = (103, 90, 72)                                       # brown wood beside the paper (as measured)
        img[:120, :620] = 98                                               # head shadow over the top of the paper
        for x in range(640, 848, 23):                                      # wood grain blobs
            cv2.circle(img, (x, 300 + (x % 50)), 4, (45, 32, 20), -1)
        img[250:300, 780:830] = 205                                        # white tape label ...
        cv2.putText(img, "H+", (785, 285), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (140, 140, 140), 2)   # ... with grey text
        dots = [(264, 51), (472, 51), (267, 258), (371, 257), (475, 254), (271, 460), (475, 454)]
        for u, v in dots:
            cv2.circle(img, (u, v), 4, (35, 35, 35) if v < 120 else (65, 65, 65), -1)          # about 4.5 mm at 209 mm
        raw = np.full((480, 848), 2090, np.uint16)
        frame = {"colour_rgb": img, "depth_raw_aligned": raw, "depth_scale_m_per_unit": 0.0001, "colour_intrinsics": INTR}
        found = calib_session.find_dots(frame)
        self.assertEqual([tuple(round(c) for c in f["pixel_uv"]) for f in found], dots)   # all 7, in reading order
        self.assertEqual([f["label"] for f in found], [f"D{i}" for i in range(1, 8)])

    def test_z_fit_recovers_k(self):
        frame = sheet_frame(depth_mm=162.0)                                 # a flat block top 38 mm above a 200 mm bed
        head = {"x": 80.0, "y": 80.0, "z": -20.0}
        xy = {"R": [[0.0, -1.0], [-1.0, 0.0]], "t_mm": [-50.0, 30.0]}
        k = 141.5
        touches = [{"label": f"T{i}", "x": x, "y": y, "z_contact": head["z"] - (162.0 - k)}
                   for i, (x, y) in enumerate(((50, 80), (60, 70), (40, 90)), 1)]
        r = calib_session.fit_z(frame, head, touches, xy)
        self.assertAlmostEqual(r["k_mm"], k, delta=0.05)
        self.assertEqual(r["verdict"], "GOOD")


class ProductionFlowTests(unittest.TestCase):
    def tearDown(self):
        machine_motion.apply(machine_motion.BELT_DRIVE)

    def test_add_setup_calibrate_approve_recalibrate(self):
        r = v26.machines_add({"name": "Unit 3 (ball screw)", "drive": "ball screw", "confirm_new": True})
        mid = [m for m in r["machines"] if m["name"] == "Unit 3 (ball screw)"][0]["id"]
        self.assertEqual(mid, "unit-3-ball-screw")
        choose(mid)
        m = v26.profiles.get(mid)
        self.assertTrue(v26.profiles.summary()["machines"][-1]["needs_setup"])
        with self.assertRaises(v26.MachineProfileError):
            v10.cal()                                                        # locked: nothing set up
        v26.setup_area({"work_area_x": 300, "work_area_y": 200, "confirm_measured": True})
        self.assertEqual(machine_motion.current()["X_MAX"], 300.0)           # its own Jog box
        real_status = v26._fresh_idle_status
        v26._fresh_idle_status = lambda: {"mpos": [100.04, 90.0, 0.0], "line": "<Idle|MPos:100.04,90,0>"}
        try:
            with self.assertRaisesRegex(ValueError, "too|needs"):
                v26.setup_scan({"tip_height_mm": 50, "confirm_measured": True})          # tip 30 mm at travel: refused
            v26.setup_scan({"tip_height_mm": 90, "scan_z": -20, "confirm_measured": True})
        finally:
            v26._fresh_idle_status = real_status
        m = v26.profiles.get(mid)
        self.assertEqual(m["motion"]["scan_pose_mpos"], [100.0, 90.0, -20.0])
        self.assertEqual(m["motion"]["firmware_z_floor"], -86.0)
        self.assertTrue(v26.profiles.calibration_mode(m))
        self.assertEqual(machine_motion.current()["POSE"], (100.0, 90.0, -20.0))
        self.assertEqual(v26.machine.cal_floor, -86.0)
        # a finished session with GOOD fits
        sess = v26._session(m)
        s = sess.load()
        s.update(xy_scan={"recording": "xy.db3", "head": {"x": 100, "y": 90, "z": -20}, "dots": [], "overlay": ""},
                 xy_fit={"verdict": "GOOD", "R": [[0, -1], [-1, 0]], "t_mm": [-50, 30], "dots_used": 7, "rms_mm": 0.6,
                         "dots": []},
                 z_scan={"recording": "z.db3", "head": {"x": 100, "y": 90, "z": -20}},
                 z_touches=[{"label": "T1", "x": 1, "y": 2, "z_contact": -40}],
                 z_fit={"verdict": "GOOD", "k_mm": 141.2, "k_spread_mm": 0.4, "touches": []})
        sess.save(s)
        import henna_preview.daily_check as dc
        real_serial = dc.recording_serial
        dc.recording_serial = lambda path: "260322278767"
        try:
            with self.assertRaisesRegex(ValueError, "name"):
                v26.approve({"confirm_dots": True, "confirm_touches": True, "confirm_air_run": True})
            v26.approve({"approver": "Tester", "confirm_dots": True, "confirm_touches": True, "confirm_air_run": True})
        finally:
            dc.recording_serial = real_serial
        m = v26.profiles.get(mid)
        self.assertTrue(v26.profiles.calibrated(m))
        cal = v10.cal()
        self.assertEqual(cal["capture_pose_mpos"], [100.0, 90.0, -20.0])
        self.assertEqual(cal["firmware_z_floor"], -86.0)
        self.assertEqual(cal["camera_serial"], "260322278767")
        with open(os.path.join(base.ROOT, cal["z_calibration"]), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["result"]["k_mm"], 141.2)
        self.assertIsNone(v26.machine.cal_floor)                             # calibration jog off once calibrated
        with self.assertRaises(v26.MachineProfileError):
            v26.session_get()                                                # calibration tools only in calibration mode
        v26.recalibrate({"by": "Tester", "confirm_recalibrate": True})
        m = v26.profiles.get(mid)
        self.assertFalse(v26.profiles.calibrated(m))
        self.assertTrue(v26.profiles.calibration_mode(m))
        self.assertEqual(len(m["previous_calibrations"]), 1)
        hist = os.listdir(os.path.join(base.ROOT, "machines", "history"))
        self.assertGreaterEqual(len(hist), 4)                                # every registry change kept

    def test_rules_routes_work_in_calibration_mode(self):
        choose("ball-screw")
        import webui_server_v14 as v14
        import webui_server_v16 as v16
        self.assertEqual(v14.v14_rules()["standoff_mm_allowed"], [4.0, 6.0])     # the page's start-up request
        self.assertEqual(v16.v16_rules()["approved_feed_mm_min"], 300.0)
        c = v10.cal()
        for k in ("xy_calibration", "z_calibration", "depth_scale"):
            self.assertNotIn(k, c)                                                # never the camera-to-nozzle parts
        self.assertEqual(c["capture_pose_mpos"], [65.7, 80.0, -5.0])

    def test_belt_drive_cannot_use_the_calibration_tools(self):
        choose("belt-drive")
        with self.assertRaises(v26.MachineProfileError):
            v26.jog_z({"direction": -1, "step_mm": 1.0, "confirm_targets_only": True})
        self.assertIsNone(v26.machine.cal_floor)

    def test_page(self):
        with open(v26.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        for s in ('id="calibcard"', 'id="setupcard"', 'id="maddgo"', 'id="mrecalgo"', "/api/calibration/jog_z",
                  "/api/calibration/approve", "data-cz", "data-cxy", "/api/calibration/jog_xy"):
            self.assertIn(s, page)


if __name__ == "__main__":
    unittest.main()
