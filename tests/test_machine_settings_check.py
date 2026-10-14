"""Machine settings tool: only ?, $I and $$ can reach the controller; settings parsing and findings (no hardware)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import machine_settings_check as msc  # noqa: E402


class _Port:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)
        return len(data)

    def close(self):
        pass


class OnlyQueriesTests(unittest.TestCase):
    def test_only_the_three_queries_reach_the_port(self):
        p = _Port()
        w = msc.OnlyQueries(p)
        for ok in (b"?", b"$I\n", b"$$\n"):
            w.write(ok)
        for bad in (b"$H\n", b"$HZ\n", b"$X\n", b"!", b"\x18", b"$100=80\n", b"$J=G91 X1 F100\n", b"G0 X10\n",
                    b"$$", b"$RST=*\n", b"??"):
            with self.assertRaises(RuntimeError):
                w.write(bad)
        self.assertEqual(p.sent, [b"?", b"$I\n", b"$$\n"])
        w.close()


class ParseTests(unittest.TestCase):
    LINES = ["$0=10", "$3=2", "$13=0", "$20=0", "$21=0", "$22=1", "$23=3", "$27=1.000", "$32=1",
             "$100=80.000", "$101=80.000", "$102=400.000", "$110=6000", "$111=6000", "$112=1500",
             "$120=200", "$121=200", "$122=100", "$130=300.000", "$131=200.000", "$132=80.000",
             "$Axes/X/StepsPerMm=80", "ok"]

    def test_parse_and_summary(self):
        num, named = msc.parse_settings(self.LINES)
        self.assertEqual(num[102], 400.0)
        self.assertEqual(named["Axes/X/StepsPerMm"], "80")
        s = msc.summarize(num)
        self.assertEqual(s["max_travel_mm"], {"X": 300.0, "Y": 200.0, "Z": 80.0})
        self.assertEqual(s["homing"]["direction_inverted_axes"], ["X", "Y"])
        self.assertEqual(s["direction_inverted_axes"], ["Y"])
        self.assertTrue(s["homing"]["enabled"])
        self.assertFalse(s["soft_limits"])
        n = " ".join(msc.notes(s))
        self.assertIn("X 300 x Y 200 mm", n)
        self.assertIn("soft limits are off", n)
        self.assertIn("laser mode is on", n)

    def test_missing_settings_are_none_and_bad_ones_flagged(self):
        s = msc.summarize({22: 0, 13: 1})
        self.assertIsNone(s["steps_per_mm"]["X"])
        n = " ".join(msc.notes(s))
        self.assertIn("HOMING IS OFF", n)
        self.assertIn("INCHES", n)


if __name__ == "__main__":
    unittest.main()
