"""machine_homing_speed_set.py: writes only $25 (100-1000), only from Idle/Alarm, checks nothing else changed, and
survives a late boot 'ok'."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_homing_speed_set as hs  # noqa: E402


class FakeGrbl:
    def __init__(self, state="Idle", stray_ok=False):
        self.state, self.out, self.got, self.stray_ok = state, b"", [], stray_ok
        self.settings = {24: 50.0, 25: 100.0, 112: 300.0, 32: 0.0}

    def write(self, data):
        self.got.append(data)
        if data == b"?":
            self.out += f"<{self.state}|MPos:0.000,0.000,0.000|FS:0,0>\r\n".encode()
        elif data == b"$$\n":
            if self.stray_ok:
                self.out += b"ok\r\n"
                self.stray_ok = False
            self.out += b"".join(f"${k}={v:.3f}\r\n".encode() for k, v in sorted(self.settings.items())) + b"ok\r\n"
        elif data.startswith(b"$25="):
            self.settings[25] = float(data[4:])
            self.out += b"ok\r\n"
        return len(data)

    def read(self, n):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk


class HomingSeekTests(unittest.TestCase):
    def setUp(self):
        self._rl = hs.read_lines
        hs.read_lines = lambda ser, seconds, until_ok=False: self._rl(ser, min(seconds, 0.05), until_ok)

    def tearDown(self):
        hs.read_lines = self._rl

    def test_sets_25_only(self):
        g = FakeGrbl(stray_ok=True)
        r = hs.change_homing_seek(hs.OnlyHomingSeek(g, 500), 500)
        self.assertTrue(r["ok"])
        self.assertEqual((r["before"][25], r["after"][25], r["after"][24]), (100.0, 500.0, 50.0))
        self.assertEqual([x for x in g.got if x.startswith(b"$2")], [b"$25=500.000\n"])

    def test_refusals(self):
        with self.assertRaisesRegex(RuntimeError, "must be Idle or Alarm"):
            hs.change_homing_seek(hs.OnlyHomingSeek(FakeGrbl(state="Home"), 500), 500)
        with self.assertRaisesRegex(RuntimeError, "refuses"):
            hs.OnlyHomingSeek(FakeGrbl(), 500).write(b"$24=200\n")
        for bad in (50, 1500):
            with self.assertRaises(ValueError):
                hs.write_command(bad)


if __name__ == "__main__":
    unittest.main()
