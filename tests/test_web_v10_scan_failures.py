"""Failed Scan recordings stay out of the recording picker; no hardware is used."""
import datetime
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui_server_v10 as web
from henna_preview.recording import RecordingError
from henna_preview.web_recorder import CameraError


class ScanFailures(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = tmp.name
        self.path = Path(self.folder) / "20261009_160000.db3"
        self.params = {"status_line": "<Idle|MPos:145.800,42.600,-40.000|FS:0,0>", "seconds": 5}
        now = datetime.datetime(2026, 10, 9, 16, 0, 0)
        self.enterContext(patch.object(web.v2, "APPROVED_RECORDING_DIRS", [self.folder]))
        self.enterContext(patch.object(web, "cal", return_value={"capture_pose_mpos": [145.8, 42.6, -40]}))
        self.enterContext(patch.object(web.shutil, "disk_usage", return_value=type("Disk", (), {"free": 3_000_000_000})()))
        self.enterContext(patch.object(web.dt, "datetime")).now.return_value = now
        self.camera = self.enterContext(patch.object(web, "camera"))
        self.log = self.enterContext(patch.object(web, "log_row"))
        self.new_dir = self.enterContext(patch.object(web, "new_dir"))

    def test_all_recording_failures_are_quarantined_and_logged(self):
        for error in (CameraError("units"), RuntimeError("SDK playback"),
                      sqlite3.OperationalError("database"), RecordingError("missing frames"),
                      OSError("recording I/O")):
            with self.subTest(error=type(error).__name__):
                self.log.reset_mock()

                def fail(path, seconds):
                    Path(path).write_bytes(b"failed scan retained for inspection")
                    raise error

                self.camera.record.side_effect = fail
                with self.assertRaises(type(error)) as caught:
                    web.scan_record(self.params)
                self.assertIs(caught.exception, error)
                self.assertFalse(self.path.exists())
                rejected = Path(str(self.path) + ".rejected")
                self.assertEqual(rejected.read_bytes(), b"failed scan retained for inspection")
                self.assertEqual(web.v2.list_recordings(), [])
                self.log.assert_called_once()
                self.assertEqual(self.log.call_args.args[7], "REFUSED")
                self.new_dir.assert_not_called()
                rejected.unlink()

    def test_failure_before_file_creation_is_logged(self):
        self.camera.record.side_effect = RuntimeError("camera could not start")
        with self.assertRaises(RuntimeError):
            web.scan_record(self.params)
        self.assertEqual(list(Path(self.folder).iterdir()), [])
        self.log.assert_called_once()
        self.new_dir.assert_not_called()

    def test_existing_recording_is_preserved(self):
        self.path.write_bytes(b"existing recording")
        with self.assertRaises(CameraError):
            web.scan_record(self.params)
        self.assertEqual(self.path.read_bytes(), b"existing recording")
        self.assertFalse(Path(str(self.path) + ".rejected").exists())
        self.camera.record.assert_not_called()
        self.log.assert_not_called()
        self.new_dir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
