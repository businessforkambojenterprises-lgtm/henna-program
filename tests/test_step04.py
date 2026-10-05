"""Step 4 checks. Standard library unittest; MediaPipe test is skipped if the model is absent."""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.depth_surface import raw_to_mm
from henna_preview.hand_detect import MODEL_PATH, ModelError, measured_depth_at, verify_model
from henna_preview.recording import (RecordingError, check_frame_index, count_frames_db3,
                                     read_frame, require_streams)


class FrameIndex(unittest.TestCase):
    def test_valid_range(self):
        check_frame_index(0, 431)
        check_frame_index(430, 431)

    def test_out_of_range_rejected(self):
        for bad in (-1, 431, 1000):
            with self.assertRaises(RecordingError):
                check_frame_index(bad, 431)

    def test_non_integer_rejected(self):
        for bad in (1.5, "3", True, None):
            with self.assertRaises(RecordingError):
                check_frame_index(bad, 431)


class Streams(unittest.TestCase):
    def test_missing_colour_rejected(self):
        with self.assertRaises(RecordingError):
            require_streams(["Depth"])

    def test_missing_depth_rejected(self):
        with self.assertRaises(RecordingError):
            require_streams(["Color", "Infrared"])

    def test_both_present_ok(self):
        require_streams(["Depth", "Color"])

    def test_db3_count_reads_streams(self):
        tmp = tempfile.mkdtemp()
        try:
            p = os.path.join(tmp, "fake.db3")
            con = sqlite3.connect(p)
            con.execute("create table topics(id integer, name text, type text)")
            con.execute("create table messages(id integer, topic_id integer, timestamp integer, data blob)")
            con.execute("insert into topics values (1,'/device_0/sensor_0/Depth_0/image/data','x')")
            con.executemany("insert into messages values (?,1,?,x'')", [(i, i) for i in range(5)])
            con.commit()
            con.close()
            counts = count_frames_db3(p)
            self.assertEqual(counts, {"Depth": 5})
            with self.assertRaises(RecordingError):
                require_streams(list(counts))  # depth-only recording is refused
        finally:
            shutil.rmtree(tmp)

    def test_wrong_file_type_rejected(self):
        with self.assertRaises(RecordingError):
            read_frame(__file__, 0)


class UnitsAndDepth(unittest.TestCase):
    def test_d405_depth_scale(self):
        # D405 recording: 0.0001 m per unit -> 2437 units = 243.7 mm
        self.assertAlmostEqual(float(raw_to_mm(2437, 1e-4)), 243.7, places=6)

    def test_landmark_on_missing_depth_is_none(self):
        d = np.full((4, 4), 250.0)
        d[1, 2] = np.nan
        z = measured_depth_at([[2, 1], [0, 0], [10, 10]], d)
        self.assertEqual(z, [None, 250.0, None])


class Model(unittest.TestCase):
    def test_wrong_checksum_rejected(self):
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".task")
        tmp.write(b"not a model")
        tmp.close()
        try:
            with self.assertRaises(ModelError):
                verify_model(tmp.name)
        finally:
            os.unlink(tmp.name)

    @unittest.skipUnless(os.path.isfile(MODEL_PATH), "model not downloaded")
    def test_blank_image_finds_no_hand(self):
        try:
            import mediapipe  # noqa: F401
        except ImportError:
            self.skipTest("mediapipe not installed in this Python")
        from henna_preview.hand_detect import detect_hands
        verify_model()
        self.assertEqual(detect_hands(np.full((480, 640, 3), 200, np.uint8)), [])


if __name__ == "__main__":
    unittest.main()
