"""Surface offset from nozzle touches: smallest offset, raise only, refusals."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.surface_offset import SurfaceOffsetError, load_offset, offset_from_touches, shifted_depth_fn

TODAY = [{"camera_skin_z": -60.078, "touch_z": -58.681}, {"camera_skin_z": -69.473, "touch_z": -67.770},
         {"camera_skin_z": -66.001, "touch_z": -64.342}]


class Offset(unittest.TestCase):
    def test_applies_the_smallest_offset(self):
        o = offset_from_touches(TODAY, 3.0)
        self.assertEqual(o["per_touch_mm"], [1.397, 1.703, 1.659])
        self.assertEqual(o["applied_mm"], 1.397)

    def test_never_lowers_the_nozzle(self):
        t = [{"camera_skin_z": -60.0, "touch_z": -60.2}, {"camera_skin_z": -61.0, "touch_z": -61.1},
             {"camera_skin_z": -62.0, "touch_z": -61.9}]
        self.assertEqual(offset_from_touches(t, 3.0)["applied_mm"], 0.0)

    def test_disagreeing_touches_are_refused(self):
        t = TODAY[:2] + [{"camera_skin_z": -66.0, "touch_z": -63.0}]
        with self.assertRaises(SurfaceOffsetError):
            offset_from_touches(t, 3.5)

    def test_too_few_or_too_large_is_refused(self):
        with self.assertRaises(SurfaceOffsetError):
            offset_from_touches(TODAY[:2], 3.0)
        big = [{"camera_skin_z": -60.0, "touch_z": -56.8}] * 3
        with self.assertRaises(SurfaceOffsetError):
            offset_from_touches(big, 3.0)

    def test_shift_raises_the_surface(self):
        k, head_z = 141.47, -40.0
        fn = shifted_depth_fn(lambda d: d, 1.4)
        self.assertAlmostEqual(head_z - (fn(165.0) - k) - (head_z - (165.0 - k)), 1.4)
        with self.assertRaises(SurfaceOffsetError):
            shifted_depth_fn(lambda d: d, -0.1)

    def test_record_must_match_recording_and_subject(self):
        rec = {"subject": "prosthetic hand (no person)", "recording": "a.db3", "touches": TODAY}
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(rec, f); f.close()
        self.assertEqual(load_offset(f.name, "a.db3", 3.0)["applied_mm"], 1.397)
        with self.assertRaises(SurfaceOffsetError):
            load_offset(f.name, "b.db3", 3.0)
        rec["subject"] = "person"
        with open(f.name, "w", encoding="utf-8") as g:
            json.dump(rec, g)
        with self.assertRaises(SurfaceOffsetError):
            load_offset(f.name, "a.db3", 3.0)


if __name__ == "__main__":
    unittest.main()
