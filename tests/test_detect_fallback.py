"""Hand detection tries the model's confidence 0.5 first and 0.3 second (user 2026-10-31, scan Z -170: a clear hand that
fills the picture was "not found" at 0.5 and found at 0.3 on the real recording)."""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import webui_server_v11 as v11  # noqa: E402

HAND = {"hand_detected": True, "hands": [{"score": 0.6, "landmarks": [{"measured_depth_mm": 100.0}]}]}
NONE = {"hand_detected": False, "hands": []}


class DetectFallbackTests(unittest.TestCase):
    def run_detect(self, by_conf):
        asked = []

        def step04(params):
            asked.append(params["min_confidence"])
            return {"run_id": "r1", "report": by_conf[params["min_confidence"]]}
        job = {"id": "j", "steps": {"scan": {"recording": "x.db3", "frame_pairs": 100}}}
        with patch.object(v11, "job_load", return_value=job), patch.object(v11.v2, "run_step04", side_effect=step04), \
                patch.object(v11, "set_step"), patch.object(v11, "hand_payload", return_value={}):
            return v11.detect({"job": "j"}), asked

    def test_found_at_0_5_asks_once(self):
        _, asked = self.run_detect({0.5: HAND, 0.3: HAND})
        self.assertEqual(asked, [0.5])

    def test_found_only_at_0_3(self):
        out, asked = self.run_detect({0.5: NONE, 0.3: HAND})
        self.assertEqual(asked, [0.5, 0.3])
        self.assertEqual(out["job"]["id"], "j")

    def test_found_at_neither_is_refused(self):
        with self.assertRaisesRegex(ValueError, "no hand found"):
            self.run_detect({0.5: NONE, 0.3: NONE})


if __name__ == "__main__":
    unittest.main()
