"""Get ready (user 2026-10-26: "Home with Z first ... this should be auto"): connect, home and move to the scan
position in one press - each only when needed, in that order, never without the no-hand and emergency-stop ticks."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class GetReadyTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        self.calls = []
        self.state = {"connected": False, "homed": False, "capture_ready": False, "busy": None}
        routes = {"/api/machine/connect": lambda p: self._do("connect", connected=True),
                  "/api/machine/home": lambda p: self._do("home", p, homed=True),
                  "/api/machine/scan_position": lambda p: self._do("scan_position", p, capture_ready=True)}
        ps = [patch.object(v29.v11, "job_load", return_value={"id": "20261026-160000-job", "subject": "realhand"}),
              patch.object(v29.machine, "status", side_effect=lambda: dict(self.state, mpos=[0, 0, 0])),
              patch.object(v29, "_route", side_effect=lambda path: routes[path]),
              patch.object(v29.v14, "machine_status", side_effect=lambda: dict(self.state)),
              patch.object(v29, "_log")]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.ticks = {"job": "20261026-160000-job", "confirm_clear": True, "confirm_estop": True}

    def _do(self, name, params=None, **change):
        if params is not None:
            self.assertTrue(params.get("confirm_clear") and params.get("confirm_estop"))
        self.calls.append(name)
        self.state.update(change)
        return dict(self.state)

    def test_everything_in_order_when_nothing_is_ready(self):
        out = self.v29.get_ready(self.ticks)
        self.assertEqual(self.calls, ["connect", "home", "scan_position"])
        self.assertEqual(out["done"], ["connected", "homed", "at the scan position"])

    def test_only_what_is_missing(self):
        self.state.update(connected=True, homed=True)
        self.v29.get_ready(self.ticks)
        self.assertEqual(self.calls, ["scan_position"])
        self.calls.clear()
        self.v29.get_ready(self.ticks)
        self.assertEqual(self.calls, [])

    def test_never_without_the_ticks(self):
        for bad in ({}, {"confirm_clear": True}, {"confirm_estop": True}):
            with self.assertRaisesRegex(ValueError, "no hand"):
                self.v29.get_ready(dict(bad, job="20261026-160000-job"))
        self.assertEqual(self.calls, [])

    def test_a_failed_homing_says_what_was_done(self):
        self.state.update(connected=True)
        with patch.object(self.v29, "_wait_homed", side_effect=self.v29.MachineError("homing did not finish")):
            with self.assertRaisesRegex(ValueError, r"Get ready stopped: homing did not finish \(done: nothing\)"):
                self.v29.get_ready(self.ticks)
        self.assertEqual(self.calls, ["home"])


if __name__ == "__main__":
    unittest.main()
