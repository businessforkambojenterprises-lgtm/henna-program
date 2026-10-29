"""The Pi writes its own validation log (user 2026-10-29: "separate Pi log"): validation_log_pi.csv, git-ignored, with
the shared log's columns - so "git pull" on the Pi never stops at the shared log again."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class PiOwnLogTests(unittest.TestCase):
    def test_own_file_with_the_columns_and_studio_writes_there(self):
        import webui_server_pi as pi
        from henna_preview.web_log import COLUMNS, append
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        saved = pi.studio.v10.LOG_FILE
        self.addCleanup(setattr, pi.studio.v10, "LOG_FILE", saved)
        path = pi.use_pi_log(d)
        self.assertEqual(os.path.basename(path), "validation_log_pi.csv")
        self.assertEqual(pi.studio.v10.LOG_FILE, path)
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.readline().strip().split(","), COLUMNS)
        append(path, 0, "test row", None, "", "", "", "ok")
        pi.use_pi_log(d)                                            # a second start keeps the rows
        with open(path, encoding="utf-8") as f:
            self.assertEqual(len(f.read().strip().splitlines()), 2)

    def test_git_ignores_it_and_the_pi_start_uses_it(self):
        with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
            self.assertIn("validation_log_pi.csv", f.read().splitlines())
        with open(os.path.join(ROOT, "webui_server_pi.py"), encoding="utf-8") as f:
            self.assertIn("use_pi_log()", f.read())


if __name__ == "__main__":
    unittest.main()
