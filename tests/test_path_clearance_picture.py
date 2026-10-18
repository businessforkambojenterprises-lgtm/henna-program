"""path_clearance_picture.py (user 2026-10-18: the real-hand path check kept refusing at the same spot): the newest
job with a region is picked, and the tall spots are listed highest first and kept apart."""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import path_clearance_picture as pcp  # noqa: E402


class PictureHelperTests(unittest.TestCase):
    def test_newest_job_with_region(self):
        with tempfile.TemporaryDirectory() as d:
            for name, steps in (("20261018-080000-job", {"scan": {"recording": "a.db3"}, "region": {"id": 1}}),
                                ("20261018-090000-job", {"scan": {"recording": "a.db3"}}),
                                ("20261017-090000-job", {"scan": {"recording": "a.db3"}, "region": {"id": 2}})):
                os.makedirs(os.path.join(d, name))
                with open(os.path.join(d, name, "job.json"), "w", encoding="utf-8") as f:
                    json.dump({"steps": steps}, f)
            self.assertEqual(pcp.newest_job_with_region(d), "20261018-080000-job")
            self.assertIsNone(pcp.newest_job_with_region(os.path.join(d, "none")))

    def test_tall_spots_highest_first_and_apart(self):
        xy = np.array([[0, 0], [2, 0], [30, 0], [60, 0], [61, 0]], float)
        z = np.array([-10, -9, -20, -15, -30], float)
        spots = pcp.tall_spots(xy, z, np.ones(5, bool), n=3)
        self.assertEqual(spots, [(2.0, 0.0, -9.0), (60.0, 0.0, -15.0), (30.0, 0.0, -20.0)])
        self.assertEqual(pcp.tall_spots(xy, z, np.zeros(5, bool)), [])


if __name__ == "__main__":
    unittest.main()
