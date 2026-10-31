"""X and Y jog at any height in the normal Jog too (user 2026-10-31: "remove it everywhere"): below travel Z they move
slowly (F300) inside the work area, Z still stops at travel Z on the way down, and a real-hand job still refuses
sideways jogs."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_pc():
    """webui_server_pc swaps v29's Handler on import - put it back afterwards for the other tests."""
    import webui_server_v29 as v29
    saved = v29.Handler
    import webui_server_pc as pc
    return pc, lambda: (setattr(v29, "Handler", saved), sys.modules.pop("webui_server_pc", None))


class JogAnyHeightTests(unittest.TestCase):
    def setUp(self):
        self.pc, undo = load_pc()
        self.addCleanup(undo)
        self.calls = []
        pc = self.pc
        for p in (patch.object(pc, "_JOG", (lambda q: self.calls.append(("v29", q["axis"])) or {"busy": "jog"}, False)),
                  patch.object(pc.admin_jog, "start_admin_xy",
                               side_effect=lambda *a, **k: self.calls.append(("xy", a[1])) or {"busy": "jog"}),
                  patch.object(pc, "z_floor", return_value=-189.0),
                  patch.object(pc.studio.v10.camera, "status", return_value={"state": "idle"}),
                  patch.dict(pc.studio._ACTIVE, {"subject": "prosthetic"})):
            p.start()
            self.addCleanup(p.stop)
        pc._REQ.admin = False

    def jog(self, axis):
        return self.pc.pc_jog({"axis": axis, "direction": 1, "step_mm": 1.0, "confirm_no_person": True})

    def test_x_and_y_go_through_the_any_height_jog(self):
        with patch.object(self.pc.v26.profiles, "active", return_value={"name": "Belt", "work_area_mm": [225, 176],
                                                                          "motion": {"travel_z": -150.0}}), \
                patch.object(self.pc.v26.profiles, "motion", return_value={"travel_z": -150.0}):
            self.jog("X")
            self.jog("Y")
        self.assertEqual(self.calls, [("xy", "X"), ("xy", "Y")])

    def test_z_keeps_the_normal_rules(self):
        self.jog("Z")
        self.assertEqual(self.calls, [("v29", "Z")])

    def test_real_hand_job_keeps_refusing_sideways(self):
        self.pc.studio._ACTIVE["subject"] = "realhand"
        self.jog("X")
        self.assertEqual(self.calls, [("v29", "X")])             # v29.jog refuses it there

    def test_page_no_longer_locks_x_y_below_travel(self):
        with open(os.path.join(ROOT, "webui", "studio_v29.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertNotIn("X and Y unlock at Z", page)
        self.assertNotIn("if(z<TRAVEL_Z-0.05)allow=false;", page)


if __name__ == "__main__":
    unittest.main()
