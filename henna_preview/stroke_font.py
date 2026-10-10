"""Single-stroke capital letters for writing a name on the hand (user request 2026-10-10).

Normal fonts are outlines (filled shapes); henna needs single pen lines, so each letter here is a few
polylines on a grid: baseline y = 0, cap height y = 10, x to the right. `name_svg(text)` returns a stroke-only
SVG (fill none) that goes through the normal design checks (read_svg, line art only, all-or-nothing on skin).
Letters A-Z (lower case is written as capitals), space and hyphen.
"""
import math
import re

CAP = 10.0
GAP = 2.4          # space between letters
SPACE = 5.0        # width of a space
MAX_CHARS = 16


def _arc(cx, cy, rx, ry, a0, a1, n=24):
    return [(cx + rx * math.cos(math.radians(a0 + (a1 - a0) * i / n)),
             cy + ry * math.sin(math.radians(a0 + (a1 - a0) * i / n))) for i in range(n + 1)]


def _glyphs():
    g = {}
    g["A"] = (7.0, [[(0, 0), (3.5, 10), (7, 0)], [(1.3, 3.8), (5.7, 3.8)]])
    g["B"] = (6.6, [[(0, 0), (0, 10), (3.6, 10)] + _arc(3.6, 7.6, 2.4, 2.4, 90, -90)[1:] + [(0, 5.2), (4.0, 5.2)]
                    + _arc(4.0, 2.6, 2.6, 2.6, 90, -90)[1:] + [(0, 0)]])
    g["C"] = (8.6, [_arc(4.6, 5, 4.6, 5, 40, 320, 32)])
    g["D"] = (7.2, [[(0, 0), (0, 10), (2.4, 10)] + _arc(2.4, 5, 4.8, 5, 90, -90, 28)[1:] + [(0, 0)]])
    g["E"] = (6.0, [[(6, 10), (0, 10), (0, 0), (6, 0)], [(0, 5), (4.6, 5)]])
    g["F"] = (6.0, [[(6, 10), (0, 10), (0, 0)], [(0, 5), (4.6, 5)]])
    g["G"] = (8.6, [_arc(4.6, 5, 4.6, 5, 40, 360, 34) + [(9.2, 4.2), (5.2, 4.2)]])
    g["H"] = (7.0, [[(0, 0), (0, 10)], [(7, 0), (7, 10)], [(0, 5), (7, 5)]])
    g["I"] = (0.0, [[(0, 0), (0, 10)]])
    g["J"] = (5.0, [[(5, 10), (5, 3)] + _arc(2.5, 3, 2.5, 3, 0, -180, 16)[1:]])
    g["K"] = (6.4, [[(0, 0), (0, 10)], [(6.2, 10), (0, 3.6)], [(2.1, 5.8), (6.4, 0)]])
    g["L"] = (5.6, [[(0, 10), (0, 0), (5.6, 0)]])
    g["M"] = (8.4, [[(0, 0), (0, 10), (4.2, 3), (8.4, 10), (8.4, 0)]])
    g["N"] = (7.0, [[(0, 0), (0, 10), (7, 0), (7, 10)]])
    g["O"] = (9.2, [_arc(4.6, 5, 4.6, 5, 90, 450, 40)])
    g["P"] = (6.6, [[(0, 0), (0, 10), (3.8, 10)] + _arc(3.8, 7.4, 2.6, 2.6, 90, -90)[1:] + [(0, 4.8)]])
    g["Q"] = (9.2, [_arc(4.6, 5, 4.6, 5, 90, 450, 40), [(5.4, 2.6), (9.4, -0.8)]])
    g["R"] = (6.8, [[(0, 0), (0, 10), (3.8, 10)] + _arc(3.8, 7.4, 2.6, 2.6, 90, -90)[1:] + [(0, 4.8)],
                    [(3.2, 4.8), (6.8, 0)]])
    g["S"] = (6.8, [_arc(3.4, 7.5, 3.2, 2.5, 25, 270, 22) + _arc(3.4, 2.5, 3.4, 2.5, 90, -155, 22)[1:]])
    g["T"] = (7.0, [[(0, 10), (7, 10)], [(3.5, 10), (3.5, 0)]])
    g["U"] = (7.0, [[(0, 10), (0, 3.5)] + _arc(3.5, 3.5, 3.5, 3.5, 180, 360, 20)[1:] + [(7, 10)]])
    g["V"] = (7.0, [[(0, 10), (3.5, 0), (7, 10)]])
    g["W"] = (9.6, [[(0, 10), (2.4, 0), (4.8, 7), (7.2, 0), (9.6, 10)]])
    g["X"] = (7.0, [[(0, 10), (7, 0)], [(7, 10), (0, 0)]])
    g["Y"] = (7.0, [[(0, 10), (3.5, 5), (7, 10)], [(3.5, 5), (3.5, 0)]])
    g["Z"] = (7.0, [[(0, 10), (7, 10), (0, 0), (7, 0)]])
    g["-"] = (4.0, [[(0, 5), (4, 5)]])
    return g


GLYPHS = _glyphs()


def clean_name(text):
    if not isinstance(text, str):
        raise ValueError("type a name")
    t = re.sub(r"\s+", " ", text.strip()).upper()
    if not t:
        raise ValueError("type a name")
    if len(t) > MAX_CHARS:
        raise ValueError(f"a name can have at most {MAX_CHARS} letters")
    bad = sorted({ch for ch in t if ch != " " and ch not in GLYPHS})
    if bad:
        raise ValueError("only the letters A-Z, spaces and hyphens can be written (not: " + " ".join(bad) + ")")
    return t


def name_strokes(text):
    """Polylines (lists of (x, y), y up) for the cleaned name, letters placed left to right."""
    t = clean_name(text)
    x, out = 0.0, []
    for ch in t:
        if ch == " ":
            x += SPACE
            continue
        width, lines = GLYPHS[ch]
        out += [[(x + px, py) for px, py in line] for line in lines]
        x += width + GAP
    return out


def name_svg(text):
    """Stroke-only SVG of the name (SVG y points down)."""
    t = clean_name(text)
    lines = name_strokes(t)
    xs = [p[0] for ln in lines for p in ln]
    ys = [p[1] for ln in lines for p in ln]
    m = 1.0
    x0, x1, y0, y1 = min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m
    w, h = x1 - x0, y1 - y0
    paths = []
    for i, ln in enumerate(lines, start=1):
        d = "M " + " L ".join(f"{px - x0:.3f} {y1 - py:.3f}" for px, py in ln)
        paths.append(f'    <path id="s{i}" d="{d}"/>')
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w:.3f} {h:.3f}" width="{w:.2f}mm" height="{h:.2f}mm">\n'
            f'  <title>Name: {t}</title>\n'
            '  <desc>Single-stroke letters written by Henna Studio (line art, no fill).</desc>\n'
            '  <g fill="none" stroke="#7a3b10" stroke-width="0.8" stroke-linecap="round" stroke-linejoin="round">\n'
            + "\n".join(paths) + "\n  </g>\n</svg>\n")


def file_stem(text):
    return "Name_" + clean_name(text).replace(" ", "_")
