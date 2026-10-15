"""Studio v29 paste dispensing: the paste lines, their independent re-read, the controller check, and the link
(paste OFF in every pause, ON again before Resume, $$ read, dispenser test) against a fake GRBL controller."""
import os
import sys
import threading
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

from henna_preview import dispense, dispense_check  # noqa: E402
from henna_preview import grbl_program_link as gpl  # noqa: E402
from henna_preview.grbl_program_link import MachineError  # noqa: E402
from henna_preview.grbl_program_link_v29 import SPINDLE_STOP, GrblProgramLinkV29  # noqa: E402
from test_grbl_program_link import CONFIG, PROGRAM, FakeGrbl, lines_and_end, wait_until  # noqa: E402

TRAVEL = -20.0
CFG = dispense.settings("pneumatic", 0.05, 0.02)


def hover():
    return lines_and_end()[0]


def ok(paste, lines=None):
    return dispense_check.check(paste, hover() if lines is None else lines, CFG, TRAVEL, dispense.S_FULL)


class PasteLinesTests(unittest.TestCase):
    def test_on_after_each_drop_off_before_each_lift(self):
        h = hover()
        p = dispense.add(h, CFG)
        self.assertEqual([x for x in p if x not in (dispense.PASTE_ON, dispense.PASTE_OFF) and not x.startswith("G4")], h)
        i = p.index("G53 G1 X71.089 Y57.046 Z-56.052 F300")
        self.assertEqual(p[i - 1], "G53 G0 X71.089 Y57.046")
        self.assertEqual(p[i + 1:i + 3], ["M3 S1000", "G4 P0.050"])
        j = p.index("G53 G1 X73.006 Y56.436 Z-56.048 F300")
        self.assertEqual(p[j + 1:j + 4], ["M5", "G4 P0.020", "G53 G0 Z-20.000"])
        self.assertEqual(p.count("M3 S1000"), 2)
        self.assertEqual(p[-1], "G53 G0 Z-20.000")
        r = ok(p)
        self.assertEqual((r["strokes"], r["paste_on"], r["paste_off"], r["problems"]), (2, 2, 2, 0))
        self.assertEqual(dispense.added_time_s(CFG, 2), 0.1)

    def test_settings(self):
        self.assertIsNone(dispense.settings("none"))
        self.assertIsNone(dispense.settings(None))
        self.assertEqual(dispense.settings("servo_ssr")["start_s"], 0.5)
        self.assertEqual(dispense.settings("pneumatic", "0.4", "")["start_s"], 0.4)
        for bad in (("valve", None, None), ("pneumatic", -0.1, None), ("pneumatic", None, 3.5),
                    ("pneumatic", "x", None)):
            with self.assertRaises(dispense.DispenseError):
                dispense.settings(*bad)

    def test_controller_check(self):
        self.assertIsNone(dispense.controller_ok({32: "0", 30: "1000.000"}))
        self.assertIn("laser mode", dispense.controller_ok({32: "1", 30: "1000"}))
        self.assertIn("$30=255", dispense.controller_ok({32: "0", 30: "255"}))
        self.assertIn("did not report", dispense.controller_ok({30: "1000"}))


class IndependentCheckTests(unittest.TestCase):
    def bad(self, paste, msg):
        with self.assertRaisesRegex(ValueError, msg):
            ok(paste)

    def test_refuses_every_wrong_paste_program(self):
        p = dispense.add(hover(), CFG)
        on = p.index("M3 S1000")
        off = p.index("M5")
        lift = off + 2
        # paste ON before the drop (still at travel height)
        q = list(p); q.insert(on - 1, q.pop(on)); q.insert(on, q.pop(on + 1))
        self.bad(q, "drop|delay")
        # a G0 with paste ON (the lift before OFF)
        q = list(p); q[off], q[lift] = q[lift], q[off]
        self.bad(q, "paste ON|lift|delay")
        # OFF missing
        q = list(p); del q[off:off + 2]
        self.bad(q, "ON while already ON|ends with paste ON|with paste ON")
        # wrong output level
        q = list(p); q[on] = "M3 S500"
        self.bad(q, "S500")
        # wrong delays
        q = list(p); q[on + 1] = "G4 P1.000"
        self.bad(q, "start delay")
        q = list(p); q[off + 1] = "G4 P0.500"
        self.bad(q, "stop delay")
        # an extra delay anywhere else
        q = list(p); q.insert(2, "G4 P0.050")
        self.bad(q, "delay that does not belong")
        # a changed move: not the checked hover program any more
        q = list(p); q[on + 2] = q[on + 2].replace("Z-56.043", "Z-60.043")
        self.bad(q, "not the checked hover program")
        # a hover program that already had paste commands
        with self.assertRaisesRegex(ValueError, "already has paste"):
            dispense_check.check(p, p, CFG, TRAVEL, dispense.S_FULL)

    def test_refuses_paste_at_travel_height(self):
        h = [x.replace("Z-56.052", "Z-19.500") for x in hover()]       # a "drop" that stays at travel height
        p = dispense.add(h, CFG)
        with self.assertRaisesRegex(ValueError, "not below travel Z"):
            dispense_check.check(p, h, CFG, TRAVEL, dispense.S_FULL)


class PasteGrbl(FakeGrbl):
    """FakeGrbl + $$ replies, the spindle output (M3/M5) and the spindle-stop override (0x9E, only in Hold)."""

    def __init__(self, laser="0", **kw):
        super().__init__(**kw)
        self.laser, self.spindle, self.override_stop, self.output_log = laser, False, False, []

    def output(self):
        return self.spindle and not self.override_stop

    def write(self, data):
        with self.lock:
            if data == b"$$\n":
                self.sent.append(data)
                for line in ("$0=10", "$30=1000.000", f"$32={self.laser}", "$110=1000.000", "ok"):
                    self.emit(line)
                return len(data)
            if data == SPINDLE_STOP:
                self.sent.append(data)
                if self.state == "Hold":
                    self.override_stop = not self.override_stop
                self.output_log.append(("0x9E", self.output()))
                return len(data)
            if data == b"~":
                self.override_stop = False          # grbl restores the spindle on cycle start
            if data == b"\x18":
                self.spindle = self.override_stop = False
            if data in (b"M3 S1000\n", b"M5\n"):
                self.spindle = data == b"M3 S1000\n"
                self.output_log.append((data.decode().strip(), self.output()))
        return super().write(data)


class LinkTests(unittest.TestCase):
    def make(self, **kw):
        fake = PasteGrbl(**kw)
        link = GrblProgramLinkV29(lambda: dict(CONFIG), opener=lambda p: fake, poll_s=0.01, fresh_s=0.5,
                                  homing_timeout=1.0, move_timeout=1.0, status_timeout=0.5, ack_timeout=2.0,
                                  end_timeout=4.0, idle_away_s=0.3)

        def cleanup():
            try:
                if link.status()["connected"] and link.status()["busy"]:
                    link.stop()
                link.disconnect()
            except MachineError:
                pass
            fake.close()
        self.addCleanup(cleanup)
        link.connect("FAKE", wait_s=1.0)
        done = threading.Event()
        link.start_homing(on_done=lambda e: done.set())
        self.assertTrue(done.wait(3))
        return link, fake

    def test_reads_settings_and_releases_the_machine(self):
        link, fake = self.make()
        found = link.read_settings()
        self.assertEqual((found[32], found[30]), ("0", "1000.000"))
        self.assertIsNone(link.status()["busy"])
        self.assertIsNone(dispense.controller_ok(found))
        self.assertFalse(any(m.startswith("$") for m in link.status()["messages"]))

    def start(self, link, armed):
        send, end = lines_and_end()
        lines = dispense.add(send, CFG) if armed else send
        if armed:
            link.arm_dispense(CFG)
        done, result = threading.Event(), {}
        link.start_program("T.gcode", lines, end, on_done=lambda e, *a: (result.update(error=e), done.set()))
        return done, result

    def test_pause_stops_the_paste_and_resume_restores_it_first(self):
        link, fake = self.make(capacity=2, move_s=0.08)
        done, result = self.start(link, armed=True)
        self.assertTrue(wait_until(lambda: fake.output(), 3), "paste never came on")
        link.pause()
        self.assertTrue(wait_until(lambda: (link.status()["program"] or {}).get("paste", {}).get("stopped_in_pause"), 2))
        self.assertFalse(fake.output())                                  # paste OFF while held
        t0 = time.monotonic()
        link.resume()
        self.assertGreaterEqual(time.monotonic() - t0, CFG["start_s"] - 0.01)   # start delay before moving on
        sent = fake.sent
        i_restore = max(i for i, x in enumerate(sent) if x == SPINDLE_STOP)
        self.assertLess(i_restore, max(i for i, x in enumerate(sent) if x == b"~"))  # restore before cycle start
        self.assertTrue(done.wait(6), "run did not finish")
        self.assertIsNone(result["error"])
        self.assertFalse(fake.spindle)                                   # the program ends with paste OFF
        self.assertEqual(sum(1 for x in sent if x == SPINDLE_STOP), 2)

    def test_hover_run_pause_sends_no_paste_commands(self):
        link, fake = self.make(capacity=2, move_s=0.05)
        done, _ = self.start(link, armed=False)
        self.assertTrue(wait_until(lambda: fake.state == "Run", 2))
        link.pause()
        time.sleep(0.3)
        link.resume()
        self.assertTrue(done.wait(6))
        self.assertNotIn(SPINDLE_STOP, fake.sent)
        self.assertFalse(any(x in (b"M3 S1000\n", b"M5\n") for x in fake.sent))

    def test_arming_is_used_once(self):
        link, fake = self.make()
        done, _ = self.start(link, armed=True)
        self.assertTrue(done.wait(6))
        self.assertIsNone(getattr(link, "_dispense_next", None))

    def test_dispenser_test_runs_still_and_ends_off(self):
        link, fake = self.make()
        before = list(link.status()["mpos"])
        done = threading.Event()
        errors = []
        link.start_dispense_test(0.2, on_done=lambda e: (errors.append(e), done.set()))
        self.assertTrue(done.wait(3))
        self.assertEqual(errors, [None])
        self.assertEqual(fake.lines()[-3:], ["M3 S1000", "G4 P0.200", "M5"])
        self.assertFalse(fake.spindle)
        self.assertEqual(link.status()["mpos"], before)
        with self.assertRaises(MachineError):
            link.start_dispense_test(5)


class FakePin:
    name, ready = "GPIO27", True

    def __init__(self):
        self.value, self.log = False, []

    def on(self):
        self.value = True
        self.log.append("on")

    def off(self):
        self.value = False
        self.log.append("off")


class PinLinkTests(LinkTests):
    """The SSR on a Pi pin: the same program, the link switches the pin after G4 P0 instead of sending M3/M5."""

    def make(self, **kw):
        link, fake = super().make(**kw)
        link.paste_output = self.pin = FakePin()
        return link, fake

    def test_pause_stops_the_paste_and_resume_restores_it_first(self):
        link, fake = self.make(capacity=2, move_s=0.08)
        done, result = self.start(link, armed=True)
        self.assertTrue(wait_until(lambda: self.pin.value, 3), "paste never came on")
        link.pause()
        self.assertFalse(self.pin.value)                                 # OFF at once
        self.assertTrue(link.status()["program"]["paste"]["stopped_in_pause"])
        self.assertTrue(wait_until(lambda: link.status()["state"] == "Hold", 2))
        t0 = time.monotonic()
        link.resume()
        self.assertGreaterEqual(time.monotonic() - t0, CFG["start_s"] - 0.01)
        self.assertTrue(done.wait(6), "run did not finish")
        self.assertIsNone(result["error"])
        self.assertFalse(self.pin.value)
        sent = fake.lines()
        self.assertNotIn("M3 S1000", sent)
        self.assertNotIn("M5", sent)
        self.assertEqual(sent.count("G4 P0"), 4)                        # 2 strokes x (before ON, before OFF)
        self.assertNotIn(SPINDLE_STOP, fake.sent)

    def test_pin_follows_the_strokes(self):
        link, fake = self.make()
        done, result = self.start(link, armed=True)
        self.assertTrue(done.wait(6))
        self.assertIsNone(result["error"])
        ons = [i for i, x in enumerate(self.pin.log) if x == "on"]
        self.assertEqual(len(ons), 2)
        self.assertEqual(self.pin.log[-1], "off")

    def test_stop_switches_the_pin_off(self):
        link, fake = self.make(capacity=2, move_s=0.08)
        self.start(link, armed=True)
        self.assertTrue(wait_until(lambda: self.pin.value, 3))
        link.stop()
        self.assertFalse(self.pin.value)

    def test_dispenser_test_runs_still_and_ends_off(self):
        link, fake = self.make()
        n = len(fake.lines())
        done, errors = threading.Event(), []
        link.start_dispense_test(0.2, on_done=lambda e: (errors.append(e), done.set()))
        self.assertTrue(wait_until(lambda: self.pin.value, 1))
        self.assertTrue(done.wait(3))
        self.assertEqual(errors, [None])
        self.assertFalse(self.pin.value)
        self.assertEqual(fake.lines()[n:], [])                          # nothing sent to the controller

    def test_reads_settings_and_releases_the_machine(self):
        pass                                                            # not needed with a pin


class ServerTests(unittest.TestCase):
    def test_pin_output_needs_no_controller_settings(self):
        import webui_server_v29 as v29

        class M:
            paste_output = FakePin()

            def read_settings(self):
                raise AssertionError("no $$ with a Pi pin")
        saved = v29.machine
        v29.machine = M()
        try:
            self.assertEqual(v29.controller_ready(), {"output": "GPIO27"})
            M.paste_output.ready = False
            with self.assertRaisesRegex(ValueError, "Pi paste pin is not working"):
                v29.controller_ready()
        finally:
            v29.machine = saved

    def test_v29_installs_its_checks(self):
        import webui_server_v17 as v17
        import webui_server_v29 as v29
        self.assertIs(v17.checked_program, v29.checked_program)
        self.assertIsInstance(v29.machine, GrblProgramLinkV29)
        self.assertTrue(os.path.isfile(v29.STUDIO_HTML))
        for route in ("/api/studio/program", "/api/machine/run", "/api/machine/dispense_test"):
            self.assertIn(route, v29.Handler.POST_API)
        self.assertIn("/api/studio/dispensers", v29.Handler.GET_API)
        self.assertTrue(v29.PASTE_NAME.match("DESIGN_PASTE_20261015-101010.gcode"))
        self.assertFalse(v29.PASTE_NAME.match("../x.gcode"))

    def test_paste_run_refused_in_laser_mode(self):
        import webui_server_v29 as v29

        class M:
            paste_output = None

            def read_settings(self):
                return {32: "1", 30: "1000"}
        saved = v29.machine
        v29.machine = M()
        try:
            with self.assertRaisesRegex(ValueError, "laser mode"):
                v29.controller_ready()
        finally:
            v29.machine = saved


if __name__ == "__main__":
    unittest.main()
