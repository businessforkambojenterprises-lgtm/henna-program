"""Every design shipped in designs/ is readable line art (strokes only) and fits the studio limits."""
import glob
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.svg_design import place, read_svg  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGNS = sorted(glob.glob(os.path.join(ROOT, "designs", "*.svg")))
MAX_STROKES = 120


class Library(unittest.TestCase):
    def test_the_library_has_the_shipped_designs(self):
        names = {os.path.basename(p) for p in DESIGNS}
        self.assertTrue({"test_henna_motif.svg", "arabic_bel.svg", "paisley.svg", "lotus.svg", "rose.svg"} <= names)
        self.assertTrue({"jaipuri_chakra.svg", "jharokha_arch.svg", "arabic_grand_trail.svg", "arabic_mandala_bel.svg",
                         "arabic_rose_cluster.svg", "arabic_trail.svg", "arabic_s_bel.svg", "arabic_leaf_cascade.svg",
                         "arabic_finger_bel.svg"} <= names)          # Jaipuri + Arabic set, user 2026-10-22

    def test_each_design_is_line_art_and_places_at_30_mm(self):
        for p in DESIGNS:
            with self.subTest(design=os.path.basename(p)):
                d = read_svg(p)
                self.assertEqual(d["ignored"], [])
                self.assertFalse([s["source"] for s in d["strokes"] if s["filled_only"]])
                strokes, _ = place(d, 30.0, (0.0, 0.0))
                # each stroke costs a low hop (skin + 5 mm, henna_preview/hop_path.py), about 2-3 s; the complex
                # Jaipuri / Arabic designs (user 2026-10-22: "more complex") have up to 116 strokes
                self.assertLessEqual(len(strokes), MAX_STROKES)
                allp = [pt for s in strokes for pt in s["plan"]]
                xs, ys = [q[0] for q in allp], [q[1] for q in allp]
                self.assertAlmostEqual(max(max(xs) - min(xs), max(ys) - min(ys)), 30.0, delta=0.6)


if __name__ == "__main__":
    unittest.main()
