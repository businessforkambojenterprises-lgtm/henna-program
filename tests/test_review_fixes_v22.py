"""Third review fix (v22): a late Home callback must not erase a newer failed-STOP warning. Fake controller only."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview import grbl_program_link_v21 as v21link  # noqa: E402
from henna_preview import grbl_program_link_v22 as mod  # noqa: E402
from tests.test_grbl_program_link import wait_until  # noqa: E402
from tests.test_review_fixes_v20 import Base  # noqa: E402
import webui_server_v22 as server  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import studio_pristine  # noqa: E402

studio_pristine.remember()           # the older modules as loaded, before any v24+ test imports a later Studio


def setUpModule():
    global _PRISTINE
    _PRISTINE = studio_pristine.apply()


def tearDownModule():
    studio_pristine.undo(_PRISTINE)


def late_home_callback(cls, gate):
    """Once armed, a Home's completion callback waits at the gate (after the machine was released)."""
    class Late(cls):
        armed = False

        def _start_worker(self, owner, action, on_done):
            if self.armed and self.busy == "homing":
                self.armed, original = False, on_done

                def late(error):
                    gate.wait(10)
                    if original:
                        original(error)
                on_done = late
            return super()._start_worker(owner, action, on_done)
    return Late


class LateHomeCallback(Base):
    def failed_stop_after_late_home(self, cls):
        gate = threading.Event()
        link, fake = self.make(late_home_callback(cls, gate), capacity=4, move_s=0.3)
        link.armed = True
        link.start_homing()                                   # its callback is held at the gate
        self.assertTrue(wait_until(lambda: link.status()["homed"] and not link.status()["busy"], 5))
        self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        fake.fail_writes = {b"!", b"\x18"}
        with self.assertRaises(mod.MachineError):
            link.stop()
        self.assertTrue(link.status()["stop_unconfirmed"])
        gate.set()                                            # the old Home callback runs now
        wait_until(lambda: False, 0.3)
        return link, fake

    def test_v21_late_home_erased_the_warning(self):         # the finding, as found
        link, fake = self.failed_stop_after_late_home(v21link.GrblProgramLinkV21)
        self.assertFalse(link.status()["stop_unconfirmed"])
        self.assertEqual(fake.state, "Run")

    def test_v22_warning_survives_a_late_home_callback(self):
        link, fake = self.failed_stop_after_late_home(mod.GrblProgramLinkV22)
        self.assertTrue(link.status()["stop_unconfirmed"])
        self.assertFalse(link.status()["homed"])
        self.assertTrue(wait_until(lambda: not fake.planner and not fake.waiting and fake.state == "Idle"
                                   and link.status()["state"] == "Idle", 10))
        self.home(link)                                       # only a new successful Home clears it
        self.assertFalse(link.status()["stop_unconfirmed"])

    def test_v22_failed_home_keeps_the_warning(self):
        link, fake = self.make(mod.GrblProgramLinkV22, capacity=4, move_s=0.3)
        self.start(link)
        self.assertTrue(wait_until(lambda: link.status()["program"]["done"] >= 4))
        fake.fail_writes = {b"!", b"\x18"}
        with self.assertRaises(mod.MachineError):
            link.stop()
        self.assertTrue(wait_until(lambda: not fake.planner and not fake.waiting and fake.state == "Idle"
                                   and link.status()["state"] == "Idle", 10))
        self.assertTrue(wait_until(lambda: link.status()["age_s"] is not None and link.status()["age_s"] < 0.3, 3))
        fake.fail_on = "$H"                                   # homing answered with an error
        done, result = threading.Event(), {}
        link.start_homing(on_done=lambda e: (result.setdefault("error", e), done.set()))
        self.assertTrue(done.wait(5))
        self.assertIsNotNone(result["error"])
        self.assertTrue(link.status()["stop_unconfirmed"])

    def test_server(self):
        self.assertIsInstance(server.machine, mod.GrblProgramLinkV22)
        for module in (server.v21, server.v20, server.v14, server.v14.v13, server.v16.v15):
            self.assertIs(module.machine, server.machine)
        self.assertEqual(server.Handler.server_version, "HennaStudio/0.22")
        self.assertIs(server.Handler.POST_API["/api/studio/name_design"][0], server.v21.name_design)


if __name__ == "__main__":
    unittest.main()
