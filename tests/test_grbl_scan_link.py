"""Fake serial regression tests. No real serial port or machine is used."""
import importlib.util
from pathlib import Path
import queue
import re
import threading
import time
import unittest


MODULE = Path(__file__).resolve().parents[1] / "henna_preview" / "grbl_scan_link.py"
spec = importlib.util.spec_from_file_location("grbl_scan_link_v13_test", MODULE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

CONFIG = {"capture_pose_mpos": [145.8, 42.6, -40.0], "travel_z": -20.0,
          "draw_feed_mm_min": 300.0, "travel_feed_mm_min": 1000.0}


def wait_until(predicate, seconds=2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


class FakeSerial:
    def __init__(self, state="Idle"):
        self.lock = threading.RLock()
        self.rx = queue.Queue()
        self.sent = []
        self.state = state
        self.mpos = [0.0, 0.0, -3.0]
        self.target = None
        self.closed = False
        self.auto_complete = True
        self.respond_status = True
        self.ack_first = True
        self.fail = None
        self.read_error = None
        self.delay = 0.025

    def emit(self, text):
        self.rx.put((text + "\r\n").encode("ascii"))

    def status_text(self):
        return f"<{self.state}|MPos:{self.mpos[0]:.3f},{self.mpos[1]:.3f},{self.mpos[2]:.3f}|FS:0,0>"

    def write(self, data):
        with self.lock:
            if self.closed:
                raise OSError("fake serial closed")
            self.sent.append(data)
            if data == b"?":
                if self.respond_status:
                    self.emit(self.status_text())
            elif data == b"\x18":
                self.state = "Idle"
                self.emit("Grbl 1.3a ['$' for help]")
            elif data == b"!":
                self.state = "Hold"
            elif data.endswith(b"\n"):
                cmd = data.decode().strip()
                if self.fail == cmd:
                    self.emit("error:9")
                    return len(data)
                self.target = list(self.mpos)
                if cmd in ("$HZ", "$H"):
                    self.state = "Home"
                    if cmd == "$H":
                        self.target = [0.0, 0.0, -3.0]
                    else:
                        self.target[2] = -3.0
                else:
                    self.state = "Jog"
                    for axis, value in re.findall(r"([XYZ])(-?\d+(?:\.\d+)?)", cmd):
                        self.target["XYZ".index(axis)] = float(value)
                if self.ack_first:
                    self.emit("ok")
                if self.auto_complete:
                    timer = threading.Timer(self.delay, self.complete)
                    timer.daemon = True
                    timer.start()
        return len(data)

    def complete(self, position=None):
        with self.lock:
            if self.closed:
                return
            self.mpos = list(self.target if position is None else position)
            self.state = "Idle"
            self.emit(self.status_text())
            if not self.ack_first:
                self.emit("ok")

    def read(self, count):
        if self.read_error:
            raise self.read_error
        try:
            return self.rx.get(timeout=0.01)
        except queue.Empty:
            return b""

    def close(self):
        self.closed = True

    def commands(self):
        with self.lock:
            return [x.decode().strip() for x in self.sent if x.endswith(b"\n")]


class ScanLinkTests(unittest.TestCase):
    def make(self, **kwargs):
        fake = FakeSerial()
        cfg = dict(CONFIG)
        cfg["capture_pose_mpos"] = list(CONFIG["capture_pose_mpos"])
        events = []
        link = mod.GrblScanLink(lambda: cfg, opener=lambda p: fake, on_event=events.append,
                                poll_s=0.01, fresh_s=0.3, homing_timeout=0.8,
                                move_timeout=0.4, status_timeout=0.2, **kwargs)
        def cleanup():
            if link.status()["connected"] and link.status()["busy"]:
                link.stop()
            try:
                link.disconnect()
            except mod.MachineError:
                pass
        self.addCleanup(cleanup)
        link.connect("FAKE", wait_s=0.5)
        return link, fake, cfg, events

    def operation(self, start):
        done, result = threading.Event(), {}
        start(on_done=lambda error: (result.update(error=error), done.set()))
        self.assertTrue(done.wait(3), "worker failed to finish")
        return result["error"]

    def home(self, link):
        self.assertIsNone(self.operation(link.start_homing))
        self.assertTrue(link.status()["homed"])

    def test_connect_and_pose_alone_do_not_authorize_motion_or_recording(self):
        link, fake, _, _ = self.make()
        fake.mpos = list(mod.POSE)
        fake.emit(fake.status_text())
        self.assertTrue(wait_until(lambda: link.status()["mpos"] == list(mod.POSE)))
        with self.assertRaises(mod.MachineError):
            link.start_scan_position()
        with self.assertRaises(mod.MachineError):
            with link.capture_guard():
                self.fail("recording must not start")
        self.assertFalse(link.status()["capture_ready"])

    def test_homing_order_and_exact_fixed_route(self):
        link, fake, _, events = self.make()
        self.home(link)
        self.assertIsNone(self.operation(link.start_scan_position))
        self.assertEqual(fake.commands(), ["$HZ", "$H"] + [c for _, c in mod.ROUTE])
        self.assertTrue(link.status()["capture_ready"])
        complete = [e for e in events if e["event"] == "stage_complete"]
        self.assertEqual(len(complete), 5)
        self.assertTrue(all(e["verified_line"].startswith("<Idle|") for e in complete))
        with link.capture_guard() as line:
            self.assertIn("145.800,42.600,-40.000", line)
            self.assertEqual(link.status()["busy"], "recording")
            with self.assertRaises(mod.MachineError):
                link.start_homing()
            with self.assertRaises(mod.MachineError):
                link.start_scan_position()
            with self.assertRaises(mod.MachineError):
                link.disconnect()
        self.assertIsNone(link.status()["busy"])

    def test_ok_before_motion_completion_does_not_advance(self):
        link, fake, _, _ = self.make()
        self.home(link)
        fake.auto_complete = False
        done = threading.Event()
        result = {}
        link.start_scan_position(on_done=lambda error: (result.update(error=error), done.set()))
        self.assertTrue(wait_until(lambda: len(fake.commands()) == 3))
        time.sleep(0.06)
        self.assertEqual(len(fake.commands()), 3)
        fake.complete(position=[0, 0, -30])  # fresh Idle, but WRONG stage Z
        time.sleep(0.06)
        self.assertEqual(len(fake.commands()), 3)
        fake.auto_complete = True
        fake.complete(position=[0, 0, -20])
        self.assertTrue(done.wait(2))
        self.assertIsNone(result["error"])
        self.assertTrue(link.status()["capture_ready"])

    def test_pre_ack_idle_without_new_status_cannot_finish_homing(self):
        link, fake, _, _ = self.make()
        fake.auto_complete = False
        fake.ack_first = False
        done, result = threading.Event(), {}
        link.start_homing(on_done=lambda error: (result.update(error=error), done.set()))
        self.assertTrue(wait_until(lambda: fake.commands() == ["$HZ"]))
        fake.respond_status = False
        fake.complete()  # emits Idle then ACK; no response to post-ACK queries
        self.assertTrue(done.wait(2))
        self.assertIsNotNone(result["error"])
        self.assertEqual(fake.commands(), ["$HZ"])
        self.assertFalse(link.status()["homed"])
        self.assertIn(b"!", fake.sent)
        self.assertIn(b"\x18", fake.sent)

    def test_ack_after_motion_status_requires_and_accepts_new_query(self):
        link, fake, _, _ = self.make()
        fake.ack_first = False
        self.home(link)
        self.assertIsNone(self.operation(link.start_scan_position))
        self.assertTrue(link.status()["capture_ready"])

    def test_fresh_alarm_allows_homing_and_requires_new_idle_after_each_ack(self):
        link, fake, _, _ = self.make()
        fake.state = "Alarm"
        fake.emit(fake.status_text())
        self.assertTrue(wait_until(lambda: link.status()["state"] == "Alarm"))
        self.home(link)
        self.assertEqual(fake.commands(), ["$HZ", "$H"])

    def test_stop_interrupts_sequence_and_invalidates_readiness(self):
        link, fake, _, _ = self.make()
        self.home(link)
        fake.auto_complete = False
        done = threading.Event()
        link.start_scan_position(on_done=lambda error: done.set())
        self.assertTrue(wait_until(lambda: len(fake.commands()) == 3))
        with self.assertRaises(mod.MachineError):
            link.disconnect()
        link.stop()
        self.assertTrue(done.wait(1))
        fake.complete()
        time.sleep(0.08)
        self.assertEqual(len(fake.commands()), 3)
        self.assertIn(b"!", fake.sent)
        self.assertIn(b"\x18", fake.sent)
        self.assertFalse(link.status()["homed"])
        self.assertFalse(link.status()["capture_ready"])

    def test_error_or_alarm_or_restart_stops_later_stages(self):
        for fault in ("error:9", "ALARM:1", "Grbl 1.3a ['$' for help]"):
            with self.subTest(fault=fault):
                link, fake, _, _ = self.make()
                self.home(link)
                fake.auto_complete = False
                done = threading.Event()
                link.start_scan_position(on_done=lambda error: done.set())
                self.assertTrue(wait_until(lambda: len(fake.commands()) == 3))
                fake.emit(fault)
                self.assertTrue(done.wait(1))
                fake.complete()
                time.sleep(0.04)
                self.assertEqual(len(fake.commands()), 3)
                self.assertFalse(link.status()["homed"])
                self.assertFalse(link.status()["capture_ready"])
                self.assertIn(b"!", fake.sent)
                self.assertIn(b"\x18", fake.sent)

    def test_config_change_refused_without_any_jog(self):
        link, fake, cfg, _ = self.make()
        self.home(link)
        cfg["travel_z"] = -45.0
        with self.assertRaises(mod.MachineError):
            link.start_scan_position()
        self.assertEqual(fake.commands(), ["$HZ", "$H"])

    def test_stale_or_running_status_refuses_homing(self):
        link, fake, _, _ = self.make()
        fake.respond_status = False
        time.sleep(0.35)
        with self.assertRaises(mod.MachineError):
            link.start_homing()
        fake.state = "Run"
        fake.emit(fake.status_text())
        self.assertTrue(wait_until(lambda: link.status()["state"] == "Run"))
        with self.assertRaises(mod.MachineError):
            link.start_homing()
        self.assertEqual(fake.commands(), [])

    def test_capture_guard_detects_motion_and_keeps_lock_after_stop(self):
        link, fake, _, _ = self.make()
        self.home(link)
        self.assertIsNone(self.operation(link.start_scan_position))
        with self.assertRaises(mod.MachineError):
            with link.capture_guard():
                link.stop()
                self.assertEqual(link.status()["busy"], "recording")
                with self.assertRaises(mod.MachineError):
                    link.start_homing()
        self.assertIsNone(link.status()["busy"])
        self.assertFalse(link.status()["capture_ready"])

    def test_capture_guard_polls_and_rejects_drift_even_if_pose_returns(self):
        link, fake, _, _ = self.make()
        self.home(link)
        self.assertIsNone(self.operation(link.start_scan_position))
        with self.assertRaises(mod.MachineError):
            with link.capture_guard():
                before = len(fake.sent)
                fake.mpos[0] += 0.2
                self.assertTrue(wait_until(lambda: not link.status()["homed"]))
                self.assertGreater(len(fake.sent), before)
                fake.mpos = list(mod.POSE)
        self.assertFalse(link.status()["capture_ready"])

    def test_serial_loss_cancels_operation_without_reusing_new_port(self):
        link, fake, _, _ = self.make()
        self.home(link)
        fake.auto_complete = False
        done = threading.Event()
        link.start_scan_position(on_done=lambda error: done.set())
        self.assertTrue(wait_until(lambda: len(fake.commands()) == 3))
        fake.read_error = OSError("unplugged")
        self.assertTrue(done.wait(1))
        self.assertFalse(link.status()["connected"])
        self.assertFalse(link.status()["homed"])
        new = FakeSerial()
        link._open = lambda p: new
        link.connect("NEW", wait_s=0.5)
        time.sleep(0.06)
        self.assertEqual(new.commands(), [])
        self.assertFalse(link.status()["homed"])

    def test_old_connection_reader_cannot_set_state_or_ack_on_new_connection(self):
        link, old, _, _ = self.make()
        old_generation = link._generation
        link.disconnect()
        new = FakeSerial()
        link._open = lambda p: new
        link.connect("FAKE2", wait_s=0.5)
        link._handle("<Idle|MPos:145.800,42.600,-40.000|FS:0,0>", old_generation)
        link._handle("ok", old_generation)
        self.assertEqual(link.status()["mpos"], [0.0, 0.0, -3.0])
        self.assertFalse(link.status()["homed"])
        self.assertTrue(old.closed)

    def test_capture_guard_rejects_status_gap_even_if_reporting_recovers(self):
        link, fake, _, _ = self.make()
        self.home(link)
        self.assertIsNone(self.operation(link.start_scan_position))
        with self.assertRaises(mod.MachineError):
            with link.capture_guard():
                fake.respond_status = False
                self.assertTrue(wait_until(lambda: not link.status()["homed"]))
                fake.respond_status = True
        self.assertFalse(link.status()["capture_ready"])

    def test_event_logging_failure_prevents_next_command(self):
        link, fake, _, _ = self.make()
        def fail(event):
            if event["event"] == "stage_complete":
                raise OSError("log disk full")
        link._on_event = fail
        error = self.operation(link.start_homing)
        self.assertIn("log disk full", error)
        self.assertEqual(fake.commands(), ["$HZ"])
        self.assertFalse(link.status()["homed"])

    def test_public_api_has_no_arbitrary_command_sender(self):
        public = sorted(n for n in dir(mod.GrblScanLink) if not n.startswith("_"))
        self.assertEqual(public, ["capture_guard", "connect", "disconnect", "start_homing", "start_scan_position", "status", "status_line", "stop"])


if __name__ == "__main__":
    unittest.main()
