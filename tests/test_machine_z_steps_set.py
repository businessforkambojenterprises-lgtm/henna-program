"""machine_z_steps_set.py: writes only $102, only from Idle/Alarm, and checks nothing else changed."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import machine_z_steps_set as zs  # noqa: E402


class FakeGrbl:
    def __init__(self, state="Idle"):
        self.state, self.settings, self.out, self.got = state, {100: 400.0, 101: 400.0, 102: 100.0, 132: 299.0}, b"", []

    def write(self, data):
        self.got.append(data)
        if data == b"?":
            self.out += f"<{self.state}|MPos:0.000,0.000,0.000|FS:0,0>\r\n".encode()
        elif data == b"$$\n":
            self.out += b"".join(f"${k}={v:.3f}\r\n".encode() for k, v in sorted(self.settings.items())) + b"ok\r\n"
        elif data[:2] == b"$1" and b"=" in data:
            k, v = data[1:].split(b"=")
            self.settings[int(k)] = float(v)
            self.out += b"ok\r\n"
        return len(data)

    def read(self, n):
        chunk, self.out = self.out[:n], self.out[n:]
        return chunk


class ZStepsTests(unittest.TestCase):
    def setUp(self):
        self._rl = zs.read_lines
        zs.read_lines = lambda ser, seconds, until_ok=False: self._rl(ser, min(seconds, 0.05), until_ok)

    def tearDown(self):
        zs.read_lines = self._rl

    def test_changes_only_z_steps(self):
        g = FakeGrbl()
        ser = zs.OnlyZSteps(g, 500)
        r = zs.change_z_steps(ser, 500)
        self.assertTrue(r["ok"])
        self.assertEqual((r["before"][102], r["after"][102]), (100.0, 500.0))
        self.assertEqual(g.got, [b"?", b"$$\n", b"$102=500.000\n", b"$$\n"])

    def test_refuses_other_commands_and_values(self):
        ser = zs.OnlyZSteps(FakeGrbl(), 500)
        for bad in (b"$103=500.000\n", b"$102=600.000\n", b"$J=G21 G90 G53 Z-1.000 F100\n", b"$H\n", b"$X\n"):
            with self.assertRaises(RuntimeError):
                ser.write(bad)
        for v in (100, 299.9, 800.1):
            with self.assertRaises(ValueError):
                zs.write_command(v)

    def test_y_axis_writes_only_101(self):
        g = FakeGrbl()
        r = zs.change_z_steps(zs.OnlyZSteps(g, 409.6, "Y"), 409.6, "Y")
        self.assertTrue(r["ok"])
        self.assertEqual((g.settings[101], g.settings[100], g.settings[102]), (409.6, 400.0, 100.0))
        with self.assertRaises(RuntimeError):
            zs.OnlyZSteps(FakeGrbl(), 409.6, "Y").write(b"$102=409.600\n")
        with self.assertRaises(ValueError):
            zs.write_command(400, "A")

    def test_nothing_written_while_moving(self):
        g = FakeGrbl(state="Jog")
        with self.assertRaisesRegex(RuntimeError, "nothing written"):
            zs.change_z_steps(zs.OnlyZSteps(g, 500), 500)
        self.assertNotIn(b"$102=500.000\n", g.got)


if __name__ == "__main__":
    unittest.main()
