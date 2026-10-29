"""Every Studio start begins at the home page; Exit names the right shortcut (user 2026-10-29: "after exit everything
should start from point 0", and the Pi's Exit message named the laptop's shortcut)."""
import os
import unittest

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "webui", "studio_v29.html")


class FreshStartTest(unittest.TestCase):
    def setUp(self):
        self.page = open(PAGE, encoding="utf-8").read()

    def test_last_job_reopens_only_in_the_same_window(self):
        self.assertIn("const last=liveWindow()?load('studio-job'):null;", self.page)
        self.assertIn("sessionStorage.setItem('studio-live','1')", self.page)

    def test_exit_forgets_the_job(self):
        self.assertIn("clearInterval(camTimer);store('studio-job');", self.page)

    def test_exit_text_uses_the_computers_shortcut(self):
        self.assertIn("window.STUDIO_SHORTCUT||'Henna Studio (PC - latest)'", self.page)
        self.assertEqual(self.page.count('"Henna Studio (PC - latest)"'), 0)


if __name__ == "__main__":
    unittest.main()
