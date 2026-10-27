"""Admin on the PC (2026-10-24, user: "it should have same home page and admin page like pis studio"): the Pi's
4-digit PIN lock, paste setup and Admin jog, ported to webui_server_pc.py + webui/pc_admin.js (the Pi branch is not
merged). PIN as a salted hash, sessions that expire, 5 wrong PINs block, reset with admin_pin_reset.py on the PC; the
Admin's paste setup applied to every program; admin routes refused without a valid session; the page has every id the
Admin page uses."""
import os
import shutil
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

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

    def test_reset_tool_on_the_pc(self):
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


def load_pc():
    """webui_server_pc swaps v29's Handler on import - put it back afterwards for the other tests."""
    import webui_server_v29 as v29
    saved = v29.Handler
    import webui_server_pc as pc
    return pc, lambda: (setattr(v29, "Handler", saved), sys.modules.pop("webui_server_pc", None))


class PcServerTests(unittest.TestCase):
    def setUp(self):
        self.pc, undo = load_pc()
        self.addCleanup(undo)

    def test_admin_routes_need_a_session(self):
        pc = self.pc
        for path in ("/api/machines/select", "/api/calibration/approve", "/api/calibration/reference",
                     "/api/calibration/z_touch", "/api/admin/paste_setup", "/api/machines/setup_area"):
            self.assertTrue(pc.needs_admin(path), path)
        for path in ("/api/machine/run", "/api/machine/pause", "/api/studio/program", "/api/admin/unlock",
                     "/api/machine/dispense_test", "/api/calibration/check", "/api/calibration/scan_position",
                     "/api/studio/exit"):
            self.assertFalse(pc.needs_admin(path), path)
        sent = []
        h = object.__new__(pc.PcHandler)
        h.path, h.headers = "/api/machines/select", FakeHeaders()
        h._host_ok = lambda: True
        h._send = lambda code, obj, *a: sent.append((code, obj))
        pc.PcHandler.do_POST(h)
        self.assertEqual(sent[0][0], 403)
        self.assertIn("Admin is locked", sent[0][1]["error"])
        self.assertIs(pc.PcHandler.POST_API["/api/studio/program"][0], pc.pc_program)
        self.assertIs(pc.PcHandler.POST_API["/api/machine/jog"][0], pc.pc_jog)
        self.assertIs(pc.studio.Handler, pc.PcHandler)          # what v29.main serves

    def test_program_uses_the_admin_paste_setup(self):
        pc = self.pc
        got = []
        saved = (pc._PROGRAM, pc.paste_setup.load)
        pc._PROGRAM = (lambda p: got.append(p) or {}, True)
        try:
            pc.paste_setup.load = lambda: None                 # nothing chosen in Admin: the Program step's choice
            pc.pc_program({"job": "j", "dispenser": "pneumatic"})
            pc.paste_setup.load = lambda: {"dispenser": "servo_ssr", "start_s": 0.5, "stop_s": 0.3}
            pc.pc_program({"job": "j", "dispenser": "pneumatic"})
        finally:
            pc._PROGRAM, pc.paste_setup.load = saved
        self.assertEqual(got[0]["dispenser"], "pneumatic")
        self.assertEqual((got[1]["dispenser"], got[1]["start_delay_s"]), ("servo_ssr", 0.5))

    def test_admin_session_allows_free_jog(self):
        pc = self.pc
        v29 = pc.studio
        sent = []
        saved = (pc.admin_jog.start_admin_xy, pc.admin_jog.start_admin_z, pc.v26.profiles.active,
                 pc.v26.profiles.motion, v29.v10.camera.status, pc._JOG)
        pc._JOG = (lambda p: sent.append("v29 " + p["axis"]) or {"busy": "jog"}, False)
        pc.admin_jog.start_admin_xy = lambda link, a, *rest: sent.append("admin " + a) or {"busy": "jog"}
        pc.admin_jog.start_admin_z = lambda link, d, s, floor, ok: sent.append(f"admin Z {floor:g}") or {"busy": "jog"}
        pc.v26.profiles.active = lambda: {"name": "Ball screw", "work_area_mm": [225, 176]}
        pc.v26.profiles.motion = lambda m: {"travel_z": -1.0, "firmware_z_floor": -40.0}
        v29.v10.camera.status = lambda: {"state": "off"}
        try:
            p = {"axis": "X", "direction": 1, "step_mm": 10, "confirm_no_person": True}
            pc._REQ.admin = False
            pc.pc_jog(p)
            pc._REQ.admin = True
            pc.pc_jog(p)
            pc.pc_jog(dict(p, axis="Z", direction=-1))
            with self.assertRaisesRegex(ValueError, "no person"):
                pc.pc_jog(dict(p, confirm_no_person=False))
            self.assertEqual(sent, ["v29 X", "admin X", "admin Z -40"])
            h = object.__new__(pc.PcHandler)
            h.path, h.headers = "/api/machine/jog", FakeHeaders()
            h._host_ok = lambda: False
            pc.PcHandler.do_POST(h)                            # no token: the flag is cleared for this request
            self.assertFalse(pc._REQ.admin)
        finally:
            (pc.admin_jog.start_admin_xy, pc.admin_jog.start_admin_z, pc.v26.profiles.active, pc.v26.profiles.motion,
             v29.v10.camera.status, pc._JOG) = saved
            pc._REQ.admin = False

    def test_page_gets_the_home_and_admin_script(self):
        html = self.pc.pc_page()
        self.assertIn('<style id="pc-admin">', html)
        self.assertIn('<script id="pc-admin-js">', html)
        self.assertLess(html.index("pc-admin-js"), html.rindex("</body>"))
        # once per Studio start the page opens on the home page, not the last job ("restarted ... this check comes")
        self.assertIn(f'window.STUDIO_BOOT = "{self.pc.BOOT}";', html)
        self.assertIn("localStorage.removeItem('studio-job')", html)
        self.assertLess(html.index("window.STUDIO_BOOT"), html.index("localStorage.removeItem('studio-job')"))


class AdminPageTests(unittest.TestCase):
    """pc_admin.js rearranges Studio's Calibration view by element id (as pi_touch.js on the Pi); a Studio page that
    renames one of these ids would break it silently."""

    STUDIO_IDS = ("v-calib", "mlist", "setupcard", "calstatus", "calibcard", "calmove", "calrefcard", "calcheck",
                  "calhist", "calclear", "calestop", "calempty", "ctargets", "capdots", "captouch", "capgo",
                  "calposhelp", "calposstate", "cxyscan", "czscan", "czhelp", "mhome", "mjog")

    def read(self, *parts):
        with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
            return f.read()

    def test_studio_page_has_every_id_the_admin_page_uses(self):
        page, js = self.read("webui", "studio_v29.html"), self.read("webui", "pc_admin.js")
        for i in self.STUDIO_IDS:
            self.assertTrue(f'id="{i}"' in page, i)
            self.assertTrue(f"'{i}'" in js, i)

    def test_tabs_ticks_routes(self):
        js = self.read("webui", "pc_admin.js")
        self.assertIn("machineCommand('/api/calibration/scan_position', { confirm_clear: true, confirm_estop: true }", js)
        self.assertIn("['ctargets', 'calclear', 'calestop']", js)
        self.assertIn("['calclear', 'calestop', 'calempty']", js)
        self.assertIn("APPROVE = ['capdots', 'captouch']", js)
        for tab in ("machine", "calibrate", "daily", "paste", "pin"):
            self.assertIn(f"['{tab}', ", js, tab)
        for gone in ("/api/pi/", "renderStorage", "renderServo", "pipause"):   # Pi-only parts left out
            self.assertNotIn(gone, js, gone)
        for route in ("/api/admin/status", "/api/admin/unlock", "/api/admin/set_pin", "/api/admin/lock",
                      "/api/admin/paste_setup", "/api/admin/jog_limits"):
            self.assertIn(route, js, route)
        self.assertIn("python.exe webui_server_pc.py", self.read("start_studio_pc.bat"))
        # the page button is not called "Home" - that is the machine's homing button (user 2026-10-24: "same name may
        # cause problem")
        self.assertIn("b.textContent = 'Main menu'", js)
        self.assertNotIn("b.textContent = 'Home'", js)


if __name__ == "__main__":
    unittest.main()
