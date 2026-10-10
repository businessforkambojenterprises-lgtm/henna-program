"""Scan-position API guards. Fake machine only; no hardware is used."""
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

import webui_server_v13 as server

LINE = "<Idle|MPos:145.800,42.600,-40.000|FS:0,0>"


class StudioScanPosition(unittest.TestCase):
    def setUp(self):
        self.machine = self.enterContext(patch.object(server, "machine"))
        self.job = self.enterContext(patch.object(server.v11, "job_load", return_value={"subject": "person"}))
        self.enterContext(patch.object(server.v10.camera, "status", return_value={"state": "off"}))

    def test_confirmation_required_for_motion_and_homing(self):
        for params in ({}, {"confirm_clear": True}, {"confirm_estop": True},
                       {"confirm_clear": "true", "confirm_estop": True}):
            for operation in (server.scan_position, server.home):
                with self.subTest(operation=operation.__name__, params=params):
                    with self.assertRaises(ValueError):
                        operation(params)
        self.machine.start_scan_position.assert_not_called()
        self.machine.start_homing.assert_not_called()

    def test_real_hand_only_moves_when_area_confirmed_clear(self):
        params = {"job": "test-job", "confirm_clear": True, "confirm_estop": True,
                  "x": 0, "y": 0, "z": -100, "command": "M3"}
        server.scan_position(params)
        # Coordinates and commands from HTTP are never forwarded.
        self.machine.start_scan_position.assert_called_once_with()

    def test_invalid_job_prevents_motion(self):
        self.job.side_effect = ValueError("unknown job")
        with self.assertRaises(ValueError):
            server.scan_position({"confirm_clear": True, "confirm_estop": True})
        self.machine.start_scan_position.assert_not_called()

    def test_recording_prevents_home_and_scan_motion(self):
        with patch.object(server.v10.camera, "status", return_value={"state": "recording"}):
            for operation in (server.home, server.scan_position):
                with self.assertRaises(server.MachineError):
                    operation({"confirm_clear": True, "confirm_estop": True})
        self.machine.start_scan_position.assert_not_called()
        self.machine.start_homing.assert_not_called()

    def test_scan_uses_guarded_actual_status_and_releases_on_failure(self):
        events = []

        @contextmanager
        def guard():
            events.append("locked")
            try:
                yield LINE
            finally:
                events.append("released")

        self.machine.capture_guard.side_effect = guard
        with TemporaryDirectory() as folder, \
             patch.object(server.v10.v2, "APPROVED_RECORDING_DIRS", [folder]), \
             patch.object(server.v10, "log_row"), \
             patch.object(server.v10.camera, "record", side_effect=RuntimeError("recording failed")), \
             patch.object(server.v11, "set_step") as save:
            with self.assertRaises(RuntimeError):
                server.scan({"job": "test-job", "status_line": "forged", "seconds": 5})
            save.assert_not_called()
        self.assertEqual(events, ["locked", "released"])

    def test_movement_during_capture_rejects_file_before_publication(self):
        @contextmanager
        def guard():
            yield LINE
            raise server.MachineError("Position changed during recording")

        self.machine.capture_guard.side_effect = guard
        with TemporaryDirectory() as folder, \
             patch.object(server.v10.v2, "APPROVED_RECORDING_DIRS", [folder]), \
             patch.object(server.v10, "log_row"), \
             patch.object(server.v10, "new_dir") as new_dir, \
             patch.object(server.v11, "set_step") as save, \
             patch.object(server.v10.camera, "record", side_effect=lambda p, s: Path(p).write_bytes(b"test")):
            with self.assertRaises(server.MachineError):
                server.scan({"job": "test-job", "status_line": "forged", "seconds": 5})
            self.assertEqual(len(list(Path(folder).glob("*.db3.rejected"))), 1)
            self.assertEqual(list(Path(folder).glob("*.db3")), [])
            new_dir.assert_not_called()
            save.assert_not_called()

    def test_success_publishes_verified_actual_position(self):
        self.machine.capture_guard.return_value.__enter__.return_value = LINE
        info = {"frame_pairs": 150, "depth_units_m": .0001, "camera_settings": {}, "median_depth_mm": 150}
        with TemporaryDirectory() as folder, \
             patch.object(server.v10.v2, "APPROVED_RECORDING_DIRS", [folder]), \
             patch.object(server.v10, "log_row"), \
             patch.object(server.v10, "new_dir", return_value=folder), \
             patch.object(server.v11, "set_step") as save, \
             patch.object(server.v10.camera, "record", return_value=info):
            server.scan({"job": "test-job", "status_line": "forged", "seconds": 5})
            actual = save.call_args.args[2]
            self.assertEqual(actual["status_line"], LINE)
            self.assertEqual(list(actual["mpos"]), [145.8, 42.6, -40.0])
            self.assertEqual(actual["frame_pairs"], 150)


class StudioShutdown(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.busy = None
        self.machine = self.enterContext(patch.object(server, "machine"))
        self.machine.status.side_effect = lambda: {"busy": self.busy}
        self.machine.stop.side_effect = self.stop_motion
        self.machine.disconnect.side_effect = self.disconnect
        self.camera_stop = self.enterContext(patch.object(
            server.v10.camera, "stop", side_effect=lambda: self.events.append("camera_stop")))
        self.http = self.enterContext(patch.object(
            server.base.http.server, "ThreadingHTTPServer")).return_value
        self.http.serve_forever.side_effect = KeyboardInterrupt
        self.http.server_close.side_effect = lambda: self.events.append("drain")
        self.enterContext(patch("builtins.print"))

    def stop_motion(self):
        self.events.append("stop")
        self.busy = None

    def disconnect(self):
        self.events.append("disconnect")
        if self.busy:
            raise server.MachineError("Cannot disconnect while " + self.busy)

    def test_recording_finishes_before_disconnect(self):
        self.busy = "recording"

        def drain():
            self.assertIs(self.http.daemon_threads, False)
            self.events.append("recording_verified")
            self.busy = None  # The request has released capture_guard.

        self.http.server_close.side_effect = drain
        server.main()
        self.assertEqual(self.events, ["recording_verified", "disconnect", "camera_stop"])
        self.machine.stop.assert_not_called()

    def test_motion_is_stopped_before_drain_and_inflight_motion_after_it(self):
        self.busy = "homing"

        def drain():
            self.events.append("drain")
            self.busy = "scan_position"  # An accepted handler started its worker.

        self.http.server_close.side_effect = drain
        server.main()
        self.assertEqual(self.events, ["stop", "drain", "stop", "disconnect", "camera_stop"])

    def test_stop_failure_still_drains_and_releases_resources(self):
        self.busy = "homing"

        def fail_stop():
            self.stop_motion()
            raise server.MachineError("stop failed")

        self.machine.stop.side_effect = fail_stop
        with self.assertRaisesRegex(server.MachineError, "stop failed"):
            server.main()
        self.assertEqual(self.events, ["stop", "drain", "disconnect", "camera_stop"])

    def test_server_close_failure_still_releases_resources(self):
        self.http.server_close.side_effect = RuntimeError("close failed")
        with self.assertRaisesRegex(RuntimeError, "close failed"):
            server.main()
        self.assertEqual(self.events, ["disconnect", "camera_stop"])

    def test_disconnect_failure_still_stops_camera(self):
        self.machine.disconnect.side_effect = server.MachineError("disconnect failed")
        with self.assertRaisesRegex(server.MachineError, "disconnect failed"):
            server.main()
        self.assertEqual(self.events, ["drain", "camera_stop"])


if __name__ == "__main__":
    unittest.main()
