"""Studio parks the head at the home corner after a run that FINISHED - never after a stopped or failed run (those
need homing first) (user 2026-10-28: "after every work finishing machine should move to home position")."""
import os
import sys
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class ParkAfterRunTests(unittest.TestCase):
    def setUp(self):
        import webui_server_v29 as v29
        self.v29 = v29
        for name in ("_record_run", "_log"):
            p = patch.object(v29, name)
            p.start()
            self.addCleanup(p.stop)

    def run_end(self, error, state):
        v29, parks = self.v29, []
        with patch.object(v29.machine, "status", return_value={"busy": None, "homed": True, "mpos": [65.7, 150, -0.5]}), \
                patch.object(v29.machine, "start_park", side_effect=lambda on_done=None: parks.append(1)
                             or on_done(None), create=True):
            v29._record_run_and_park("j", "r", error, {"program": {"state": state}})
            end = time.monotonic() + 2
            while not parks and time.monotonic() < end:
                time.sleep(0.02)
            time.sleep(0.1)
        return parks

    def test_a_finished_run_parks(self):
        self.assertEqual(self.run_end(None, "finished"), [1])

    def test_a_stopped_or_failed_run_does_not(self):
        self.assertEqual(self.run_end("STOP requested; home again", "stopped"), [])
        self.assertEqual(self.run_end("No acknowledgment", "failed"), [])

    def test_it_is_installed_when_studio_starts(self):
        v29 = self.v29
        saved = v29.v14._record_run
        try:
            v29.install_park_after_run()
            self.assertIs(v29.v14._record_run, v29._record_run_and_park)
        finally:
            v29.v14._record_run = saved
        with open(v29.__file__, encoding="utf-8") as f:
            self.assertIn("    install_park_after_run()", f.read())              # called in main()


if __name__ == "__main__":
    unittest.main()
