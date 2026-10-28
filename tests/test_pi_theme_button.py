"""The light / dark button is on the Pi's touch top bar again (user 2026-10-28: "where is theme changing icon?" - the
5" touch layout of 2026-10-14 had hidden it to save room)."""
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PiThemeButtonTests(unittest.TestCase):
    def test_not_hidden_on_the_touch_layout(self):
        with open(os.path.join(ROOT, "webui", "pi_touch.css"), encoding="utf-8") as f:
            css = f.read().replace(" ", "")
        self.assertNotIn("#btntheme{display:none}", css)
        with open(os.path.join(ROOT, "webui", "studio_v29.html"), encoding="utf-8") as f:
            self.assertIn('id="btntheme"', f.read())


if __name__ == "__main__":
    unittest.main()
