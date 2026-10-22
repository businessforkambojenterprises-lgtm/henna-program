"""Font choice for names (v19): installed fonts -> single centre lines; only listed fonts. No hardware."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import font_strokes as fs  # noqa: E402
from henna_preview.svg_design import place, read_svg  # noqa: E402
import webui_server_v19 as server  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_pristine  # noqa: E402

studio_pristine.remember()           # the older modules as loaded, before any v24+ test imports a later Studio


def setUpModule():
    global _PRISTINE
    _PRISTINE = studio_pristine.apply()


def tearDownModule():
    studio_pristine.undo(_PRISTINE)

HAVE = {f["id"] for f in fs.list_fonts()}


def read(svg):
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as f:
        f.write(svg)
    try:
        return read_svg(f.name)
    finally:
        os.remove(f.name)


@unittest.skipUnless("segoe-script" in HAVE, "Segoe Script is not installed on this PC")
class FontLines(unittest.TestCase):
    def test_handwriting_fonts_listed_first(self):
        fonts = fs.list_fonts()
        rec = [f["name"] for f in fonts if f["recommended"]]
        self.assertIn("Segoe Script", rec)
        self.assertTrue(all(f["recommended"] for f in fonts[:len(rec)]))

    def test_name_becomes_single_line_art(self):
        for fid in ("segoe-script", "segoe-print", "ink-free"):
            if fid not in HAVE:
                continue
            with self.subTest(font=fid):
                d = read(fs.font_name_svg("Harsh", fid))
                self.assertEqual(d["ignored"], [])
                self.assertFalse([s for s in d["strokes"] if s["filled_only"]])
                strokes, _ = place(d, 30.0, (0.0, 0.0))
                self.assertTrue(5 <= len(strokes) <= fs.MAX_STROKES)

    def test_refusals(self):
        for text, fid in (("Harsh", "no-such-font"), ("Harsh", r"C:\Windows\Fonts\arial.ttf"), ("", "segoe-script"),
                          ("Ravi!", "segoe-script"), ("<b>", "segoe-script"), ("A" * 17, "segoe-script")):
            with self.subTest(text=text, font=fid):
                with self.assertRaises(ValueError):
                    fs.font_name_svg(text, fid)

    def test_too_many_strokes_refused(self):
        with patch.object(fs, "MAX_STROKES", 3):
            with self.assertRaisesRegex(fs.FontStrokeError, "more than 3"):
                fs.font_name_svg("Harsh", "segoe-script")


@unittest.skipUnless("segoe-script" in HAVE, "Segoe Script is not installed on this PC")
class NameEndpoint(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch.object(server.v11, "UPLOAD_DIR", tmp.name))
        self.dir = tmp.name

    def test_font_name_saved_once_and_reused(self):
        d = server.name_design({"text": "Harsh", "font": "segoe-script"})["design"]
        self.assertTrue(d["usable"])
        self.assertEqual(os.listdir(self.dir), ["Name_Harsh_segoe-script.svg"])
        self.assertEqual(server.name_design({"text": "Harsh", "font": "segoe-script"})["design"]["svg"], d["svg"])
        self.assertEqual(len(os.listdir(self.dir)), 1)

    def test_studio_font_still_works(self):
        server.name_design({"text": "harsh", "font": "studio"})
        server.name_design({"text": "Sandeep"})                             # no font = studio capitals
        self.assertEqual(sorted(os.listdir(self.dir)), ["Name_HARSH.svg", "Name_SANDEEP.svg"])

    def test_font_list_and_route(self):
        fonts = server.fonts()["fonts"]
        self.assertEqual(fonts[0]["id"], "studio")
        self.assertNotIn("path", fonts[1])                                  # file paths are not sent to the page
        self.assertIs(server.Handler.POST_API["/api/studio/name_design"][0], server.name_design)
        self.assertIn("/api/studio/fonts", server.Handler.GET_API)


if __name__ == "__main__":
    unittest.main()
