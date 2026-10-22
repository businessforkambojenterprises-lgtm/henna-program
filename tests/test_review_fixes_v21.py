"""Second review fixes (v21): each test reproduces a Codex finding on v20 and shows v21 fixes it. No hardware."""
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import font_strokes as fs  # noqa: E402
from henna_preview import grbl_program_link_v20 as v20link  # noqa: E402
from henna_preview import grbl_program_link_v21 as mod  # noqa: E402
from henna_preview import name_files_v21 as names  # noqa: E402
from tests.test_grbl_program_link import lines_and_end, wait_until  # noqa: E402
from tests.test_review_fixes_v20 import Base  # noqa: E402
import webui_server_v21 as server  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_pristine  # noqa: E402

studio_pristine.remember()           # the older modules as loaded, before any v24+ test imports a later Studio


def setUpModule():
    global _PRISTINE
    _PRISTINE = studio_pristine.apply()


def tearDownModule():
    studio_pristine.undo(_PRISTINE)

FONTS = {f["id"] for f in fs.list_fonts()}


class StopWarningNeedsHome(Base):
    def unconfirmed_stop(self, cls):
        link, fake = self.make(cls, capacity=4, move_s=0.3)
        self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        fake.fail_writes = {b"!", b"\x18"}
        with self.assertRaises(mod.MachineError):
            link.stop()
        self.assertTrue(link.status()["stop_unconfirmed"])
        return link, fake

    def test_v20_connect_hid_the_warning(self):              # finding 1, as found
        link, fake = self.unconfirmed_stop(v20link.GrblProgramLinkV20)
        link.connect("FAKE")
        self.assertFalse(link.status()["stop_unconfirmed"])
        self.assertEqual(fake.state, "Run")                   # ... while the machine was still moving

    def test_v21_warning_stays_until_home(self):
        link, fake = self.unconfirmed_stop(mod.GrblProgramLinkV21)
        link.connect("FAKE")                                  # Connect on the open connection
        self.assertTrue(link.status()["stop_unconfirmed"])
        self.assertTrue(wait_until(lambda: not fake.planner and not fake.waiting and fake.state == "Idle", 10))
        link.disconnect()
        fake.closed = False                                   # the same fake controller, opened again
        link.connect("FAKE", wait_s=1.0)                      # a new connection does not clear it either
        self.assertTrue(link.status()["stop_unconfirmed"])
        self.home(link)
        self.assertFalse(link.status()["stop_unconfirmed"])  # only a successful Home does


def delayed(cls, gate):
    """The first run's worker waits at the gate before recording its result (a slow thread)."""
    class Delayed(cls):
        def _start_worker_v20(self, owner, action, finalize, on_done):
            first = not getattr(self, "_delayed_once", False)
            self._delayed_once = True

            def slow(error):
                if first:
                    gate.wait(10)
                finalize(error)
            return super()._start_worker_v20(owner, action, slow, on_done)
    return Delayed


class RecordOwnRun(Base):
    def stop_home_start_second(self, cls):
        gate = threading.Event()
        link, fake = self.make(delayed(cls, gate), capacity=4, move_s=0.05)
        result, done = self.start(link, "FIRST.gcode")
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        link.stop()
        self.home(link)
        send, end = lines_and_end()
        link.start_program("SECOND.gcode", send, end)
        self.assertTrue(wait_until(lambda: (link.status()["program"] or {}).get("done", 0) >= 2))
        gate.set()
        self.assertTrue(done.wait(5))
        return link, result["args"]

    def test_v20_recorded_the_next_run(self):                # finding 2, as found
        link, (error, snap) = self.stop_home_start_second(v20link.GrblProgramLinkV20)
        self.assertEqual((snap["program"]["name"], snap["program"]["state"]), ("SECOND.gcode", "running"))

    def test_v21_records_its_own_run(self):
        link, (error, snap) = self.stop_home_start_second(mod.GrblProgramLinkV21)
        prog = snap["program"]
        self.assertEqual((prog["name"], prog["state"]), ("FIRST.gcode", "stopped"))
        self.assertGreaterEqual(prog["done"], 4)
        self.assertIsNotNone(error)
        self.assertIsNotNone(snap["mpos"])                    # its own last position before STOP
        self.assertEqual(link.status()["program"]["name"], "SECOND.gcode")
        self.assertTrue(wait_until(lambda: link.status()["program"]["state"] == "finished", 10))

    def test_v21_normal_finish_record(self):
        link, fake = self.make(mod.GrblProgramLinkV21, capacity=4, move_s=0.02)
        send, end = lines_and_end()
        result, done = self.start(link, "ONE.gcode")
        self.assertTrue(done.wait(5))
        error, snap = result["args"]
        self.assertIsNone(error)
        self.assertEqual((snap["program"]["state"], snap["program"]["done"]), ("finished", len(send)))
        self.assertEqual([round(v, 3) for v in snap["mpos"]], [round(v, 3) for v in end])


class NameFiles(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch.object(server.v11, "UPLOAD_DIR", tmp.name))
        self.dir = tmp.name

    def test_upload_stem_matches_v11(self):
        for name in ("Name_ABCDEFGHIJKLMNOP_arial-rounded-mt-bold", "a b/c.svg", "***", "Name_HARSH"):
            with self.subTest(name=name):
                with patch.object(server.v11, "read_svg", return_value={"strokes": []}):
                    with patch.object(server.v11, "design_entry", side_effect=lambda p: p):
                        saved = server.v11.design_upload({"filename": name, "svg": "<svg/>"})
                self.assertEqual(os.path.basename(saved["design"]), names.upload_stem(name) + ".svg")
                os.remove(os.path.join(self.dir, names.upload_stem(name) + ".svg"))

    def test_find_same_uses_saved_name_and_numbered_copies(self):
        stem = "Name_ABCDEFGHIJKLMNOP_arial-rounded-mt-bold"
        saved = names.upload_stem(stem)
        for fname, text in ((saved + ".svg", "other"), (saved + "-2.svg", "mine"), (saved + "x.svg", "mine")):
            with open(os.path.join(self.dir, fname), "w", encoding="utf-8") as f:
                f.write(text)
        self.assertEqual(os.path.basename(names.find_same(self.dir, stem, "mine")), saved + "-2.svg")
        self.assertIsNone(names.find_same(self.dir, stem, "new"))

    @unittest.skipUnless("segoe-script" in FONTS, "Segoe Script is not installed on this PC")
    def test_ampersand_name(self):                           # finding 3
        with self.assertRaises(ET.ParseError):
            ET.fromstring(fs.font_name_svg("A&B", "segoe-script").encode("utf-8"))   # v19/v20 file was broken
        svg = names.font_name_svg("A&B", "segoe-script")
        self.assertEqual(ET.fromstring(svg.encode("utf-8")).find("{http://www.w3.org/2000/svg}title").text,
                         "Name: A&B (Segoe Script)")
        self.assertEqual(svg.split("<g", 1)[1], fs.font_name_svg("A&B", "segoe-script").split("<g", 1)[1])
        d = server.name_design({"text": "A&B", "font": "segoe-script"})["design"]
        self.assertTrue(d["usable"])

    @unittest.skipUnless("arial-rounded-mt-bold" in FONTS, "Arial Rounded MT Bold is not installed on this PC")
    def test_long_name_saved_once(self):                     # finding 4
        params = {"text": "ABCDEFGHIJKLMNOP", "font": "arial-rounded-mt-bold"}
        first = server.name_design(params)["design"]["svg"]
        self.assertEqual(server.name_design(params)["design"]["svg"], first)
        self.assertEqual(len(os.listdir(self.dir)), 1)

    def test_studio_capitals_and_route(self):
        server.name_design({"text": "harsh"})
        server.name_design({"text": "Harsh", "font": "studio"})
        self.assertEqual(os.listdir(self.dir), ["Name_HARSH.svg"])
        self.assertIs(server.Handler.POST_API["/api/studio/name_design"][0], server.name_design)
        self.assertEqual(server.Handler.server_version, "HennaStudio/0.21")
        self.assertIs(server.v20.machine, server.machine)
        self.assertIs(server.v14.machine, server.machine)


if __name__ == "__main__":
    unittest.main()
