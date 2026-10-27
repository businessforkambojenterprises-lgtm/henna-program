"""Studio v29 (user 2026-10-17, step 1): prosthetic jobs may use 1.0 to 6.0 mm nozzle height; real-hand jobs keep the
6 mm minimum; the approved calibration file's range is not edited, only lowered at its lower end in Studio."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class StandoffTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29
        self.v29 = webui_server_v29

    def test_prosthetic_range_starts_at_1_mm_once_v29_starts(self):
        v29 = self.v29
        saved = (v29.v14.MIN_STANDOFF_MM, v29.v10.cal, v29._CAL)
        self.assertEqual(v29.v14.MIN_STANDOFF_MM, 4.0)               # importing v29 changes nothing
        v29.v10.cal = lambda: {"standoff_mm_allowed": [3.0, 6.0], "travel_z": -5.0}
        try:
            v29.install_close_heights()
            self.assertEqual(v29.v14.MIN_STANDOFF_MM, 1.0)
            c = v29.v10.cal()
            self.assertEqual(c["standoff_mm_allowed"], [1.0, 6.0])
            self.assertEqual(c["travel_z"], -5.0)                    # everything else unchanged
            self.assertEqual(v29.v14.standoff_range(), [1.0, 6.0])   # what the Design step offers
            v29.install_close_heights()                              # twice is the same
            self.assertEqual(v29.v10.cal()["standoff_mm_allowed"], [1.0, 6.0])
            with open(v29.__file__, encoding="utf-8") as f:
                self.assertIn("    install_close_heights()" + chr(10), f.read())   # main() applies it
        finally:
            v29.v14.MIN_STANDOFF_MM, v29.v10.cal, v29._CAL = saved

    def test_real_hand_from_1_mm(self):
        """2026-10-27 "unlock speed and height options": a real hand down to the machine's 1 mm (was 6 mm)."""
        v28 = self.v29.v28
        saved = v28.v27.clearance
        v28.v27.clearance = lambda job, s: None
        try:
            with self.assertRaisesRegex(ValueError, "1 mm"):
                v28._real_rules({"subject": "realhand"}, 0.5)
            for ok in (1.0, 2.0, 4.0, 6.0):
                v28._real_rules({"subject": "realhand"}, ok)
        finally:
            v28.v27.clearance = saved

    def test_page_defaults(self):
        with open(self.v29.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("st.value=JOB.subject==='realhand'?6:Math.max(STAND[0],4)", page)   # new designs: 4 mm
        self.assertIn("[...st.options].forEach(o=>{o.disabled=false})", page)         # every height for every job
        self.assertIn("$('pgfeed').disabled=false", page)                              # every speed for every job
        self.assertIn("(close - prosthetic)", page)


if __name__ == "__main__":
    unittest.main()
