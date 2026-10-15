"""machine_laser_mode_set.py: writes only $32, only from Idle/Alarm, checks nothing else changed, and survives a late
boot 'ok' that cuts the first $$ reply short (the Pi, 2026-10-15)."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_laser_mode_set as lm  # noqa: E402


class FakeGrbl:
    def __init__(self, state="Idle", stray_ok=False):
        self.state, self.out, self.got = state, b"", []
        self.settings = {30: 1000.0, 32: 1.0, 100: 403.5, 102: 400.0}
        self.stray_ok = stray_ok

    def write(self, data):
        self.got.append(data)
        if data == b"?":
            self.out += f"<{self.state}|MPos:0.000,0.000,0.000|FS:0,0>\r\n".encode()
        elif data == b"$$\n":
            if self.stray_ok:                                  # the late boot 'ok' arrives first
                self.out += b"ok\r\n"
                self.stray_ok = False
            self.out += b"".join(f"${k}={v:.3f}\r\n".encode() for k, v in sorted(self.settings.items())) + b"ok\r\n"
        elif data.startswith(b"$32="):
            self.settings[32] = float(data[4:])
            self.out += b"ok\r\n"
        return len(data)

    def read(self, n):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk


class LaserModeTests(unittest.TestCase):
    def setUp(self):
        self._rl = lm.read_lines
        lm.read_lines = lambda ser, seconds, until_ok=False: self._rl(ser, min(seconds, 0.05), until_ok)

    def tearDown(self):
        lm.read_lines = self._rl

    def test_sets_32_only(self):
        g = FakeGrbl()
        r = lm.change_laser_mode(lm.OnlyLaserMode(g, "0"), "0")
        self.assertTrue(r["ok"])
        self.assertEqual((r["before"][32], r["after"][32], r["after"][30]), (1.0, 0.0, 1000.0))
        self.assertEqual([x for x in g.got if x.startswith(b"$3")], [b"$32=0\n"])

    def test_late_boot_ok_is_survived(self):
        g = FakeGrbl(stray_ok=True)
        r = lm.change_laser_mode(lm.OnlyLaserMode(g, "0"), "0")
        self.assertTrue(r["ok"])
        self.assertEqual(r["after"][32], 0.0)

    def test_refuses_while_moving_and_other_commands(self):
        with self.assertRaisesRegex(RuntimeError, "must be Idle or Alarm"):
            lm.change_laser_mode(lm.OnlyLaserMode(FakeGrbl(state="Run"), "0"), "0")
        with self.assertRaisesRegex(RuntimeError, "refuses"):
            lm.OnlyLaserMode(FakeGrbl(), "0").write(b"$30=255\n")
        with self.assertRaises(ValueError):
            lm.write_command("2")


if __name__ == "__main__":
    unittest.main()
