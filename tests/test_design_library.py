"""Every design shipped in designs/ is readable line art (strokes only) and fits the studio limits."""
import glob
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.svg_design import place, read_svg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGNS = sorted(glob.glob(os.path.join(ROOT, "designs", "*.svg")))


class Library(unittest.TestCase):
    def test_the_library_has_the_shipped_designs(self):
        names = {os.path.basename(p) for p in DESIGNS}
        self.assertTrue({"test_henna_motif.svg", "arabic_bel.svg", "paisley.svg", "lotus.svg", "rose.svg"} <= names)

    def test_each_design_is_line_art_and_places_at_30_mm(self):
        for p in DESIGNS:
            with self.subTest(design=os.path.basename(p)):
                d = read_svg(p)
                self.assertEqual(d["ignored"], [])
                self.assertFalse([s["source"] for s in d["strokes"] if s["filled_only"]])
                strokes, _ = place(d, 30.0, (0.0, 0.0))
                self.assertLessEqual(len(strokes), 40)            # each stroke costs a descent from travel Z
                allp = [pt for s in strokes for pt in s["plan"]]
                xs, ys = [q[0] for q in allp], [q[1] for q in allp]
                self.assertAlmostEqual(max(max(xs) - min(xs), max(ys) - min(ys)), 30.0, delta=0.6)


if __name__ == "__main__":
    unittest.main()
