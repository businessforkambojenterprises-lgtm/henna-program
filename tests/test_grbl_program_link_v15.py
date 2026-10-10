"""Studio v15 link fixes against the fake GRBL controller: fresh status during a run, verified Pause.

No real serial port or machine is used.
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import grbl_program_link as v14  # noqa: E402
from henna_preview import grbl_program_link_v15 as mod  # noqa: E402
from tests.test_grbl_program_link import CONFIG, PROGRAM, FakeGrbl, lines_and_end, wait_until  # noqa: E402


class V15Run(unittest.TestCase):
    def make(self, cls=mod.GrblProgramLinkV15, **fake_kwargs):
        fake = FakeGrbl(**fake_kwargs)
        link = cls(lambda: dict(CONFIG), opener=lambda p: fake, on_event=lambda e: None,
                   poll_s=0.05, fresh_s=0.5, homing_timeout=1.0, move_timeout=1.0, status_timeout=0.5,
                   ack_timeout=0.5, end_timeout=3.0, idle_away_s=0.2)

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
        done = threading.Event()
        link.start_homing(on_done=lambda e: done.set())
        self.assertTrue(done.wait(3))
        return link, fake

    def start(self, link):
        send, end = lines_and_end()
        done, result = threading.Event(), {}
        link.start_program("T.gcode", send, end, on_done=lambda e: (result.update(error=e), done.set()))
        return send, result, done

    def polls_during_run(self, cls):
        # Big planner: every line is acknowledged at once, as during smooth drawing on the real controller.
        link, fake = self.make(cls, capacity=1000, move_s=0.08)
        before = fake.sent.count(b"?")
        _, result, done = self.start(link)
        self.assertTrue(done.wait(5))
        self.assertIsNone(result["error"])
        return fake.sent.count(b"?") - before

    def test_status_is_polled_through_the_whole_run(self):
        # 10 moves x 0.08 s = 0.8 s of motion, polled every 0.05 s.
        self.assertGreaterEqual(self.polls_during_run(mod.GrblProgramLinkV15), 12)

    def test_v14_did_not_poll_while_acks_were_fast(self):
        # Documents the defect seen on the real machine; v14 itself stays unchanged.
        self.assertLess(self.polls_during_run(v14.GrblProgramLink), self.polls_during_run(mod.GrblProgramLinkV15))

    def test_position_stays_fresh_while_drawing(self):
        link, fake = self.make(capacity=1000, move_s=0.08)
        _, _, done = self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 12))   # all lines queued
        ages = []
        while not done.is_set():
            st = link.status()
            if st["age_s"] is not None:
                ages.append(st["age_s"])
            time.sleep(0.02)
        self.assertTrue(ages)
        self.assertLess(max(ages), 0.3)

    def test_pause_that_holds_needs_no_extra_hold(self):
        link, fake = self.make(capacity=2, move_s=0.1)
        _, result, done = self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 3))
        link.pause()
        time.sleep(0.6)
        self.assertEqual(fake.sent.count(b"!"), 1)
        self.assertEqual(link.status()["busy"], "program")
        link.resume()
        self.assertTrue(done.wait(5))
        self.assertIsNone(result["error"])

    def test_pause_that_does_not_hold_is_resent_then_stopped(self):
        link, fake = self.make(capacity=4, move_s=0.6)      # about 2 s of motion still queued at Pause
        fake.ignore_hold = True
        _, result, done = self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        link.pause()
        self.assertTrue(done.wait(5))
        self.assertIn("Pause did not hold", result["error"])
        self.assertGreater(fake.sent.count(b"!"), 2)          # pause + re-sent holds + the stop's hold
        self.assertIn(b"\x18", fake.sent)
        st = link.status()
        self.assertEqual(st["program"]["state"], "failed")
        self.assertFalse(st["homed"])


class V15Server(unittest.TestCase):
    def test_server_uses_the_v15_link_everywhere(self):
        import webui_server_v15 as server
        self.assertIsInstance(server.machine, mod.GrblProgramLinkV15)
        self.assertIs(server.v14.machine, server.machine)
        self.assertIs(server.v14.v13.machine, server.machine)
        self.assertEqual(server.Handler.server_version, "HennaStudio/0.15")
        self.assertIn("/api/machine/jog", server.Handler.POST_API)
        self.assertIn("program", server.v14.MOVING)


if __name__ == "__main__":
    unittest.main()
