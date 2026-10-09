"""SVG design reader: shapes, curves, arcs, transforms, fill flags, and placement in machine X/Y."""
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from henna_preview.svg_design import SvgDesignError, parse_transform, path_subpaths, place, read_svg, resample

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def svg(body):
    f = tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8")
    f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">{body}</svg>')
    f.close()
    return f.name


class PathData(unittest.TestCase):
    def test_closed_square_absolute_and_relative(self):
        a = path_subpaths("M 0 0 L 10 0 L 10 10 L 0 10 Z")
        b = path_subpaths("m0,0 l10,0 0,10 -10,0z")
        self.assertEqual(len(a), 1)
        self.assertTrue(a[0][1])
        np.testing.assert_allclose(a[0][0], b[0][0])

    def test_two_subpaths_are_two_strokes(self):
        self.assertEqual(len(path_subpaths("M0 0 L5 0 M10 10 L20 10")), 2)

    def test_h_v_and_compact_numbers(self):
        p = path_subpaths("M0,0H10V-5.5h-2.5v.5")[0][0]
        np.testing.assert_allclose(p[-1], [7.5, -5.0])

    def test_cubic_ends_on_its_end_point(self):
        p = path_subpaths("M0 0 C 0 10 10 10 10 0")[0][0]
        np.testing.assert_allclose(p[-1], [10, 0])
        self.assertGreater(p[:, 1].max(), 7.0)   # bulges towards the control points

    def test_smooth_cubic_reflects_previous_control(self):
        p = path_subpaths("M0 0 C 0 10 10 10 10 0 S 20 -10 20 0")[0][0]
        self.assertLess(p[p[:, 0] > 12, 1].min(), -5.0)

    def test_arc_semicircle_radius(self):
        p = path_subpaths("M 0 0 A 5 5 0 0 1 10 0")[0][0]
        r = np.hypot(p[:, 0] - 5, p[:, 1])
        np.testing.assert_allclose(r, 5, atol=1e-6)

    def test_bad_data_is_rejected(self):
        with self.assertRaises(SvgDesignError):
            path_subpaths("10 10 L 5 5")


class Elements(unittest.TestCase):
    def test_circle_and_group_transform(self):
        d = read_svg(svg('<g transform="translate(10,20) scale(2)"><circle cx="0" cy="0" r="3" fill="none" stroke="#000"/></g>'))
        s = d["strokes"][0]
        self.assertTrue(s["closed"])
        np.testing.assert_allclose(np.hypot(s["points"][:, 0] - 10, s["points"][:, 1] - 20), 6, atol=1e-6)
        self.assertFalse(s["filled_only"])

    def test_filled_shape_without_stroke_is_flagged(self):
        d = read_svg(svg('<rect x="0" y="0" width="5" height="5"/>'))
        self.assertTrue(d["strokes"][0]["filled_only"])

    def test_line_polyline_polygon(self):
        d = read_svg(svg('<line x1="0" y1="0" x2="5" y2="0" stroke="#000"/>'
                         '<polyline points="0,0 1,1 2,0" stroke="#000" fill="none"/>'
                         '<polygon points="0,0 1,1 2,0" stroke="#000" fill="none"/>'))
        self.assertEqual([s["closed"] for s in d["strokes"]], [False, False, True])

    def test_rotate_about_point(self):
        m = parse_transform("rotate(90 10 10)")
        np.testing.assert_allclose(m @ [20, 10, 1], [10, 20, 1], atol=1e-9)

    def test_no_lines_is_an_error(self):
        with self.assertRaises(SvgDesignError):
            read_svg(svg('<text x="0" y="0">hi</text>'))


class Placement(unittest.TestCase):
    def test_fits_and_centres(self):
        d = read_svg(svg('<rect x="0" y="0" width="40" height="20" stroke="#000" fill="none"/>'))
        strokes, k = place(d, 30.0, (100.0, 50.0))
        p = strokes[0]["plan"]
        self.assertAlmostEqual(k, 0.75)
        np.testing.assert_allclose(p.max(0) - p.min(0), [30, 15], atol=0.3)
        np.testing.assert_allclose((p.max(0) + p.min(0)) / 2, [100, 50], atol=0.3)

    def test_resample_spacing_and_no_repeated_end(self):
        sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
        p, length = resample(sq, True, 0.5)
        self.assertAlmostEqual(length, 40.0)
        self.assertEqual(len(p), 80)
        np.testing.assert_allclose(np.linalg.norm(np.diff(p, axis=0), axis=1), 0.5, atol=1e-9)

    def test_svg_y_down_becomes_machine_y_up(self):
        d = read_svg(svg('<line x1="0" y1="0" x2="0" y2="10" stroke="#000"/>'))   # drawn downwards
        p = place(d, 10.0, (0, 0))[0][0]["plan"]
        self.assertLess(p[-1, 1], p[0, 1])


class TestMotif(unittest.TestCase):
    def test_motif_is_line_art_with_18_strokes(self):
        d = read_svg(os.path.join(ROOT, "designs", "test_henna_motif.svg"))
        self.assertEqual(len(d["strokes"]), 18)
        self.assertFalse(any(s["filled_only"] for s in d["strokes"]))
        self.assertTrue(all(s["closed"] for s in d["strokes"]))

    def test_scallop_bulges_outwards(self):
        d = read_svg(os.path.join(ROOT, "designs", "test_henna_motif.svg"))
        sc = [s for s in d["strokes"] if "scallop" in s["source"]][0]["points"]
        r = np.hypot(sc[:, 0] - 50, sc[:, 1] - 50)
        self.assertGreater(r.max(), 43.0)
        self.assertGreater(r.min(), 39.0)


if __name__ == "__main__":
    unittest.main()
