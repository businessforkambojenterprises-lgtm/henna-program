"""Jumps between strokes round tall skin (user 2026-10-24: "make the pen's jumps go around the tall mound instead of
straight over it" -> "build the detour now"): a jump whose straight line crosses skin too tall for travel Z goes round
it at travel Z (shortest candidate that fits); every other jump stays straight with its low hop; the path check
measures the routed legs; step 23's program writes them at travel Z; the re-read accepts them; and a run is refused when
the program's jumps are not the ones just checked."""
import json
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from henna_preview import hop_path, travel_route  # noqa: E402
from henna_preview import path_clearance as pc  # noqa: E402
from henna_preview.program_reread_v2 import reread_v2  # noqa: E402

TRAVEL, LIMIT, MARGIN = -1.0, -10.0, 8.0


def surface(tall_boxes, base=-40.0):
    """A 1 mm grid over X 40-200, Y 40-180 at Z base, with tall mounds (Z -6) in the boxes."""
    xs, ys = np.meshgrid(np.arange(40, 200, 1.0), np.arange(40, 180, 1.0))
    xy = np.column_stack([xs.ravel(), ys.ravel()])
    z = np.full(len(xy), base)
    for x0, x1, y0, y1 in tall_boxes:
        z[(xy[:, 0] >= x0) & (xy[:, 0] <= x1) & (xy[:, 1] >= y0) & (xy[:, 1] <= y1)] = -6.0
    return xy, z


# two strokes: the first ends at (90, 110), the second starts at (150, 110); the heel mound sits between them
STROKES = [((80.0, 110.0), (90.0, 110.0)), ((150.0, 110.0), (160.0, 110.0))]
MOUND = (115, 125, 100, 120)


class ChooseGapTests(unittest.TestCase):
    def test_goes_round_a_mound_on_the_straight_jump(self):
        xy, z = surface([MOUND])
        w, top = travel_route.choose_gap(STROKES[0][1], STROKES[1][0], xy, z, MARGIN, LIMIT, bounds=(225, 176))
        self.assertNotEqual(w, [])
        self.assertLessEqual(top, LIMIT)
        segs = travel_route.legs(STROKES[0][1], w, STROKES[1][0])
        self.assertLessEqual(travel_route.top_under(xy, z, segs, MARGIN), LIMIT)
        for p in w:                                          # inside the work area, 3 decimals
            self.assertTrue(0 <= p[0] <= 225 and 0 <= p[1] <= 176)

    def test_straight_when_low_enough_or_unmeasured(self):
        xy, z = surface([(180, 190, 160, 170)])               # the mound is nowhere near
        self.assertEqual(travel_route.choose_gap(STROKES[0][1], STROKES[1][0], xy, z, MARGIN, LIMIT)[0], [])
        far = np.array([[10.0, 10.0]])
        self.assertEqual(travel_route.choose_gap((90, 110), (150, 110), far, np.array([-5.0]), MARGIN, LIMIT)[0], [])

    def test_no_route_round_keeps_straight_unless_clearly_lower(self):
        xy, z = surface([(40, 200, 40, 180)])                 # everything tall: nothing to gain
        w, top = travel_route.choose_gap(STROKES[0][1], STROKES[1][0], xy, z, MARGIN, LIMIT)
        self.assertEqual((w, top), ([], -6.0))

    def test_segments_through_the_jump_route(self):
        segs = pc.travel_segments([(65.7, 80.0)], STROKES, [], {0: [(120.0, 140.0)]})
        self.assertEqual(segs[-2:], [((90.0, 110.0), (120.0, 140.0)), ((120.0, 140.0), (150.0, 110.0))])
        self.assertEqual(pc.travel_segments([(65.7, 80.0)], STROKES)[-1], ((90.0, 110.0), (150.0, 110.0)))


def hop_program(hop_z=-20.0):
    """step 23's shape: first rise, first stroke, a low hop, second stroke, final rise."""
    return ["(test)", "G21", "G90", "G53 G0 Z-1.000", "G53 G0 X80.000 Y110.000",
            "G53 G1 X80.000 Y110.000 Z-30.000 F300", "G53 G1 X90.000 Y110.000 Z-30.000 F300",
            f"G53 G0 Z{hop_z:.3f}", "G53 G0 X150.000 Y110.000",
            "G53 G1 X150.000 Y110.000 Z-30.000 F300", "G53 G1 X160.000 Y110.000 Z-30.000 F300", "G53 G0 Z-1.000"]


LIFTED = [np.array([[80.0, 110.0, -30.0], [90.0, 110.0, -30.0]]), np.array([[150.0, 110.0, -30.0], [160.0, 110.0, -30.0]])]
GAP = {"from": [90.0, 110.0], "to": [150.0, 110.0], "waypoints": [[120.0, 140.0]]}


class ProgramTests(unittest.TestCase):
    def test_the_routed_jump_is_written_at_travel_z_and_passes_the_re_read(self):
        import step_path_clearance as spc
        out = spc.gap_routes(hop_program(), LIFTED, [GAP], TRAVEL)
        self.assertEqual(out[7:10], ["G53 G0 Z-1.000", "G53 G0 X120.000 Y140.000", "G53 G0 X150.000 Y110.000"])
        self.assertNotIn("G53 G0 Z-20.000", out)              # the low hop over the mound is gone
        rr = reread_v2("\n".join(out), TRAVEL, -45.0, 300.0)
        self.assertEqual(rr["strokes"], 2)
        self.assertEqual(travel_route.program_gaps(out, TRAVEL), [((90.0, 110.0), [(120.0, 140.0)], (150.0, 110.0))])
        self.assertEqual(travel_route.program_approach(out, TRAVEL), [])
        self.assertEqual(hop_path.hop_moves_from_text("\n".join(out), TRAVEL), [])   # nothing low left to check
        other = dict(GAP, to=[151.0, 110.0])                   # not this program's jump: left straight
        self.assertEqual(spc.gap_routes(hop_program(), LIFTED, [other], TRAVEL), hop_program())
        self.assertEqual(travel_route.program_gaps(hop_program(), TRAVEL), [])

    def test_wrap_program_hands_the_gaps_over(self):
        import step_path_clearance as spc
        cfg = {"hand_capture": {"recording": "r.db3"}}
        env = {"HENNA_APPROACH": json.dumps({"recording": "r.db3", "target": [80.0, 110.0], "waypoints": [],
                                             "gaps": [GAP]})}
        info = spc.approach(cfg, env)                          # gaps alone are enough to hand over
        self.assertIsNotNone(info)
        out = spc.wrap_program(lambda lifted, hops, p, header: hop_program(), info)(LIFTED, [], {"travel_z": TRAVEL}, [])
        self.assertIn("G53 G0 X120.000 Y140.000", out)


class RunCheckTests(unittest.TestCase):
    def test_a_run_needs_the_jumps_that_were_just_checked(self):
        import step_path_clearance as spc
        import webui_server_v29 as v29
        job = {"id": "20261024-150000-job"}
        saved = (v29.v27.clearance, dict(v29._LAST_APPROACH), dict(v29._LAST_GAPS), v29.v10.cal)
        v29.v10.cal = lambda: {"travel_z": TRAVEL}
        routed = spc.gap_routes(hop_program(), LIFTED, [GAP], TRAVEL)
        try:
            v29.v27.clearance = v29.path_clearance
            v29._LAST_APPROACH[job["id"]] = []
            v29._LAST_GAPS[job["id"]] = v29._gap_key([GAP])
            v29._route_check(job, routed)                                   # the checked jumps: fine
            with self.assertRaisesRegex(ValueError, "jumps round tall skin between strokes are not the ones"):
                v29._route_check(job, hop_program())                        # the program goes straight over
            v29._LAST_GAPS[job["id"]] = []
            v29._route_check(job, hop_program())
            with self.assertRaisesRegex(ValueError, "not the ones checked now"):
                v29._route_check(job, routed)
            v29.v27.clearance = v29._WHOLE_HAND_CLEARANCE
            with self.assertRaisesRegex(ValueError, "only the path check can approve"):
                v29._route_check(job, routed)
        finally:
            v29.v27.clearance, v29.v10.cal = saved[0], saved[3]
            v29._LAST_APPROACH.clear()
            v29._LAST_APPROACH.update(saved[1])
            v29._LAST_GAPS.clear()
            v29._LAST_GAPS.update(saved[2])


if __name__ == "__main__":
    unittest.main()
