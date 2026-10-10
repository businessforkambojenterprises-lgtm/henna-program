"""Names in an installed font, turned into single pen lines (user request 2026-10-10, "Font choice for names").

Installed fonts are filled outlines. The name is drawn large in the font, the letters are thinned to their
centre lines (OpenCV ximgproc.thinning), the centre lines are traced into polylines (staircase pixels are not
taken for junctions, short dead-end twigs at junctions are removed), smoothed lightly and simplified. The
result is a stroke-only SVG that goes through the normal design checks. Only fonts found by `list_fonts()`
can be used - never a path given from outside.
"""
import glob
import os
import re

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

RENDER_PX = 220
SPUR_PX = 18
MAX_STROKES = 60
MAX_CHARS = 16
ALLOWED = re.compile(r"^[A-Za-z0-9 '&.\-]+$")
RECOMMENDED = ("Segoe Script", "Segoe Print", "Ink Free", "French Script MT")
_N4 = ((-1, 0), (1, 0), (0, -1), (0, 1))
_ND = ((-1, -1), (-1, 1), (1, -1), (1, 1))
_cache = {}


class FontStrokeError(ValueError):
    pass


def font_dirs():
    dirs = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(os.path.join(local, "Microsoft", "Windows", "Fonts"))
    return [d for d in dirs if os.path.isdir(d)]


def list_fonts():
    """[{id, name, path, recommended}] for installed .ttf/.otf fonts (cached)."""
    if "fonts" in _cache:
        return _cache["fonts"]
    seen, out = set(), []
    for d in font_dirs():
        for path in sorted(glob.glob(os.path.join(d, "*.ttf")) + glob.glob(os.path.join(d, "*.otf"))):
            try:
                family, style = ImageFont.truetype(path, 20).getname()
            except Exception:
                continue
            name = family if style in ("Regular", "Normal", "") else f"{family} {style}"
            if name in seen:
                continue
            seen.add(name)
            fid = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()
            out.append({"id": fid, "name": name, "path": path, "recommended": family in RECOMMENDED and name == family})
    out.sort(key=lambda f: (not f["recommended"], f["name"].lower()))
    _cache["fonts"] = out
    return out


def font_by_id(fid):
    for f in list_fonts():
        if f["id"] == fid:
            return f
    raise FontStrokeError("unknown font - choose one from the list")


def clean_text(text):
    if not isinstance(text, str):
        raise FontStrokeError("type a name")
    t = re.sub(r"\s+", " ", text.strip())
    if not t:
        raise FontStrokeError("type a name")
    if len(t) > MAX_CHARS:
        raise FontStrokeError(f"a name can have at most {MAX_CHARS} characters")
    if not ALLOWED.match(t):
        raise FontStrokeError("only letters, digits, spaces and - ' & . can be written")
    return t


def render_mask(text, font_path, px=RENDER_PX):
    f = ImageFont.truetype(font_path, px)
    left, top, right, bottom = f.getbbox(text)
    if right - left < 2 or bottom - top < 2:
        raise FontStrokeError("this font draws nothing for this text")
    pad = px // 5
    img = Image.new("L", (right - left + 2 * pad, bottom - top + 2 * pad), 0)
    ImageDraw.Draw(img).text((pad - left, pad - top), text, font=f, fill=255)
    return (np.array(img) > 127).astype(np.uint8) * 255


def trace_skeleton(skel):
    """Centre-line pixels -> list of pixel paths [(y, x), ...]."""
    ys, xs = np.nonzero(skel)
    pts = set(zip(ys.tolist(), xs.tolist()))

    def neighbours(p):
        y, x = p
        orth = [(y + dy, x + dx) for dy, dx in _N4 if (y + dy, x + dx) in pts]
        out = list(orth)
        for dy, dx in _ND:
            d = (y + dy, x + dx)
            if d in pts and not any(abs(d[0] - o[0]) + abs(d[1] - o[1]) == 1 for o in orth):
                out.append(d)          # diagonal step only when no orthogonal pixel already links to it
        return out

    nb = {p: neighbours(p) for p in pts}
    nodes = {p for p in pts if len(nb[p]) != 2}
    used, edges = set(), []
    for n in sorted(nodes):
        for q in nb[n]:
            if (n, q) in used:
                continue
            path, prev, cur = [n, q], n, q
            used.update({(n, q), (q, n)})
            while cur not in nodes:
                nxt = [r for r in nb[cur] if r != prev and (cur, r) not in used]
                if not nxt:
                    break
                r = nxt[0]
                used.update({(cur, r), (r, cur)})
                path.append(r)
                prev, cur = cur, r
            edges.append(path)
    seen = {p for e in edges for p in e}
    for p in sorted(pts - seen):                     # closed loops without any junction (o, 0, dots)
        if p in seen:
            continue
        loop, prev, cur = [p], None, p
        seen.add(p)
        while True:
            nxt = [r for r in nb[cur] if r != prev and r not in seen]
            if not nxt:
                break
            prev, cur = cur, nxt[0]
            loop.append(cur)
            seen.add(cur)
        loop.append(p)
        edges.append(loop)
    deg = {p: len(nb[p]) for p in nodes}
    spur = lambda e: (deg.get(e[0]) == 1) != (deg.get(e[-1]) == 1) and len(e) < SPUR_PX  # noqa: E731
    return [e for e in edges if not spur(e) and len(e) >= 3]


def _smooth(a, w=5):
    if len(a) < w + 2:
        return a
    k = np.ones(w) / w
    closed = np.allclose(a[0], a[-1])
    if closed:
        ext = np.vstack([a[-w:-1], a, a[1:w]])
        sm = np.column_stack([np.convolve(ext[:, i], k, "same") for i in range(2)])[w - 1:-(w - 1)]
        sm[-1] = sm[0]
        return sm
    sm = np.column_stack([np.convolve(a[:, i], k, "same") for i in range(2)])
    h = w // 2
    sm[:h], sm[-h:] = a[:h], a[-h:]                  # keep the stroke ends where they are
    return sm


def text_polylines(text, font_path):
    """Polylines in pixel units (x right, y down)."""
    mask = render_mask(text, font_path)
    skel = cv2.ximgproc.thinning(mask)
    out = []
    for e in trace_skeleton(skel):
        a = _smooth(np.array([[x, y] for y, x in e], float))
        s = cv2.approxPolyDP(a.astype(np.float32).reshape(-1, 1, 2), 0.9, False).reshape(-1, 2)
        if len(s) >= 2:
            out.append(s.astype(float))
    if not out:
        raise FontStrokeError("this font draws nothing usable for this text")
    if len(out) > MAX_STROKES:
        raise FontStrokeError(f"this font makes {len(out)} strokes (more than {MAX_STROKES}) - choose a simpler "
                              "font or a shorter name")
    return out


def font_name_svg(text, font_id):
    t = clean_text(text)
    font = font_by_id(font_id)
    lines = text_polylines(t, font["path"])
    allp = np.vstack(lines)
    x0, y0 = allp.min(axis=0) - 10
    x1, y1 = allp.max(axis=0) + 10
    paths = [f'    <path id="s{i}" d="M ' + " L ".join(f"{x - x0:.2f} {y - y0:.2f}" for x, y in ln) + '"/>'
             for i, ln in enumerate(lines, start=1)]
    safe_font = re.sub(r"[^A-Za-z0-9 .\-]", "", font["name"])
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {x1 - x0:.2f} {y1 - y0:.2f}">\n'
            f'  <title>Name: {t} ({safe_font})</title>\n'
            '  <desc>Centre lines of the font letters, written by Henna Studio (line art, no fill).</desc>\n'
            '  <g fill="none" stroke="#7a3b10" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">\n'
            + "\n".join(paths) + "\n  </g>\n</svg>\n")


def file_stem(text, font_id):
    t = re.sub(r"[^A-Za-z0-9]+", "_", clean_text(text)).strip("_")
    return f"Name_{t}_{font_id}"[:60]
