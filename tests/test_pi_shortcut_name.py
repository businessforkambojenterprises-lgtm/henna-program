"""The Pi's Exit message names the Pi's own desktop shortcut, "Henna Studio" (user 2026-10-29: it named the laptop's)."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PiShortcutNameTest(unittest.TestCase):
    def test_pi_page_sets_the_shortcut_the_installer_makes(self):
        js = open(os.path.join(ROOT, "webui", "pi_touch.js"), encoding="utf-8").read()
        self.assertIn("window.STUDIO_SHORTCUT = 'Henna Studio';", js)
        for script in ("install_from_stick.sh", "stage2_setup_studio.sh"):
            text = open(os.path.join(ROOT, "raspberry_pi", script), encoding="utf-8").read()
            self.assertRegex(text, re.compile(r"^Name=Henna Studio$", re.M), script)


if __name__ == "__main__":
    unittest.main()
