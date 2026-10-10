"""GRBL link with a fake serial port: status parsing, the only commands it can send, homing order, stop."""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.grbl_link import GrblLink, MachineError  # noqa: E402


class FakeSerial:
    """Answers '?' with a status line; answers $HZ/$H with ok after a short delay."""

    def __init__(self, state="Idle", fail=None):
        self.state, self.fail = state, fail
        self.out = b""
        self.sent = []
        self.lock = threading.Lock()

    def write(self, data):
        with self.lock:
            self.sent.append(data)
            if data == b"?":
                self.out += f"<{self.state}|MPos:145.800,42.599,-40.000|FS:0,0>\r\n".encode()
            elif data.endswith(b"\n"):
                cmd = data.strip().decode()
                if self.fail == cmd:
                    self.out += b"error:9\r\n"
                else:
                    self.state = "Home"
                    threading.Timer(0.15, self._ok).start()

    def _ok(self):
        with self.lock:
            self.state = "Idle"
            self.out += b"ok\r\n"

    def read(self, n):
        time.sleep(0.01)
        with self.lock:
            d, self.out = self.out[:n], self.out[n:]
        return d

    def close(self):
        pass


class Link(unittest.TestCase):
    def make(self, **kw):
        self.fake = FakeSerial(**kw)
        link = GrblLink(opener=lambda port: self.fake)
        self.addCleanup(link.disconnect)
        return link

    def test_connect_reads_position_and_status_line_needs_idle(self):
        link = self.make()
        st = link.connect("COM7")
        self.assertEqual((st["state"], st["mpos"]), ("Idle", [145.8, 42.599, -40.0]))
        self.assertEqual(link.status_line(), "<Idle|MPos:145.800,42.599,-40.000|FS:0,0>")
        self.fake.state = "Run"
        time.sleep(0.4)
        with self.assertRaises(MachineError):
            link.status_line()

    def test_homing_sends_exactly_HZ_then_H(self):
        link = self.make()
        link.connect("COM7")
        done = threading.Event()
        res = {}
        link.start_homing(on_done=lambda err: (res.update(err=err), done.set()))
        self.assertTrue(done.wait(5))
        self.assertIsNone(res["err"])
        lines = [d for d in self.fake.sent if d != b"?"]
        self.assertEqual(lines, [b"$HZ\n", b"$H\n"])

    def test_homing_refused_while_running_and_errors_are_reported(self):
        link = self.make(state="Run")
        link.connect("COM7")
        with self.assertRaises(MachineError):
            link.start_homing()
        link2 = self.make(fail="$HZ")
        link2.connect("COM7")
        done = threading.Event()
        res = {}
        link2.start_homing(on_done=lambda err: (res.update(err=err), done.set()))
        self.assertTrue(done.wait(5))
        self.assertIn("error:9", res["err"])
        self.assertNotIn(b"$H\n", self.fake.sent)        # $H is not sent after a failed $HZ

    def test_stop_sends_feed_hold_then_reset(self):
        link = self.make()
        link.connect("COM7")
        link.stop()
        self.assertEqual([d for d in self.fake.sent if d != b"?"][-2:], [b"!", b"\x18"])

    def test_no_way_to_send_other_lines(self):
        public = [n for n in dir(GrblLink) if not n.startswith("_")]
        self.assertEqual(sorted(public), ["connect", "disconnect", "start_homing", "status", "status_line", "stop"])

    def test_restart_banner_is_reported(self):
        link = self.make()
        link.connect("COM7")
        link._handle("Grbl 1.3a ['$' for help]")
        self.assertTrue(link.status()["restarted"])


if __name__ == "__main__":
    unittest.main()
