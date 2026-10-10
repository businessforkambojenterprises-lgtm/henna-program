"""Studio v14 program link against a fake GRBL controller (planner queue, ok per line, hold, resume, reset).

No real serial port or machine is used.
"""
import os
import queue
import re
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import grbl_program_link as mod  # noqa: E402

CONFIG = {"capture_pose_mpos": [145.8, 42.6, -40.0], "travel_z": -20.0,
          "draw_feed_mm_min": 300.0, "travel_feed_mm_min": 1000.0}
PROGRAM = "\n".join([
    "(DESIGN HOVER TEST - PROSTHETIC HAND ONLY - NO PERSON - NO DISPENSING)",
    "(nozzle 4 mm above measured skin)",
    "G21", "G90",
    "G53 G0 Z-20.000",
    "G53 G0 X71.089 Y57.046",
    "G53 G1 X71.089 Y57.046 Z-56.052 F300",
    "G53 G1 X72.044 Y56.731 Z-56.043 F300",
    "G53 G1 X73.006 Y56.436 Z-56.048 F300",
    "G53 G0 Z-20.000",
    "G53 G0 X96.124 Y75.617",
    "G53 G1 X96.124 Y75.617 Z-63.547 F300",
    "G53 G1 X95.415 Y75.387 Z-63.664 F300",
    "G53 G0 Z-20.000", ""])
FLOOR = -64.7
END = (95.415, 75.387, -20.0)


def lines_and_end(text=PROGRAM):
    return mod.program_lines(text, -20.0, FLOOR, 300.0, -97.0)


def wait_until(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class FakeGrbl:
    """Planner of `capacity` blocks; each move takes `move_s`; ok as soon as a block fits."""

    def __init__(self, capacity=4, move_s=0.01):
        self.lock = threading.RLock()
        self.rx = queue.Queue()
        self.sent = []
        self.state, self.mpos = "Idle", [0.0, 0.0, -3.0]
        self.planner, self.waiting = [], []
        self.capacity, self.move_s = capacity, move_s
        self.held = False
        self.closed = False
        self.fail_on = None          # a line answered with error:20
        self.stop_acking_after = None
        self.skip_moves = 0          # silently drop this many moves (wrong end position)
        self.alarm_at_line = None
        self.banner_at_line = None
        self.lines_seen = 0
        threading.Thread(target=self._motion, daemon=True).start()

    def emit(self, text):
        self.rx.put((text + "\r\n").encode("ascii"))

    def status_text(self):
        return f"<{self.state}|MPos:{self.mpos[0]:.3f},{self.mpos[1]:.3f},{self.mpos[2]:.3f}|FS:0,0>"

    def write(self, data):
        with self.lock:
            if self.closed:
                raise OSError("closed")
            self.sent.append(data)
            if data == b"?":
                self.emit(self.status_text())
            elif data == b"!":
                self.held, self.state = True, "Hold"
            elif data == b"~":
                if self.held:
                    self.held = False
                    self.state = "Run" if self.planner else "Idle"
            elif data == b"\x18":
                self.planner, self.waiting, self.held, self.state = [], [], False, "Idle"
                self.emit("Grbl 1.1h ['$' for help]")
            elif data.endswith(b"\n"):
                self._line(data.decode().strip())
        return len(data)

    def _line(self, cmd):
        self.lines_seen += 1
        if self.alarm_at_line == self.lines_seen:
            self.state = "Alarm"
            self.emit("ALARM:1")
            return
        if self.banner_at_line == self.lines_seen:
            self.emit("Grbl 1.1h ['$' for help]")
            return
        if cmd == self.fail_on:
            self.emit("error:20")
            return
        if cmd in ("$HZ", "$H"):
            self.state = "Home"
            self.mpos = [0.0, 0.0, -3.0] if cmd == "$H" else [self.mpos[0], self.mpos[1], -3.0]
            self.state = "Idle"
            self.emit("ok")
            return
        target = list(self.mpos if not self.planner else self.planner[-1])
        moves = cmd.startswith("G53") or cmd.startswith("$J=")
        for axis, value in re.findall(r"([XYZ])(-?\d+(?:\.\d+)?)", cmd if moves else ""):
            target["XYZ".index(axis)] = float(value)
        if moves:
            if self.skip_moves:
                self.skip_moves -= 1
            else:
                self.waiting.append(target)
        self._fill()
        if not moves:
            self._ack()

    def _ack(self):
        if self.stop_acking_after is not None and self.lines_seen > self.stop_acking_after:
            return
        self.emit("ok")

    def _fill(self):
        while self.waiting and len(self.planner) < self.capacity:
            self.planner.append(self.waiting.pop(0))
            if not self.held:
                self.state = "Run"
            self._ack()

    def _motion(self):
        while not self.closed:
            time.sleep(self.move_s)
            with self.lock:
                if self.planner and not self.held and self.state != "Alarm":
                    self.mpos = self.planner.pop(0)
                    self._fill()
                    if not self.planner and not self.waiting:
                        self.state = "Idle"

    def read(self, count):
        try:
            return self.rx.get(timeout=0.01)
        except queue.Empty:
            return b""

    def close(self):
        self.closed = True

    def lines(self):
        with self.lock:
            return [x.decode().strip() for x in self.sent if x.endswith(b"\n")]


class ProgramLinesTests(unittest.TestCase):
    def test_comments_dropped_and_end_position(self):
        send, end = lines_and_end()
        self.assertEqual(send[:3], ["G21", "G90", "G53 G0 Z-20.000"])
        self.assertFalse([s for s in send if s.startswith("(")])
        self.assertEqual(len(send), 12)
        self.assertEqual(end, END)

    def test_refuses_anything_outside_the_allowlist(self):
        bad = {"M3 S1000": "not an allowed", "$H": "not an allowed", "S100": "not an allowed", "T1": "not an allowed",
               "G0 X1 Y1": "not an allowed", "G53 G0 Z-30.000": "travel Z", "G53 G1 X1.000 Y1.000 Z-70.000 F300": "floor",
               "G53 G1 X1.000 Y1.000 Z-50.000 F900": "feed", "G53 G1 X300.000 Y1.000 Z-50.000 F300": "outside",
               "G53 G1 X1.000 Y1.000 Z-10.000 F300": "travel", "(ok comment) M3": "not an allowed"}
        for extra, why in bad.items():
            with self.subTest(line=extra):
                text = PROGRAM.replace("G53 G0 X96.124 Y75.617", extra + "\nG53 G0 Z-20.000\nG53 G0 X96.124 Y75.617")
                with self.assertRaisesRegex(mod.MachineError, why):
                    lines_and_end(text)

    def test_structure_rules(self):
        cases = {"sideways below travel": PROGRAM.replace("G53 G0 Z-20.000\nG53 G0 X96.124", "G53 G0 X96.124"),
                 "drawing move before": PROGRAM.replace("G53 G0 Z-20.000\nG53 G0 X71.089 Y57.046\n", ""),
                 "end at travel": PROGRAM.rsplit("G53 G0 Z-20.000", 1)[0],
                 "start with G21": PROGRAM.replace("G21\n", "")}
        for why, text in cases.items():
            with self.subTest(why):
                with self.assertRaises(mod.MachineError):
                    lines_and_end(text)


class ProgramRunTests(unittest.TestCase):
    def make(self, **fake_kwargs):
        fake = FakeGrbl(**fake_kwargs)
        events = []
        link = mod.GrblProgramLink(lambda: dict(CONFIG), opener=lambda p: fake, on_event=events.append,
                                   poll_s=0.01, fresh_s=0.5, homing_timeout=1.0, move_timeout=1.0,
                                   status_timeout=0.5, ack_timeout=0.5, end_timeout=2.0, idle_away_s=0.2)

        def cleanup():
            if link.status()["connected"] and link.status()["busy"]:
                link.stop()
            try:
                link.disconnect()
            except mod.MachineError:
                pass
            fake.close()
        self.addCleanup(cleanup)
        link.connect("FAKE", wait_s=1.0)
        return link, fake, events

    def home(self, link):
        done = threading.Event()
        link.start_homing(on_done=lambda e: done.set())
        self.assertTrue(done.wait(3))
        self.assertTrue(link.status()["homed"])

    def run_program(self, link, text=PROGRAM, wait=True, **kw):
        send, end = lines_and_end(text)
        done, result = threading.Event(), {}
        link.start_program("TEST.gcode", send, kw.pop("end", end),
                           on_done=lambda e: (result.update(error=e), done.set()))
        if wait:
            self.assertTrue(done.wait(5), "run did not finish")
        return send, result, done

    def test_runs_whole_program_and_verifies_end(self):
        link, fake, events = self.make()
        self.home(link)
        send, result, _ = self.run_program(link)
        self.assertIsNone(result["error"])
        self.assertEqual(fake.lines()[2:], send)                 # after $HZ, $H: exactly the program lines
        st = link.status()
        self.assertEqual(st["program"]["state"], "finished")
        self.assertEqual(st["program"]["done"], len(send))
        self.assertTrue(st["homed"])
        self.assertIsNone(st["busy"])
        self.assertIn("program_complete", [e["event"] for e in events])
        self.assertNotIn(b"\x18", fake.sent)

    def test_requires_homing_and_idle(self):
        link, fake, _ = self.make()
        send, end = lines_and_end()
        with self.assertRaisesRegex(mod.MachineError, "Home"):
            link.start_program("T", send, end)
        self.assertEqual(fake.lines(), [])

    def test_refuses_changed_calibration_rules(self):
        link, fake, _ = self.make()
        self.home(link)
        link._calibration = lambda: dict(CONFIG, travel_z=-25.0)
        send, end = lines_and_end()
        with self.assertRaises(mod.MachineError):
            link.start_program("T", send, end)
        self.assertEqual(fake.lines(), ["$HZ", "$H"])

    def test_controller_error_stops_and_requires_homing(self):
        link, fake, _ = self.make()
        self.home(link)
        fake.fail_on = "G53 G1 X72.044 Y56.731 Z-56.043 F300"
        send, result, _ = self.run_program(link)
        self.assertIn("error:20", result["error"])
        self.assertEqual(link.status()["program"]["state"], "failed")
        self.assertFalse(link.status()["homed"])
        self.assertIn(b"!", fake.sent)
        self.assertIn(b"\x18", fake.sent)
        sent = fake.lines()
        self.assertNotIn("G53 G0 X96.124 Y75.617", sent)          # nothing after the refused line

    def test_alarm_and_restart_stop_the_run(self):
        for attr in ("alarm_at_line", "banner_at_line"):
            with self.subTest(attr):
                link, fake, _ = self.make()
                self.home(link)
                setattr(fake, attr, 6)
                send, result, _ = self.run_program(link)
                self.assertIsNotNone(result["error"])
                self.assertEqual(link.status()["program"]["state"], "failed")
                self.assertFalse(link.status()["homed"])
                self.assertLess(len(fake.lines()), 2 + len(send))

    def test_missing_acknowledgment_stops_the_run(self):
        link, fake, _ = self.make()
        self.home(link)
        fake.stop_acking_after = 5
        send, result, _ = self.run_program(link)
        self.assertIn("No acknowledgment", result["error"])
        self.assertIn(b"\x18", fake.sent)

    def test_wrong_end_position_fails(self):
        link, fake, _ = self.make()
        self.home(link)
        send, result, _ = self.run_program(link, end=(1.0, 2.0, -20.0))
        self.assertIn("not at the program end", result["error"])
        self.assertEqual(link.status()["program"]["state"], "failed")

    def test_pause_sends_nothing_new_until_resume(self):
        link, fake, events = self.make(capacity=2, move_s=0.05)
        self.home(link)
        send, result, done = self.run_program(link, wait=False)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 3))
        link.pause()
        self.assertTrue(wait_until(lambda: link.status()["state"] == "Hold"))
        time.sleep(0.3)                                            # longer than the ack timeout: no failure
        frozen = len(fake.lines())
        time.sleep(0.2)
        self.assertEqual(len(fake.lines()), frozen)
        self.assertTrue(link.status()["program"]["paused"])
        self.assertEqual(link.status()["busy"], "program")
        link.resume()
        self.assertTrue(done.wait(5))
        self.assertIsNone(result["error"])
        self.assertIn(b"~", fake.sent)
        self.assertEqual(fake.lines()[2:], send)
        self.assertEqual({"program_pause", "program_resume"} & {e["event"] for e in events}, {"program_pause", "program_resume"})

    def test_stop_marks_run_stopped(self):
        link, fake, _ = self.make(capacity=2, move_s=0.05)
        self.home(link)
        send, result, done = self.run_program(link, wait=False)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 3))
        link.stop()
        self.assertTrue(done.wait(3))
        self.assertEqual(link.status()["program"]["state"], "stopped")
        self.assertFalse(link.status()["homed"])
        self.assertLess(len(fake.lines()), 2 + len(send))

    def test_busy_link_refuses_other_motion(self):
        link, fake, _ = self.make(capacity=2, move_s=0.05)
        self.home(link)
        send, end = lines_and_end()
        _, _, done = self.run_program(link, wait=False)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 2))
        for call in (lambda: link.start_program("T", send, end), link.start_scan_position, link.start_homing):
            with self.assertRaisesRegex(mod.MachineError, "busy"):
                call()
        with self.assertRaises(mod.MachineError):
            link.disconnect()
        self.assertTrue(done.wait(5))

    def jog(self, link, axis, direction, step):
        done, result = threading.Event(), {}
        link.start_jog(axis, direction, step, on_done=lambda e: (result.update(error=e), done.set()))
        self.assertTrue(done.wait(3))
        return result["error"]

    def test_jog_target_rules(self):
        t = mod.GrblProgramLink.jog_target
        self.assertEqual(t((10.0, 10.0, -40.0), "Z", 1, 10.0), (10.0, 10.0, -30.0))     # Z up from the scan pose
        self.assertEqual(t((10.0, 10.0, -20.0), "X", 1, 0.1), (10.1, 10.0, -20.0))
        refused = [((10.0, 10.0, -40.0), "X", 1, 1.0),       # X/Y below travel height
                   ((10.0, 10.0, -40.0), "Z", -1, 0.1),      # Z down below travel height
                   ((10.0, 10.0, -15.0), "Z", -1, 10.0),     # would pass below -20
                   ((10.0, 10.0, -0.5), "Z", 1, 1.0),        # above the top
                   ((0.0, 10.0, -5.0), "X", -1, 1.0),        # outside the machine
                   ((10.0, 176.0, -5.0), "Y", 1, 0.1),
                   ((10.0, 10.0, -5.0), "Z", 1, 5.0),        # step not offered
                   ((10.0, 10.0, -5.0), "A", 1, 1.0),
                   ((10.0, 10.0, -5.0), "X", 2, 1.0)]
        for args in refused:
            with self.subTest(args=args):
                with self.assertRaises(mod.MachineError):
                    t(*args)

    def test_jog_needs_homing_then_moves_and_verifies(self):
        link, fake, events = self.make()
        with self.assertRaisesRegex(mod.MachineError, "Home"):
            link.start_jog("Z", -1, 1.0)
        self.assertEqual(fake.lines(), [])
        self.home(link)
        self.assertIsNone(self.jog(link, "Z", -1, 10.0))
        self.assertIsNone(self.jog(link, "X", 1, 10.0))
        self.assertEqual(fake.lines()[2:], ["$J=G21 G90 G53 Z-13.000 F300", "$J=G21 G90 G53 X10.000 F1000"])
        st = link.status()
        self.assertEqual(st["mpos"], [10.0, 0.0, -13.0])
        self.assertTrue(st["homed"])
        self.assertIsNone(st["busy"])
        with self.assertRaisesRegex(mod.MachineError, "travel height"):
            link.start_jog("Z", -1, 10.0)                          # -23 would be below Z -20
        self.assertEqual(len(fake.lines()), 4)
        self.assertEqual([e["event"] for e in events if e["event"] == "jog"], ["jog", "jog"])

    def test_jog_refused_while_a_program_runs(self):
        link, fake, _ = self.make(capacity=2, move_s=0.05)
        self.home(link)
        _, _, done = self.run_program(link, wait=False)
        self.assertTrue(wait_until(lambda: link.status()["busy"] == "program"))
        with self.assertRaisesRegex(mod.MachineError, "busy"):
            link.start_jog("Z", 1, 1.0)
        self.assertTrue(done.wait(5))

    def test_pause_and_resume_need_a_run(self):
        link, _, _ = self.make()
        self.home(link)
        with self.assertRaises(mod.MachineError):
            link.pause()
        with self.assertRaises(mod.MachineError):
            link.resume()


if __name__ == "__main__":
    unittest.main()
