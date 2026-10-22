"""Route the first travel move round tall skin (Studio v29, user 2026-10-22: "build step 2"): the lowest of straight /
X-first / Y-first is chosen (straight unless 1 mm lower), the path check measures its legs, step 23's program gets
the waypoints at travel Z, the re-read / paste check / drop slowing accept consecutive sideways moves ONLY at travel
Z, and a run is refused when its program's route is not the one just checked."""
import json
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import dispense, dispense_check, travel_route  # noqa: E402
from henna_preview import path_clearance as pc  # noqa: E402
from henna_preview.program_reread_v2 import ProgramReadError, reread_v2  # noqa: E402

TRAVEL = -1.0


def surface(tall_box):
    """A 1 mm grid over X 40-200, Y 40-180 at Z -60, with a tall block (Z -2) where tall_box says."""
    xs, ys = np.meshgrid(np.arange(40, 200, 1.0), np.arange(40, 180, 1.0))
    xy = np.column_stack([xs.ravel(), ys.ravel()])
    z = np.full(len(xy), -60.0)
    x0, x1, y0, y1 = tall_box
    z[(xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)] = -2.0
    return xy, z


def program(approach=()):
    lines = ["(test)", "G21", "G90", f"G53 G0 Z{TRAVEL:.3f}"]
    lines += [f"G53 G0 X{x:.3f} Y{y:.3f}" for x, y in approach]
    lines += ["G53 G0 X120.000 Y120.000", "G53 G1 X120.000 Y120.000 Z-30.000 F300", "G53 G1 X125.000 Y120.000 Z-30.000 F300",
              "G53 G0 Z-25.000", "G53 G0 X130.000 Y125.000", "G53 G1 X130.000 Y125.000 Z-30.000 F300",
              "G53 G1 X135.000 Y125.000 Z-30.000 F300", f"G53 G0 Z{TRAVEL:.3f}"]
    return lines


class ChooseTests(unittest.TestCase):
    START, TARGET = (65.7, 80.0), (120.0, 120.0)

    def test_round_a_tall_block_on_the_straight_line(self):
        xy, z = surface((85, 100, 88, 105))                     # on the straight line, not on either L-route
        w, top, rated = travel_route.choose(self.START, self.TARGET, xy, z, 8.0)
        self.assertNotEqual(w, [])
        self.assertAlmostEqual(top, -60.0)
        self.assertAlmostEqual(rated[0][1], -2.0)                # the straight line saw the block

    def test_straight_when_clear_or_not_clearly_better(self):
        xy, z = surface((180, 190, 170, 175))                   # nowhere near
        self.assertEqual(travel_route.choose(self.START, self.TARGET, xy, z, 8.0)[0], [])
        z2 = z.copy()
        z2[:] = -60.0
        z2[(xy[:, 0] > 90) & (xy[:, 0] < 95)] = -59.5          # everything about the same: straight stays
        self.assertEqual(travel_route.choose(self.START, self.TARGET, xy, z2, 8.0)[0], [])

    def test_segments_and_program_approach(self):
        segs = pc.travel_segments([(65.7, 80)], [((120, 120), (125, 120)), ((130, 125), (135, 125))], [(120.0, 80.0)])
        self.assertEqual(segs, [((65.7, 80.0), (120.0, 80.0)), ((120.0, 80.0), (120.0, 120.0)),
                                ((125.0, 120.0), (130.0, 125.0))])
        self.assertEqual(travel_route.program_approach(program([(120.0, 80.0)]), TRAVEL), [(120.0, 80.0)])
        self.assertEqual(travel_route.program_approach(program(), TRAVEL), [])


class ProgramRuleTests(unittest.TestCase):
    def test_reread_accepts_a_route_at_travel_z_only(self):
        rr = reread_v2("\n".join(program([(120.0, 80.0)])), TRAVEL, -45.0, 300.0)
        self.assertEqual(rr["strokes"], 2)                       # the waypoint is not a stroke
        bad = program()
        bad.insert(bad.index("G53 G0 X130.000 Y125.000") + 1, "G53 G0 X140.000 Y125.000")   # two moves at a hop
        with self.assertRaisesRegex(ProgramReadError, "not right after rising"):
            reread_v2("\n".join(bad), TRAVEL, -45.0, 300.0)

    def test_paste_counts_strokes_not_waypoints(self):
        lines = [ln for ln in program([(120.0, 80.0)]) if not ln.startswith("(")]
        cfg = dispense.settings("pneumatic", 0.3, 0.2)
        paste = dispense.add(lines, cfg)
        chk = dispense_check.check(paste, lines, cfg, TRAVEL, dispense.S_FULL)
        self.assertEqual((chk["strokes"], chk["paste_on"]), (2, 2))

    def test_slow_drops_keeps_the_route(self):
        import webui_server_v17 as v17
        text, _ = v17.slow_drops("\n".join(program([(120.0, 80.0)])) + "\n", 300)
        self.assertIn("G53 G0 X120.000 Y80.000\nG53 G0 X120.000 Y120.000", text)
        low = program()
        low.insert(low.index("G53 G0 X130.000 Y125.000") + 1, "G53 G0 X140.000 Y125.000")
        with self.assertRaisesRegex(ValueError, "not followed by a drop"):
            v17.slow_drops("\n".join(low) + "\n", 300)


class StepRunnerTests(unittest.TestCase):
    def test_waypoints_written_after_the_first_rise_for_the_same_design(self):
        import step_path_clearance as spc
        cfg = {"hand_capture": {"recording": "r.db3"}}
        env = {"HENNA_APPROACH": json.dumps({"recording": "r.db3", "target": [120.0, 120.0], "waypoints": [[120.0, 80.0]]})}
        info = spc.approach(cfg, env)
        self.assertIsNotNone(info)
        self.assertIsNone(spc.approach({"hand_capture": {"recording": "other.db3"}}, env))
        base = lambda lifted, hops, p, header: ["G21", "G90", "G53 G0 Z-1.000", "G53 G0 X120.000 Y120.000", "G53 G0 Z-1.000"]  # noqa: E731
        lifted = [np.array([[120.0, 120.0, -30.0]])]
        out = spc.wrap_program(base, info)(lifted, [], {"travel_z": -1.0}, [])
        self.assertEqual(out[2:5], ["G53 G0 Z-1.000", "G53 G0 X120.000 Y80.000", "G53 G0 X120.000 Y120.000"])
        other = [np.array([[110.0, 120.0, -30.0]])]                # a different design: no waypoints
        self.assertEqual(spc.wrap_program(base, info)(other, [], {"travel_z": -1.0}, []), base(other, [], {}, []))


class RunCheckTests(unittest.TestCase):
    def test_a_run_needs_the_route_that_was_just_checked(self):
        import webui_server_v29 as v29
        job = {"id": "20261022-140000-job"}
        saved = (v29.v27.clearance, dict(v29._LAST_APPROACH), v29.v10.cal)
        v29.v10.cal = lambda: {"travel_z": TRAVEL}
        try:
            v29.v27.clearance = v29.path_clearance
            v29._LAST_APPROACH[job["id"]] = [(120.0, 80.0)]
            v29._route_check(job, program([(120.0, 80.0)]))                  # same route: fine
            with self.assertRaisesRegex(ValueError, "not the one checked now"):
                v29._route_check(job, program())
            v29._LAST_APPROACH[job["id"]] = []
            v29._route_check(job, program())
            v29.v27.clearance = saved[0] if saved[0] is not v29.path_clearance else v29._WHOLE_HAND_CLEARANCE
            with self.assertRaisesRegex(ValueError, "only the path check can approve"):
                v29._route_check(job, program([(120.0, 80.0)]))
        finally:
            v29.v27.clearance, v29.v10.cal = saved[0], saved[2]
            v29._LAST_APPROACH.clear()
            v29._LAST_APPROACH.update(saved[1])


if __name__ == "__main__":
    unittest.main()
