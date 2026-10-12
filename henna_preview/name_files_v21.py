"""Studio v21 name files - "Second review fixes" (outside review by Codex, 2026-10-12).

1. The name in the SVG <title> is XML-escaped: "A&B" (allowed by font_strokes) made a malformed SVG in v19/v20.
2. Saving and reuse use the SAME file name: v11's upload shortens the name to 40 characters, but v18/v19 looked
   for the longer name, so a long name was saved again every time. Reuse now looks for the saved name and its
   numbered copies (Name_X.svg, Name_X-2.svg ...) with exactly the same content.
font_strokes.py, stroke_font.py and webui_server_v11/v18/v19 are unchanged.
"""
import glob
import os
import re
from xml.sax.saxutils import escape

from henna_preview import font_strokes


def upload_stem(name):
    """Exactly the file name v11.design_upload saves under (keep in step with webui_server_v11.design_upload)."""
    return re.sub(r"[^A-Za-z0-9_-]+", "_", os.path.splitext(os.path.basename(str(name)))[0]).strip("_")[:40] or "design"


def font_name_svg(text, font_id):
    """font_strokes.font_name_svg with the name escaped in the title (the drawing itself is identical)."""
    t = font_strokes.clean_text(text)
    svg = font_strokes.font_name_svg(text, font_id)
    raw = f"  <title>Name: {t} ("
    if svg.count(raw) != 1:
        raise ValueError("unexpected name SVG layout")
    return svg.replace(raw, f"  <title>Name: {escape(t)} (", 1)


def find_same(upload_dir, stem, svg):
    """An already saved upload with this file name (or a numbered copy) and exactly this content, else None."""
    saved = upload_stem(stem)
    numbered = re.compile(re.escape(saved) + r"(-\d+)?\.svg")
    for path in sorted(glob.glob(os.path.join(glob.escape(upload_dir), glob.escape(saved) + "*.svg"))):
        if not numbered.fullmatch(os.path.basename(path)):
            continue
        with open(path, encoding="utf-8") as f:
            if f.read() == svg:
                return path
    return None
