"""The live view's program path (user 2026-10-27: "i want live visualize like ugs"): every move of the job's written
program with its kind (1 drawing, 0 travel / hop) and its index among the command lines the link counts; the paste
program when there is one; nothing for a job without a program."""
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PROGRAM = """(DESIGN HOVER TEST)
G21
G90
G53 G0 Z-0.500
G53 G0 X100.000 Y100.000
G53 G1 X100.000 Y100.000 Z-30.000 F300
M3 S1000
G53 G1 X110.000 Y100.000 Z-30.000 F300
M5
G53 G0 Z-25.000
"""


class ProgramPathTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.file = os.path.join(self.dir, "DESIGN_PASTE_x.gcode")
        with open(self.file, "w", encoding="ascii") as f:
            f.write(PROGRAM)

    def job(self, program):
        return {"id": "j", "steps": {"scan": {"mpos": [65.7, 150.0, -0.5]}, "program": program}}

    def test_moves_kinds_and_line_numbers(self):
        job = self.job({"program": "p", "program_file": "x", "dispense": {"file": self.file}})
        with patch.object(self.v29.v11, "job_load", return_value=job), \
                patch.object(self.v29.v10, "out_dir", return_value=self.dir):
            r = self.v29.program_path({"job": ["j"]})
        self.assertEqual(r["moves"], [[65.7, 150.0, -0.5, 0], [100.0, 100.0, -0.5, 0], [100.0, 100.0, -30.0, 1],
                                      [110.0, 100.0, -30.0, 1], [100 + 10.0, 100.0, -25.0, 0]])
        self.assertEqual(r["line"], [2, 3, 4, 6, 8])                         # G21, G90, M3, M5 are not moves
        self.assertEqual((r["lines"], r["start"]), (9, [65.7, 150.0, -0.5]))

    def test_no_program_or_a_file_elsewhere_gives_nothing(self):
        with patch.object(self.v29.v11, "job_load", return_value=self.job({})):
            self.assertEqual(self.v29.program_path({"job": ["j"]})["moves"], [])
        job = self.job({"program": "p", "program_file": self.file})
        with patch.object(self.v29.v11, "job_load", return_value=job), \
                patch.object(self.v29.v10, "out_dir", return_value=os.path.join(self.dir, "other")):
            self.assertEqual(self.v29.program_path({"job": ["j"]})["moves"], [])

    def test_route_and_page(self):
        self.assertIs(self.v29.Handler.GET_API["/api/studio/program_path"], self.v29.program_path)
        with open(self.v29.STUDIO_HTML, encoding="utf-8") as f:
            page = f.read()
        for bit in ('id="lv"', "function lvDraw", "/api/studio/program_path?job="):
            self.assertIn(bit, page)


if __name__ == "__main__":
    unittest.main()
