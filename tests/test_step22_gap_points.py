"""Step 22 gap-check points: highest skin, lowest skin and a third point away from both, taken from the program path."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from step22_design_hover_test import gap_points


class GapPoints(unittest.TestCase):
    def test_picks_extremes_and_a_spread_point(self):
        # stroke columns: x, y, raw skin z, nozzle z, floor z
        a = np.array([[0, 0, -60, -57, -59], [10, 0, -65, -62, -64], [5, 8, -62, -59, -61]], float)
        b = np.array([[20, 20, -63, -60, -62]], float)
        g = gap_points([a, b])
        self.assertEqual([(p["x"], p["y"]) for p in g], [(0, 0), (10, 0), (20, 20)])
        self.assertEqual(g[0]["program_z"], -57.0)
        self.assertEqual(g[1]["camera_skin_z"], -65.0)


if __name__ == "__main__":
    unittest.main()
