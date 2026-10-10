"""Build preflight refuses incomplete source checkouts before writing a snapshot."""
import hashlib
from pathlib import Path
import tempfile
import unittest

import build_windows as build


class BuildInputs(unittest.TestCase):
    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.project = Path(self.temp)
        for relative in ('webui_server_v13.py', 'webui/studio_v13.html',
                         'henna_preview/grbl_scan_link.py', 'models/hand_landmarker.task'):
            path = self.project / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'fixture')
        self.model = self.project / 'models/hand_landmarker.task'
        digest = hashlib.sha256(self.model.read_bytes()).hexdigest()
        (self.project / 'models/MODEL_INFO.txt').write_text('SHA-256: ' + digest)

    def test_documented_model_and_entry_points_accepted(self):
        build.verify_build_inputs(self.project)

    def test_missing_model_refused_before_snapshot_created(self):
        self.model.unlink()
        target = self.project / 'snapshot'
        with self.assertRaisesRegex(ValueError, 'missing.*hand_landmarker'):
            build.prepare_app(self.project, target)
        self.assertFalse(target.exists())

    def test_changed_model_refused_before_snapshot_created(self):
        self.model.write_bytes(b'different')
        target = self.project / 'snapshot'
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            build.prepare_app(self.project, target)
        self.assertFalse(target.exists())

    def test_missing_v13_entry_refused(self):
        (self.project / 'webui_server_v13.py').unlink()
        with self.assertRaisesRegex(ValueError, 'missing.*v13'):
            build.verify_build_inputs(self.project)


if __name__ == '__main__':
    unittest.main()
