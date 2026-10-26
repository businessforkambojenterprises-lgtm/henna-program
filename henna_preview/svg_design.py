"""Line-art SVG -> design strokes -> machine X/Y polylines (plan view).

Reads <path>, <line>, <polyline>, <polygon>, <circle>, <ellipse> and <rect> (square corners),
with group/element transforms (matrix, translate, scale, rotate, skewX, skewY). Path commands:
M L H V C S Q T A Z, absolute and relative. Every subpath is one stroke (pen up between).
Standard library only. This is DESIGN data: it says where the line should go in plan view;
heights always come from measured depth later.

Filled shapes are flagged: henna needs single centre-lines, and a filled shape without a stroke
would be traced around its edge (double lines).
"""
import math
import re
import xml.etree.ElementTree as ET

import numpy as np


class SvgDesignError(ValueError):
    """Raised for unusable design files or placements."""


_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_TOKEN = re.compile(rf"([MmLlHhVvCcSsQqTtAaZz])|({_NUM})")
_SUPPORTED = {"path", "line", "polyline", "polygon", "circle", "ellipse", "rect"}
_IGNORED = {"svg", "g", "title", "desc", "defs", "metadata", "style", "namedview"}


# ---------- transforms ----------
def _mat(a, b, c, d, e, f):
    return np.array([[a, c, e], [b, d, f], [0.0, 0.0, 1.0]])


def parse_transform(text):
    m = np.eye(3)
    if not text:
        return m
    for name, args in re.findall(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)", text):
        v = [float(x) for x in re.findall(_NUM, args)]
        if name == "matrix" and len(v) == 6:
            t = _mat(*v)
        elif name == "translate":
            t = _mat(1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0.0)
        elif name == "scale":
            t = _mat(v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        elif name == "rotate":
            a = math.radians(v[0])
            t = _mat(math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0, 0)
            if len(v) == 3:
                t = _mat(1, 0, 0, 1, v[1], v[2]) @ t @ _mat(1, 0, 0, 1, -v[1], -v[2])
        elif name == "skewX":
            t = _mat(1, 0, math.tan(math.radians(v[0])), 1, 0, 0)
        elif name == "skewY":
            t = _mat(1, math.tan(math.radians(v[0])), 0, 1, 0, 0)
        else:
            raise SvgDesignError(f"bad transform: {name}({args})")
        m = m @ t
    return m


def _apply(m, pts):
    p = np.asarray(pts, float)
    return (m[:2, :2] @ p.T).T + m[:2, 2]


# ---------- curve flattening (fine; resampled later in mm) ----------
def _n(length, fine=0.25):
    return max(8, int(math.ceil(length / fine)))


def _cubic(p0, p1, p2, p3):
    n = _n(np.linalg.norm(p1 - p0) + np.linalg.norm(p2 - p1) + np.linalg.norm(p3 - p2))
    t = np.linspace(0, 1, n + 1)[1:, None]
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3


def _quad(p0, p1, p2):
    n = _n(np.linalg.norm(p1 - p0) + np.linalg.norm(p2 - p1))
    t = np.linspace(0, 1, n + 1)[1:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2


def _arc(p0, rx, ry, phi_deg, large, sweep, p1):
    """SVG endpoint arc -> points (SVG 1.1 implementation notes F.6.5/F.6.6)."""
    if np.allclose(p0, p1):
        return np.empty((0, 2))
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return np.array([p1])
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (p0 - p1) / 2
    x1, y1 = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = (x1 / rx) ** 2 + (y1 / ry) ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1 ** 2 - ry ** 2 * x1 ** 2
    den = rx ** 2 * y1 ** 2 + ry ** 2 * x1 ** 2
    co = math.sqrt(max(0.0, num / den)) * (-1 if large == sweep else 1)
    cx1, cy1 = co * rx * y1 / ry, -co * ry * x1 / rx
    cx, cy = cp * cx1 - sp * cy1 + (p0[0] + p1[0]) / 2, sp * cx1 + cp * cy1 + (p0[1] + p1[1]) / 2

    def ang(u, v):
        a = math.atan2(u[0] * v[1] - u[1] * v[0], u[0] * v[0] + u[1] * v[1])
        return a

    v1 = ((x1 - cx1) / rx, (y1 - cy1) / ry)
    v2 = ((-x1 - cx1) / rx, (-y1 - cy1) / ry)
    t1, dt = ang((1, 0), v1), ang(v1, v2)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi
    n = _n(abs(dt) * max(rx, ry))
    t = t1 + dt * np.linspace(0, 1, n + 1)[1:]
    x = cx + rx * np.cos(t) * cp - ry * np.sin(t) * sp
    y = cy + rx * np.cos(t) * sp + ry * np.sin(t) * cp
    pts = np.column_stack([x, y])
    pts[-1] = p1
    return pts


# ---------- path data ----------
def path_subpaths(d):
    """-> list of (points (N, 2), closed) in the path's own units."""
    toks = [(c, float(n) if n else None) for c, n in _TOKEN.findall(d or "")]
    out, cur, start, pts, closed = [], np.zeros(2), np.zeros(2), [], False
    prev_ctrl, prev_cmd, cmd, i = None, None, None, 0

    def num():
        nonlocal i
        if i >= len(toks) or toks[i][1] is None:
            raise SvgDesignError("path data ended early or has a misplaced number")
        i += 1
        return toks[i - 1][1]

    def flush():
        nonlocal pts, closed
        if len(pts) > 1:
            out.append((np.array(pts), closed))
        pts, closed = [], False

    while i < len(toks):
        if toks[i][0]:
            cmd = toks[i][0]
            i += 1
        elif cmd is None:
            raise SvgDesignError("path data must start with a command")
        rel, C = cmd.islower(), cmd.upper()
        base = cur if rel else np.zeros(2)
        if C == "M":
            flush()
            cur = base + [num(), num()]
            start, pts = cur.copy(), [cur.copy()]
            cmd = "l" if rel else "L"   # further pairs are line-tos
        elif C == "L":
            cur = base + [num(), num()]
            pts.append(cur.copy())
        elif C == "H":
            cur = np.array([(cur[0] if rel else 0.0) + num(), cur[1]])
            pts.append(cur.copy())
        elif C == "V":
            cur = np.array([cur[0], (cur[1] if rel else 0.0) + num()])
            pts.append(cur.copy())
        elif C in "CS":
            if C == "C":
                c1 = base + [num(), num()]
            else:
                c1 = 2 * cur - prev_ctrl if prev_cmd in "CS" and prev_ctrl is not None else cur.copy()
            c2, end = base + [num(), num()], base + [num(), num()]
            pts.extend(_cubic(cur, c1, c2, end))
            prev_ctrl, cur = c2, end
        elif C in "QT":
            if C == "Q":
                c1 = base + [num(), num()]
            else:
                c1 = 2 * cur - prev_ctrl if prev_cmd in "QT" and prev_ctrl is not None else cur.copy()
            end = base + [num(), num()]
            pts.extend(_quad(cur, c1, end))
            prev_ctrl, cur = c1, end
        elif C == "A":
            rx, ry, phi, large, sweep = num(), num(), num(), num(), num()
            end = base + [num(), num()]
            pts.extend(_arc(cur, rx, ry, phi, int(large), int(sweep), end))
            cur = end
        elif C == "Z":
            if pts and not np.allclose(pts[-1], start):
                pts.append(start.copy())
            closed = True
            cur = start.copy()
            flush()
            pts = [cur.copy()]
        prev_cmd = C
        if C not in "CSQT":
            prev_ctrl = None
    flush()
    return out


def _ellipse(cx, cy, rx, ry):
    n = _n(2 * math.pi * max(rx, ry))
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([cx + rx * np.cos(t), cy + ry * np.sin(t)])


def _style(el, inherited):
    s = dict(inherited)
    for k in ("fill", "stroke"):
        if el.get(k) is not None:
            s[k] = el.get(k).strip()
    for part in (el.get("style") or "").split(";"):
        if ":" in part:
            k, v = (x.strip() for x in part.split(":", 1))
            if k in ("fill", "stroke"):
                s[k] = v
    return s


FROM_STROKE_ATTR = "data-henna-from-stroke"
ORIGINAL_ATTR = "data-henna-original"


def read_svg(path):
    """-> {'strokes': [{'points', 'closed', 'source', 'filled_only'}], 'ignored': [...]} in SVG user units."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as e:
        raise SvgDesignError(f"cannot read SVG: {e}")
    strokes, ignored = [], []

    def walk(el, m, style):
        tag = el.tag.split("}")[-1]
        m = m @ parse_transform(el.get("transform"))
        style = _style(el, style)
        f = lambda k, d=0.0: float(re.findall(_NUM, el.get(k) or str(d))[0])  # noqa: E731
        geo = []
        if tag == "path":
            geo = path_subpaths(el.get("d"))
        elif tag == "line":
            geo = [(np.array([[f("x1"), f("y1")], [f("x2"), f("y2")]]), False)]
        elif tag in ("polyline", "polygon"):
            v = [float(x) for x in re.findall(_NUM, el.get("points") or "")]
            if len(v) >= 4:
                geo = [(np.array(v[:len(v) // 2 * 2]).reshape(-1, 2), tag == "polygon")]
        elif tag == "circle":
            geo = [(_ellipse(f("cx"), f("cy"), f("r"), f("r")), True)]
        elif tag == "ellipse":
            geo = [(_ellipse(f("cx"), f("cy"), f("rx"), f("ry")), True)]
        elif tag == "rect":
            x, y, w, h = f("x"), f("y"), f("width"), f("height")
            geo = [(np.array([[x, y], [x + w, y], [x + w, y + h], [x, y + h]]), True)]
        elif tag not in _IGNORED:
            ignored.append(tag)
        if tag in _SUPPORTED:
            filled_only = style.get("fill", "black") not in ("none", "transparent") and \
                style.get("stroke", "none") in ("none", "transparent")
            for k, (pts, closed) in enumerate(geo):
                if closed and len(pts) > 2 and np.allclose(pts[0], pts[-1]):
                    pts = pts[:-1]
                strokes.append({"points": _apply(m, pts), "closed": bool(closed),
                                "source": f"{tag}#{el.get('id') or len(strokes)}" + (f".{k}" if len(geo) > 1 else ""),
                                "filled_only": filled_only})
        for ch in el:
            walk(ch, m, style)

    walk(root, np.eye(3), {})
    if not strokes:
        raise SvgDesignError("no drawable lines found in the SVG")
    # the rest of a stopped run (Studio "Re-scan and continue", 2026-10-26): a copy of the design whose root says
    # FROM_STROKE_ATTR="n" - the first n strokes were drawn already. They still set the size and the centre (place),
    # so the rest lands exactly where it was; place() leaves them out.
    try:
        skip = int(root.get(FROM_STROKE_ATTR) or 0)
    except ValueError:
        raise SvgDesignError(f"{FROM_STROKE_ATTR} must be a whole number")
    if not 0 <= skip < len(strokes):
        raise SvgDesignError(f"{FROM_STROKE_ATTR}={skip}: the design has {len(strokes)} strokes")
    for i, st in enumerate(strokes):
        st["skip"] = i < skip
    return {"strokes": strokes, "ignored": sorted(set(ignored)), "from_stroke": skip}


# ---------- placement in machine X/Y ----------
def resample(points, closed, spacing):
    """Evenly spaced points along the polyline (closed: no repeated end point)."""
    p = np.asarray(points, float)
    if closed:
        p = np.vstack([p, p[:1]])
    seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = s[-1]
    if total <= 0:
        return p[:1], 0.0
    n = max(2, int(round(total / spacing)))
    t = np.linspace(0, total, n + 1)
    if closed:
        t = t[:-1]
    out = np.column_stack([np.interp(t, s, p[:, 0]), np.interp(t, s, p[:, 1])])
    return out, float(total)


def place(design, fit_mm, centre_xy, rotation_deg=0.0, spacing_mm=0.5):
    """Scale the whole design to fit fit_mm (largest side), centre it on centre_xy, rotate. Strokes marked "skip"
    (already drawn - read_svg FROM_STROKE_ATTR) count for the size and centre but are not returned.

    SVG y points down; machine Y is taken as -y so the design keeps its look in an X-right/Y-up view.
    -> list of {'plan' (N, 2) machine mm, 'closed', 'source', 'length_mm'}, scale (mm per SVG unit).
    """
    if not fit_mm or fit_mm <= 0:
        raise SvgDesignError("fit size must be > 0 mm")
    allp = np.vstack([s["points"] for s in design["strokes"]])
    lo, hi = allp.min(0), allp.max(0)
    size = float(max(hi - lo))
    if size <= 0:
        raise SvgDesignError("design has no extent")
    k = fit_mm / size
    mid = (lo + hi) / 2
    a = math.radians(rotation_deg)
    R = np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])
    out = []
    for s in design["strokes"]:
        if s.get("skip"):
            continue
        q = (s["points"] - mid) * k
        q[:, 1] = -q[:, 1]
        q = (R @ q.T).T + np.asarray(centre_xy, float)
        plan, length = resample(q, s["closed"], spacing_mm)
        out.append({"plan": plan, "closed": s["closed"], "source": s["source"], "length_mm": round(length, 2)})
    return out, k
