"""Web page v10 building blocks: '?' lines, recording option fix, program re-read, log rows."""
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.grbl_status import StatusLineError, check_pose, check_xy, parse_status
from henna_preview.program_reread import ProgramReadError, reread
from henna_preview.web_log import COLUMNS, append
from henna_preview.web_recorder import CameraError, fix_late_option_repeats

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StatusLines(unittest.TestCase):
    def test_parses_idle_mpos_with_prompt_and_ok(self):
        s = parse_status(">>> ?\n<Idle|MPos:145.800,42.599,-40.000|FS:0,0|Ov:100,100,100>\nok")
        self.assertEqual(s["mpos"], [145.8, 42.599, -40.0])
        self.assertTrue(check_pose(s["mpos"]))

    def test_refuses_moving_alarm_wpos_and_wrong_pose(self):
        with self.assertRaises(StatusLineError):
            parse_status("<Jog|MPos:145.592,42.538,-40.000|FS:0,0>")
        with self.assertRaises(StatusLineError):
            parse_status("<Alarm|MPos:0.000,0.000,0.000|FS:0,0>")
        with self.assertRaises(StatusLineError):
            parse_status("<Idle|WPos:1.000,2.000,3.000|FS:0,0>")
        with self.assertRaises(StatusLineError):
            check_pose([145.8, 42.6, -20.0])

    def test_reading_must_be_over_the_point(self):
        self.assertTrue(check_xy([92.072, 57.313, -56.0], 92.073, 57.313))
        with self.assertRaises(StatusLineError):
            check_xy([97.199, 77.627, -66.0], 92.073, 57.313)


def make_db(rows):
    """rows: (topic, t_ns, bytes)"""
    f = tempfile.NamedTemporaryFile(suffix=".db3", delete=False)
    f.close()
    con = sqlite3.connect(f.name)
    con.execute("create table topics(id integer primary key, name text, type text, serialization_format text, offered_qos_profiles text)")
    con.execute("create table messages(id integer primary key, topic_id integer, timestamp integer, data blob)")
    ids = {}
    for name, ts, data in rows:
        if name not in ids:
            ids[name] = con.execute("insert into topics(name, type, serialization_format, offered_qos_profiles) values (?, 's', 'cdr', '')",
                                    (name,)).lastrowid
        con.execute("insert into messages(topic_id, timestamp, data) values (?, ?, ?)", (ids[name], ts, data))
    con.commit()
    con.close()
    return f.name


class OptionFix(unittest.TestCase):
    EP = "/device_0/sensor_0/option/Error_Polling_Enabled/value"
    DU = "/device_0/sensor_0/option/Depth_Units/value"

    def test_removes_only_late_repeats(self):
        p = make_db([(self.DU, 100, b"0.000100"), (self.EP, 100, b"1.0"), ("/img", 150, b"x"),
                     (self.EP, 2_500_000_100, b"1.0"), (self.EP, 2_500_000_100, b"1.0")])
        removed = fix_late_option_repeats(p)
        self.assertEqual([r[0] for r in removed], ["Error_Polling_Enabled", "Error_Polling_Enabled"])
        con = sqlite3.connect(p)
        self.assertEqual(con.execute("select count(*) from messages").fetchone()[0], 3)
        con.close()

    def test_a_real_change_is_refused_and_kept(self):
        p = make_db([(self.DU, 100, b"0.000100"), (self.DU, 900, b"0.001000")])
        with self.assertRaises(CameraError):
            fix_late_option_repeats(p)
        con = sqlite3.connect(p)
        self.assertEqual(con.execute("select count(*) from messages").fetchone()[0], 2)
        con.close()


GOOD = """(HOVER TEST)
G21
G90
G53 G0 Z-20.000
G53 G0 X91.017 Y72.460
G53 G1 X91.017 Y72.460 Z-61.323 F300
G53 G1 X90.900 Y73.000 Z-61.400 F300
G53 G0 Z-20.000
"""


class Reread(unittest.TestCase):
    def test_good_program(self):
        r = reread(GOOD, -20.0, -62.0, 300)
        self.assertEqual((r["strokes"], r["draw_moves"], r["problems"]), (1, 2, 0))

    def test_each_rule(self):
        for bad in (GOOD.replace("Z-61.400", "Z-62.500"),          # below floor
                    GOOD.replace("F300\nG53 G0", "F900\nG53 G0"),   # feed
                    GOOD.replace("G53 G0 Z-20.000\nG53 G0 X", "G53 G0 X"),  # sideways rapid low
                    GOOD + "M3\n",                                   # forbidden command
                    GOOD.replace("(HOVER TEST)", "(HOVER (TEST))"),  # bracket in comment
                    GOOD.rsplit("G53 G0 Z-20.000", 1)[0]):           # does not end at travel Z
            with self.assertRaises(ProgramReadError):
                reread(bad, -20.0, -62.0, 300)

    def test_real_program_from_today(self):
        p = os.path.join(ROOT, "validation", "S2026-10-09-A", "results", "DESIGN_HOVER_TEST_PROSTHETIC_20261009-113027.gcode")
        with open(p, encoding="ascii") as f:
            r = reread(f.read(), -20.0, -66.3, 300)
        self.assertEqual((r["strokes"], r["draw_moves"]), (18, 306))


class LogRows(unittest.TestCase):
    def test_appends_a_quoted_row_with_the_same_columns(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
        f.write(",".join(COLUMNS) + "\nold,row")  # no newline at the end on purpose
        f.close()
        append(f.name, 1, "Scan", [145.8, 42.599, -40.0], expected="pose 1", measured="a, b", result="ok")
        with open(f.name, encoding="utf-8") as g:
            lines = g.read().splitlines()
        self.assertEqual(lines[1], "old,row")
        self.assertIn('"a, b"', lines[2])
        self.assertIn("145.800,42.599,-40.000", lines[2])


if __name__ == "__main__":
    unittest.main()
