"""Exit button (Studio v29, user 2026-10-24: "also a exit button, so it closes everything running"): refused while
the machine moves / records or a run is watched; otherwise Ctrl+C's shutdown (camera off, CNC disconnected) and the
launcher window closes on EXIT_CODE."""
import _thread
import os
import sys
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class ExitTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.saved = (v29.machine.status, v29.v28.WATCH.view, v29._log, _thread.interrupt_main, dict(v29._EXIT))
        self.busy, self.watch, self.interrupted = None, False, []
        v29.machine.status = lambda: {"busy": self.busy, "mpos": [65.7, 80.0, -5.0]}
        v29.v28.WATCH.view = lambda _=None: {"active": self.watch}
        v29._log = lambda *a, **k: None
        _thread.interrupt_main = lambda *a: self.interrupted.append(True)

    def tearDown(self):
        v29 = self.v29
        (v29.machine.status, v29.v28.WATCH.view, v29._log, _thread.interrupt_main, ex) = self.saved
        v29._EXIT.clear()
        v29._EXIT.update(ex)

    def test_refused_while_busy_or_watched_or_unconfirmed(self):
        with self.assertRaisesRegex(ValueError, "confirm"):
            self.v29.studio_exit({})
        self.busy = "program"
        with self.assertRaisesRegex(ValueError, r"busy \(program\).*STOP"):
            self.v29.studio_exit({"confirm": True})
        self.busy, self.watch = None, True
        with self.assertRaisesRegex(ValueError, "watched"):
            self.v29.studio_exit({"confirm": True})
        time.sleep(0.8)
        self.assertEqual(self.interrupted, [])
        self.assertFalse(self.v29._EXIT["requested"])

    def test_closes_like_ctrl_c(self):
        self.assertEqual(self.v29.studio_exit({"confirm": True}), {"closing": True})
        self.assertTrue(self.v29._EXIT["requested"])
        time.sleep(0.8)                                       # after the page got its answer
        self.assertEqual(self.interrupted, [True])
        self.assertEqual(self.v29.Handler.POST_API["/api/studio/exit"], (self.v29.studio_exit, False))
        with open(os.path.join(ROOT, "start_studio_pc.bat"), encoding="utf-8") as f:
            self.assertIn(f'if "%errorlevel%"=="{self.v29.EXIT_CODE}" exit 0', f.read())
        with open(os.path.join(ROOT, "webui", "studio_v29.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn('id="btnexit"', page)
        self.assertIn("/api/studio/exit", page)


if __name__ == "__main__":
    unittest.main()
