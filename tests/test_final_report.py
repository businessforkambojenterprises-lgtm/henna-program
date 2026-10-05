"""Step 7: the final report refuses broken chains and always says motion_ready=false."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.final_report import ReportError, build_final_report
from henna_preview.hand_detect import MODEL_SHA256


class Chain(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.hand, self.reg, self.des = (os.path.join(self.t, n) for n in ("hand", "reg", "des"))
        for d in (self.hand, self.reg, self.des):
            os.makedirs(d)
        self.hand_rep = {"input": {"sha256": "abc", "frame_index": 7, "device": {}, "streams": {}},
                         "model": {"sha256": MODEL_SHA256}, "units": {"depth_scale_m_per_unit": 1e-4, "units_source": "x"},
                         "colour_intrinsics": {}, "hand_detected": True, "hands": [{"score": 0.9}], "timings_s": {}}
        self.region = {"accepted": True, "source": {"recording": "none", "recording_sha256": "abc", "frame_index": 7,
                                                    "hand_run": self.hand},
                       "suggestion": {"edited_by_user": False}, "polygon_px": [[0, 0], [5, 0], [5, 5]], "stats": {},
                       "missing_depth_policy": "excluded"}
        self.design = {"motion_ready": False, "inputs": {"recording_sha256": "abc", "frame_index": 7},
                       "params": {}, "planned": {}, "warnings": [], "timings_s": {},
                       "validity": {"path_points_measured": 3, "path_points_interpolated": 0, "planned_samples": 3}}

    def tearDown(self):
        shutil.rmtree(self.t)

    def build(self):
        for d, obj, name in ((self.hand, self.hand_rep, "report.json"), (self.reg, self.region, "region.json"),
                             (self.des, self.design, "design_report.json")):
            json.dump(obj, open(os.path.join(d, name), "w"))
        return build_final_report(self.des, self.reg, check_recording=False)

    def test_good_chain(self):
        r = self.build()
        self.assertIs(r["motion_ready"], False)
        self.assertIsNone(r["gcode"])
        self.assertIsNone(r["machine_coordinates"])
        self.assertEqual(r["points"]["interpolated"], 0)

    def test_unaccepted_region_refused(self):
        self.region["accepted"] = False
        with self.assertRaises(ReportError):
            self.build()

    def test_mismatched_frame_refused(self):
        self.design["inputs"]["frame_index"] = 8
        with self.assertRaises(ReportError):
            self.build()

    def test_unapproved_model_refused(self):
        self.hand_rep["model"]["sha256"] = "0" * 64
        with self.assertRaises(ReportError):
            self.build()

    def test_motion_ready_true_refused(self):
        self.design["motion_ready"] = True
        with self.assertRaises(ReportError):
            self.build()

    def test_changed_recording_refused(self):
        p = os.path.join(self.t, "rec.db3")
        open(p, "wb").write(b"changed")
        self.region["source"]["recording"] = p
        for d, obj, name in ((self.hand, self.hand_rep, "report.json"), (self.reg, self.region, "region.json"),
                             (self.des, self.design, "design_report.json")):
            json.dump(obj, open(os.path.join(d, name), "w"))
        with self.assertRaises(ReportError):
            build_final_report(self.des, self.reg, check_recording=True)


if __name__ == "__main__":
    unittest.main()
