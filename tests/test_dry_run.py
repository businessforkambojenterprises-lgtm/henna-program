"""Step 15 checks: the dry-run program stays inside every user limit and only moves."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.dry_run import DryRunError, independent_check, lift_strokes, program

P = {"lift_mm": 40.0, "travel_z": -5.0, "floor_z": -35.0, "draw_feed_mm_min": 300.0, "travel_feed_mm_min": 1000.0}
STROKE = np.array([[57.0, 37.0, -70.0, -67.0, -69.0], [60.0, 40.0, -60.0, -57.0, -59.0], [62.0, 45.0, -57.0, -54.0, -56.0]])


def build(p=P, s=STROKE):
    lifted = lift_strokes([s], p)
    return lifted, program(lifted, p, ["DRY RUN - NO HAND - NO DISPENSING"])


class DryRun(unittest.TestCase):
    def test_lift_and_limits(self):
        lifted, lines = build()
        np.testing.assert_allclose(lifted[0][:, 2], [-27.0, -17.0, -14.0])
        moves = independent_check(lines, P)
        self.assertEqual(sum(m["kind"] == "draw" for m in moves), 3)

    def test_only_motion_commands(self):
        _, lines = build()
        body = [l for l in lines if not l.startswith("(")]
        for l in body:
            self.assertRegex(l, r"^(G21|G90|G53 G[01] .*)$")
        self.assertFalse(any(t in " ".join(body) for t in ("M3", "M5", "M8", "S", "$", "G92", "G38")))

    def test_floor_violation_refused(self):
        with self.assertRaises(DryRunError):
            build(dict(P, lift_mm=20.0))  # lifted min -47 < floor -35

    def test_travel_not_above_path_refused(self):
        with self.assertRaises(DryRunError):
            build(dict(P, travel_z=-20.0))

    def test_bad_order_refused(self):
        with self.assertRaises(DryRunError):
            build(dict(P, floor_z=-1.0))  # floor above travel

    def test_nested_or_unsafe_comment_refused(self):
        _, lines = build()
        for bad in ["(Home ($H) first)", "(note $X)", "(a; b)"]:
            with self.assertRaises(DryRunError):
                independent_check([bad] + lines, P)

    def test_tampering_caught(self):
        _, lines = build()
        bad = [l.replace("Z-27.000", "Z-40.000") for l in lines]
        with self.assertRaises(DryRunError):
            independent_check(bad, P)
        with self.assertRaises(DryRunError):
            independent_check(lines + ["M3 S100"], P)
        with self.assertRaises(DryRunError):
            independent_check([l.replace("G53 G0 Z-5.000", "G53 G0 Z-30.000", 1) for l in lines], P)


if __name__ == "__main__":
    unittest.main()
