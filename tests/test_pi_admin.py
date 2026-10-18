"""Pi Admin (2026-10-16): 4-digit PIN as a salted hash, sessions that expire, 5 wrong PINs block, PIN reset at the Pi;
the Admin's paste setup applied to every program; the Pi server refuses admin routes without a valid session."""
import json
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "raspberry_pi"))

from henna_preview import admin_lock as al  # noqa: E402
from henna_preview import paste_setup  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class AdminLockTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.clock = Clock()
        self.lock = al.AdminLock(os.path.join(self.dir, "admin_pin.json"), clock=self.clock)

    def test_first_pin_then_unlock_and_expiry(self):
        self.assertFalse(self.lock.status()["pin_set"])
        with self.assertRaises(al.AdminError):
            self.lock.set_pin("12a4")
        token = self.lock.set_pin("2468")
        self.assertTrue(self.lock.valid(token))
        with open(os.path.join(self.dir, "admin_pin.json"), encoding="utf-8") as f:
            stored = f.read()
        self.assertNotIn("2468", stored)                       # only the salted hash is stored
        self.clock.t += al.SESSION_S + 1
        self.assertFalse(self.lock.valid(token))               # expired
        t2 = self.lock.unlock("2468")
        self.assertTrue(self.lock.status(t2)["unlocked"])
        self.lock.lock_now(t2)
        self.assertFalse(self.lock.valid(t2))

    def test_activity_keeps_the_session_open(self):
        token = self.lock.set_pin("2468")
        for _ in range(3):
            self.clock.t += al.SESSION_S - 10
            self.assertTrue(self.lock.valid(token))

    def test_wrong_pins_block_unlocking(self):
        self.lock.set_pin("2468")
        for i in range(al.MAX_TRIES - 1):
            with self.assertRaisesRegex(al.AdminError, "tries left"):
                self.lock.unlock("0000")
        with self.assertRaisesRegex(al.AdminError, "blocked"):
            self.lock.unlock("0000")
        with self.assertRaisesRegex(al.AdminError, "wait"):
            self.lock.unlock("2468")                           # even the right PIN while blocked
        self.clock.t += al.LOCKOUT_S + 1
        self.assertTrue(self.lock.valid(self.lock.unlock("2468")))

    def test_change_needs_the_current_pin_or_a_session(self):
        token = self.lock.set_pin("2468")
        with self.assertRaisesRegex(al.AdminError, "current PIN"):
            self.lock.set_pin("1357")
        self.lock.set_pin("1357", current="2468")
        self.assertFalse(self.lock.valid(token))               # changing the PIN ends old sessions
        t = self.lock.unlock("1357")
        self.lock.set_pin("9999", token=t)
        with self.assertRaises(al.AdminError):
            self.lock.unlock("1357")

    def test_reset_tool_at_the_pi(self):
        import admin_pin_reset as tool
        self.lock.set_pin("2468")
        self.assertEqual(tool.main(ask=lambda q: "no", lock=self.lock), 1)
        self.assertTrue(self.lock.pin_set())
        saved_append = None
        try:
            import henna_preview.web_log as wl
            saved_append, wl.append = wl.append, lambda *a, **k: None
            self.assertEqual(tool.main(ask=lambda q: "RESET", lock=self.lock), 0)
        finally:
            if saved_append:
                wl.append = saved_append
        self.assertFalse(self.lock.pin_set())


class PasteSetupTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.path = os.path.join(self.dir, "paste_setup.json")

    def test_save_load_apply(self):
        self.assertIsNone(paste_setup.load(self.path))
        self.assertEqual(paste_setup.apply({"job": "j"}, None), {"job": "j"})
        v = paste_setup.save("servo_ssr", 0.4, 0.25, self.path)
        self.assertEqual((v["dispenser"], v["start_s"], v["stop_s"]), ("servo_ssr", 0.4, 0.25))
        p = paste_setup.apply({"job": "j", "dispenser": "pneumatic", "start_delay_s": 2}, v)
        self.assertEqual((p["dispenser"], p["start_delay_s"], p["stop_delay_s"]), ("servo_ssr", 0.4, 0.25))
        none = paste_setup.save("none", path=self.path)
        self.assertEqual(paste_setup.apply({"dispenser": "pneumatic"}, none)["dispenser"], "none")
        with self.assertRaises(ValueError):
            paste_setup.save("pneumatic", 9, 0.1, self.path)


class FakeHeaders(dict):
    def get(self, k, d=None):
        return dict.get(self, k, d)


class PiServerAdminTests(unittest.TestCase):
    def test_admin_routes_need_a_session(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            import webui_server_pi as pi
            for path in ("/api/machines/select", "/api/calibration/approve", "/api/calibration/reference",
                         "/api/calibration/z_touch", "/api/pi/servo/save", "/api/pi/paste_setup"):
                self.assertTrue(pi.needs_admin(path), path)
            for path in ("/api/machine/run", "/api/machine/pause", "/api/studio/program", "/api/pi/admin/unlock",
                         "/api/machine/dispense_test", "/api/calibration/check", "/api/calibration/scan_position"):
                self.assertFalse(pi.needs_admin(path), path)
            sent = []
            h = object.__new__(pi.PiHandler)
            h.path, h.headers = "/api/machines/select", FakeHeaders()
            h._host_ok = lambda: True
            h._send = lambda code, obj, *a: sent.append((code, obj))
            pi.PiHandler.do_POST(h)
            self.assertEqual(sent[0][0], 403)
            self.assertIn("Admin is locked", sent[0][1]["error"])
            self.assertIs(pi.PiHandler.POST_API["/api/studio/program"][0], pi.pi_program)
        finally:
            if "webui_server_pi" in sys.modules:
                sys.modules["webui_server_pi"].studio.machine.paste_output = None
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


class AdminPageTests(unittest.TestCase):
    """One Admin page (2026-10-18): pi_touch.js rearranges Studio's Calibration view by element id, sets Studio's own
    safety and approval boxes from one tick each, and moves to the scan position with the calibration route (allowed
    in calibration mode, and for operators). A Studio page that renames one of these ids would break it silently."""

    STUDIO_IDS = ("v-calib", "mlist", "setupcard", "calstatus", "calibcard", "calmove", "calrefcard", "calcheck",
                  "calhist", "calclear", "calestop", "calempty", "ctargets", "capdots", "captouch", "capair", "capgo",
                  "calposhelp", "calposstate", "cxyscan", "czscan", "czhelp", "mhome", "mjog")

    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_studio_page_has_every_id_the_admin_page_uses(self):
        import webui_server_pi as pi_mod  # noqa: F401 - the page the Pi serves
        page, js = self.read("webui", "studio_v29.html"), self.read("webui", "pi_touch.js")
        for i in self.STUDIO_IDS:
            self.assertTrue(f'id="{i}"' in page, i)
            self.assertTrue(f"'{i}'" in js, i)

    def test_one_tick_per_tab_and_the_calibration_route(self):
        js = self.read("webui", "pi_touch.js")
        self.assertIn("machineCommand('/api/calibration/scan_position', { confirm_clear: true, confirm_estop: true }", js)
        self.assertIn("calibrate: ['Only the dot sheet or the block is on the bed", js)
        self.assertIn("['ctargets', 'calclear', 'calestop']", js)
        self.assertIn("['calclear', 'calestop', 'calempty']", js)
        self.assertIn("APPROVE = ['capdots', 'captouch', 'capair']", js)
        for tab in ("machine", "calibrate", "daily", "paste", "storage", "pin"):
            self.assertIn(f"['{tab}', ", js, tab)


class AdminJogTests(unittest.TestCase):
    """Sideways jog in Admin (2026-10-18): with a valid Admin session the jog skips v29's real-hand rule (the v28
    route with all its other checks); without one the rule refuses X/Y as before."""

    def test_admin_session_allows_sideways_jog(self):
        import webui_server_v10 as v10
        original = v10.camera
        try:
            import webui_server_pi as pi
            v29 = pi.studio
            sent = []
            saved = (v29._JOG, dict(v29._ACTIVE))
            v29._JOG = (lambda p: sent.append(p["axis"]) or {"busy": "jog"}, False)
            v29._ACTIVE.update(job="20261018-084336-job", subject="realhand")
            try:
                p = {"axis": "X", "direction": 1, "step_mm": 10, "confirm_no_person": True}
                pi._REQ.admin = False
                with self.assertRaisesRegex(ValueError, "Sideways jog is off"):
                    pi.pi_jog(p)
                pi._REQ.admin = True
                pi.pi_jog(p)
                self.assertEqual(sent, ["X"])
                self.assertIs(pi.PiHandler.POST_API["/api/machine/jog"][0], pi.pi_jog)
                h = object.__new__(pi.PiHandler)
                h.path, h.headers = "/api/machine/jog", FakeHeaders()
                h._host_ok = lambda: False
                pi.PiHandler.do_POST(h)                    # no token: the flag is cleared for this request
                self.assertFalse(pi._REQ.admin)
            finally:
                v29._JOG = saved[0]
                v29._ACTIVE.clear()
                v29._ACTIVE.update(saved[1])
                pi._REQ.admin = False
        finally:
            if "webui_server_pi" in sys.modules:
                sys.modules["webui_server_pi"].studio.machine.paste_output = None
            v10.camera = original
            sys.modules.pop("webui_server_pi", None)


if __name__ == "__main__":
    unittest.main()
