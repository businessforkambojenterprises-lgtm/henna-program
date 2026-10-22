"""Names on the hand (v18): single-stroke font, line-art checks, upload path. No hardware."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import stroke_font as sf  # noqa: E402
from henna_preview.svg_design import place, read_svg  # noqa: E402
import webui_server_v18 as server  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_pristine  # noqa: E402

studio_pristine.remember()           # the older modules as loaded, before any v24+ test imports a later Studio


def setUpModule():
    global _PRISTINE
    _PRISTINE = studio_pristine.apply()


def tearDownModule():
    studio_pristine.undo(_PRISTINE)


def read_text_svg(text):
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as f:
        f.write(sf.name_svg(text))
    try:
        return read_svg(f.name)
    finally:
        os.remove(f.name)


class StrokeFont(unittest.TestCase):
    def test_every_letter_is_line_art(self):
        for letters in ("ABCDEFGHIJKLM", "NOPQRSTUVWXYZ"):
            with self.subTest(letters=letters):
                d = read_text_svg(letters)
                self.assertEqual(d["ignored"], [])
                self.assertFalse([s for s in d["strokes"] if s["filled_only"]])
                self.assertEqual(len(d["strokes"]), sum(len(sf.GLYPHS[c][1]) for c in letters))

    def test_names_place_at_size_with_few_strokes(self):
        for name in ("Harsh", "Sandeep", "Anne-Marie", "Jo Ann"):
            with self.subTest(name=name):
                strokes, _ = place(read_text_svg(name), 30.0, (0.0, 0.0))
                self.assertLessEqual(len(strokes), 40)
                xs = [p[0] for s in strokes for p in s["plan"]]
                self.assertAlmostEqual(max(xs) - min(xs), 30.0, delta=0.6)     # width is the long side

    def test_cleaning_and_refusals(self):
        self.assertEqual(sf.clean_name("  harsh   kumar "), "HARSH KUMAR")
        for bad in ("", "   ", None, 7, "Harsh1", "Ravi!", "Ã‰lan", "A" * 17):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    sf.clean_name(bad)

    def test_svg_is_safe_text(self):
        svg = sf.name_svg("Harsh")
        self.assertNotIn("<!DOCTYPE", svg)
        self.assertIn('fill="none"', svg)
        self.assertIn("<title>Name: HARSH</title>", svg)


class NameDesignEndpoint(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch.object(server.v11, "UPLOAD_DIR", tmp.name))
        self.dir = tmp.name

    def test_name_becomes_a_usable_design_and_is_reused(self):
        d = server.name_design({"text": "harsh"})["design"]
        self.assertTrue(d["usable"])
        self.assertTrue(d["uploaded"] or d["svg"].endswith("Name_HARSH.svg"))
        self.assertEqual(os.listdir(self.dir), ["Name_HARSH.svg"])
        again = server.name_design({"text": "HARSH"})["design"]
        self.assertEqual(again["svg"], d["svg"])
        self.assertEqual(os.listdir(self.dir), ["Name_HARSH.svg"])                 # no copy made
        server.name_design({"text": "Sandeep"})
        self.assertEqual(sorted(os.listdir(self.dir)), ["Name_HARSH.svg", "Name_SANDEEP.svg"])

    def test_bad_name_writes_nothing(self):
        with self.assertRaises(ValueError):
            server.name_design({"text": "<script>"})
        self.assertEqual(os.listdir(self.dir), [])

    def test_route(self):
        self.assertIs(server.Handler.POST_API["/api/studio/name_design"][0], server.name_design)
        self.assertEqual(server.Handler.server_version, "HennaStudio/0.18")


if __name__ == "__main__":
    unittest.main()
