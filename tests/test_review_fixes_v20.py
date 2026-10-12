"""Review fixes (v20): each test reproduces a Codex review finding on v15 and shows v20 fixes it. Fake controller only."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import grbl_program_link_v15 as v15  # noqa: E402
from henna_preview import grbl_program_link_v20 as mod  # noqa: E402
from tests.test_grbl_program_link import CONFIG, FakeGrbl, lines_and_end, wait_until  # noqa: E402


class Base(unittest.TestCase):
    def make(self, cls=mod.GrblProgramLinkV20, **fake_kwargs):
        fake = FakeGrbl(**fake_kwargs)
        link = cls(lambda: dict(CONFIG), opener=lambda p: fake, on_event=lambda e: None,
                   poll_s=0.05, fresh_s=0.5, homing_timeout=1.0, move_timeout=1.0, status_timeout=0.5,
                   ack_timeout=0.5, end_timeout=3.0, idle_away_s=0.2)

        def cleanup():
            try:
                if link.status()["connected"] and link.status()["busy"]:
                    link.stop()
            except mod.MachineError:
                pass
            try:
                link.disconnect()
            except mod.MachineError:
                pass
            fake.close()
        self.addCleanup(cleanup)
        link.connect("FAKE", wait_s=1.0)
        self.home(link)
        return link, fake

    def home(self, link):
        # like the page: Home only once a fresh position report has arrived (e.g. after a reset)
        self.assertTrue(wait_until(lambda: link.status()["age_s"] is not None and link.status()["age_s"] < 0.3, 3))
        done = threading.Event()
        link.start_homing(on_done=lambda e: done.set())
        self.assertTrue(done.wait(3))

    def start(self, link, name="T.gcode", on_done=None):
        send, end = lines_and_end()
        done, result = threading.Event(), {}

        def finished(*args):
            result["args"] = args
            if on_done:
                on_done(*args)
            done.set()
        link.start_program(name, send, end, on_done=finished)
        return result, done


class StopResetsEvenIfHoldFails(Base):
    def test_v15_skipped_the_reset(self):                    # finding 1, as found
        link, fake = self.make(v15.GrblProgramLinkV15, capacity=4, move_s=0.3)
        _, done = self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        fake.fail_writes = {b"!"}
        try:
            link.stop()
        except mod.MachineError:
            pass
        self.assertNotIn(b"\x18", fake.sent)

    def test_v20_resets_and_reports_unconfirmed(self):
        link, fake = self.make(capacity=4, move_s=0.3)
        _, done = self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        fake.fail_writes = {b"!"}
        with self.assertRaisesRegex(mod.MachineError, "emergency stop"):
            link.stop()
        self.assertIn(b"\x18", fake.sent)                    # the reset was still sent
        st = link.status()
        self.assertTrue(st["stop_unconfirmed"])
        self.assertFalse(st["homed"])
        self.assertTrue(done.wait(3))
        self.home(link)
        self.assertFalse(link.status()["stop_unconfirmed"])  # cleared by a successful Home

    def test_v20_both_writes_failing_is_unconfirmed(self):
        link, fake = self.make(capacity=4, move_s=0.3)
        self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        fake.fail_writes = {b"!", b"\x18"}
        with self.assertRaises(mod.MachineError):
            link.stop()
        self.assertTrue(link.status()["stop_unconfirmed"])
        self.assertIsNone(link.status()["busy"])


class PauseNeedsConfirmation(Base):
    def silent_ignored_pause(self, cls):
        link, fake = self.make(cls, capacity=4, move_s=0.6)
        result, done = self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        fake.ignore_hold = True
        fake.silent = True
        link.pause()
        return link, fake, result, done

    def test_v15_stayed_paused_while_moving(self):           # finding 2, as found
        link, fake, result, done = self.silent_ignored_pause(v15.GrblProgramLinkV15)
        time.sleep(2.0)
        self.assertFalse(done.is_set())
        self.assertNotIn(b"\x18", fake.sent)
        fake.silent = False
        link.stop()

    def test_v20_stops_when_pause_is_not_confirmed(self):
        link, fake, result, done = self.silent_ignored_pause(mod.GrblProgramLinkV20)
        self.assertTrue(done.wait(3.0))
        self.assertIn("not confirmed", result["args"][0])
        self.assertIn(b"\x18", fake.sent)
        self.assertFalse(link.status()["homed"])

    def test_v20_normal_pause_still_works(self):
        link, fake = self.make(capacity=2, move_s=0.1)
        result, done = self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 3))
        link.pause()
        time.sleep(2.0)                                      # longer than the 1.5 s confirmation window
        self.assertEqual(link.status()["busy"], "program")
        self.assertNotIn(b"\x18", fake.sent)
        link.resume()
        self.assertTrue(done.wait(5))
        self.assertIsNone(result["args"][0])


class RecordBeforeRelease(Base):
    def test_snapshot_belongs_to_the_finished_run(self):    # finding 3
        link, fake = self.make(capacity=4, move_s=0.02)
        send, end = lines_and_end()
        seen = {}

        def first_done(error, snapshot):
            link.start_program("SECOND.gcode", send, end)     # start the next run before recording the first
            seen["snapshot"] = snapshot
            seen["live"] = link.status()["program"]["name"]
        result, done = self.start(link, "FIRST.gcode", on_done=first_done)
        self.assertTrue(done.wait(5))
        snap = seen["snapshot"]["program"]
        self.assertEqual((snap["name"], snap["state"], snap["done"]), ("FIRST.gcode", "finished", len(send)))
        self.assertEqual(seen["live"], "SECOND.gcode")
        self.assertTrue(wait_until(lambda: link.status()["program"]["state"] == "finished", 5))


if __name__ == "__main__":
    unittest.main()


from unittest.mock import patch  # noqa: E402

import webui_server_v20 as server  # noqa: E402
from tests.test_studio_v14 import ALL_TICKS, JOB, V14Base  # noqa: E402


class ServerRecordsSnapshot(V14Base):
    def test_run_result_comes_from_the_snapshot(self):
        link = self.enterContext(patch.object(server, "machine"))
        link.status.return_value = {"busy": None, "mpos": [1.0, 2.0, -40.0], "program": None}
        server.run_start(dict({"job": JOB}, **ALL_TICKS))
        on_done = link.start_program.call_args[1]["on_done"]
        link.status.return_value = {"mpos": None, "program": {"state": "running", "done": 0, "elapsed_s": 0.1}}  # next run
        snap = {"mpos": [95.4, 75.4, -20.0], "program": {"state": "finished", "done": 12, "elapsed_s": 9.9}}
        on_done(None, snap)
        run = self.job()["runs"][-1]
        self.assertEqual((run["result"], run["lines_done"], run["elapsed_s"]), ("finished", 12, 9.9))

    def test_routes_and_page(self):
        self.assertIs(server.Handler.POST_API["/api/machine/run"][0], server.run_start)
        self.assertEqual(server.Handler.server_version, "HennaStudio/0.20")
        page = open(server.STUDIO_HTML, encoding="utf-8").read()
        self.assertIn("STOP NOT CONFIRMED", page)
        self.assertIn("Ready to run again", page)
