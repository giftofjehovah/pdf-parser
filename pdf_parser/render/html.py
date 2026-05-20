"""HTML renderer — page-faithful absolute layout using bbox coordinates.

Each page becomes a white box sized to the PDF page dimensions (scaled 1.5×).
Text elements and table cells are absolutely positioned from their BBox.

When *pdf_path* is supplied to :func:`to_html`, background fill colours are
extracted directly from the PDF (via pdfplumber) so they match the source
document exactly.  Without a *pdf_path* the renderer falls back to heuristic
row classification that is good enough for simple inspection.
"""

from __future__ import annotations

import html as _h
from pathlib import Path

from pdf_parser.model import BBox, DocNode

# PDF points → CSS pixels.  1.5× gives a comfortable on-screen reading size.
_S = 1.5

_CSS = f"""\
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: #777;
  padding: 24px;
  font-family: Helvetica, Arial, sans-serif;
  font-size: {7.5 * _S:.2f}px;
}}
.page {{
  position: relative;
  background: white;
  margin: 0 auto 32px;
  box-shadow: 0 4px 24px rgba(0,0,0,.45);
  overflow: hidden;
}}
.tb {{
  position: absolute;
  overflow: hidden;
  white-space: nowrap;
}}
.tb-h1 {{ font-size: {14 * _S:.2f}px; font-weight: bold; }}
.tb-h2 {{ font-size: {12 * _S:.2f}px; font-weight: bold; }}
.tb-h3 {{ font-size: {11 * _S:.2f}px; font-weight: bold; }}
.tb-h4 {{ font-size: {10 * _S:.2f}px; font-weight: bold; }}
.cell {{
  position: absolute;
  overflow: hidden;
  display: flex;
  align-items: center;
  font-size: {7.5 * _S:.2f}px;
  line-height: {9 * _S:.2f}px;
  padding: {2 * _S:.1f}px {3 * _S:.1f}px;
  border: 0.6px solid #AAAAAA;
}}
/* heuristic fallback classes (used when no pdf_path is given) */
.rH {{ background: #D0D0D0; font-weight: bold; justify-content: center; }}
.rS {{ background: #E8E8E8; font-weight: bold; font-size: {7.0 * _S:.2f}px; }}
.rT {{ background: #F0F0F0; font-weight: bold; border-top-color: #000 !important; }}
.rK {{ background: #DDEEFF; font-weight: bold;
       border-top: 1.2px solid #000 !important;
       border-bottom: 1.2px solid #000 !important; }}
.rP {{ font-style: italic; color: #444; }}
.num {{ justify-content: flex-end; }}
"""

_DOC_TMPL = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PDF Preview</title>
<style>
{css}
</style>
</head>
<body>
{body}
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Coordinate / colour helpers
# ---------------------------------------------------------------------------

def _esc(s: str | None) -> str:
    return _h.escape(s or "", quote=False)


def _single_bbox(node: DocNode) -> BBox:
    if isinstance(node.bbox, BBox):
        return node.bbox
    bboxes: list[BBox] = node.bbox
    return BBox(
        page=bboxes[0].page,
        x0=min(b.x0 for b in bboxes),
        y0=min(b.y0 for b in bboxes),
        x1=max(b.x1 for b in bboxes),
        y1=max(b.y1 for b in bboxes),
    )


def _bbox_style(b: BBox) -> str:
    return (
        f"left:{b.x0 * _S:.1f}px;"
        f"top:{b.y0 * _S:.1f}px;"
        f"width:{(b.x1 - b.x0) * _S:.1f}px;"
        f"height:{(b.y1 - b.y0) * _S:.1f}px;"
    )


def _color_to_hex(color) -> str | None:
    """Convert a pdfplumber colour value to a hex string; return None for white."""
    if color is None:
        return None
    if isinstance(color, (int, float)):
        v = round(color * 255)
        if v >= 255:
            return None
        return f"#{v:02X}{v:02X}{v:02X}"
    if len(color) == 3:
        r, g, b = (round(c * 255) for c in color)
        if r >= 255 and g >= 255 and b >= 255:
            return None
        return f"#{r:02X}{g:02X}{b:02X}"
    if len(color) == 4:            # CMYK
        c, m, y, k = color
        r = round((1 - c) * (1 - k) * 255)
        g = round((1 - m) * (1 - k) * 255)
        b = round((1 - y) * (1 - k) * 255)
        if r >= 255 and g >= 255 and b >= 255:
            return None
        return f"#{r:02X}{g:02X}{b:02X}"
    return None


# A rect entry: (x0, top, x1, bottom, page_index, hex_color)
_RectEntry = tuple[float, float, float, float, int, str]


def _load_rect_colors(pdf_path: Path) -> list[_RectEntry]:
    """Extract all filled, non-white rectangles from the PDF."""
    import pdfplumber  # local import — not all callers need the PDF open

    entries: list[_RectEntry] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            for rect in page.rects:
                if not rect.get("fill"):
                    continue
                color = _color_to_hex(rect.get("non_stroking_color"))
                if color is None:
                    continue
                entries.append((
                    rect["x0"], rect["top"],
                    rect["x1"], rect["bottom"],
                    page_idx, color,
                ))
    return entries


def _lookup_color(rects: list[_RectEntry], bbox: BBox) -> str | None:
    """Return the hex fill colour of the largest rect that contains the cell centre."""
    cx = (bbox.x0 + bbox.x1) / 2
    cy = (bbox.y0 + bbox.y1) / 2
    best_area = 0.0
    best_color: str | None = None
    for x0, y0, x1, y1, page, color in rects:
        if page != bbox.page:
            continue
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            area = (x1 - x0) * (y1 - y0)
            if area > best_area:
                best_area = area
                best_color = color
    return best_color


# ---------------------------------------------------------------------------
# Heuristic row classification (fallback when pdf_path is absent)
# ---------------------------------------------------------------------------

import re as _re

_PCT_RE = _re.compile(r"[-\d]+\.\d+%")
_NUM_RE = _re.compile(r"^[\s$\(\)\-\d,\.%\u2014FY]+$")


def _classify_row_fallback(row: DocNode, row_idx: int) -> str:
    if row_idx == 0:
        return "H"
    cells = row.children
    if not cells:
        return "I"
    label = (cells[0].text or "").strip()
    data = [(c.text or "").strip() for c in cells[1:]]
    if all(not t for t in data) and label:
        return "S"
    if any(_PCT_RE.search(t) for t in data if t):
        return "P"
    ll = label.lower()
    if ll.startswith("total") or "  total" in ll:
        return "T"
    if not label.startswith("  ") and any(data):
        return "K"
    return "I"


_FALLBACK_BG = {
    "H": "#D0D0D0", "S": "#E8E8E8", "T": "#F0F0F0",
    "K": "#DDEEFF", "P": "", "I": "",
}


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_table(
    table: DocNode,
    rects: list[_RectEntry],
) -> str:
    use_rects = bool(rects)
    parts: list[str] = []

    for row_idx, row in enumerate(table.children):
        # Determine per-row background colour once (from the first cell's lookup).
        row_color: str | None = None
        if use_rects and row.children:
            first_bbox = _single_bbox(row.children[0])
            row_color = _lookup_color(rects, first_bbox)

        # Heuristic fallback class (used when no rects available)
        fb_cls = _classify_row_fallback(row, row_idx) if not use_rects else ""

        for col_idx, cell in enumerate(row.children):
            b = _single_bbox(cell)
            pos = _bbox_style(b)

            # --- background & bold ---
            if use_rects:
                # Re-look up per cell (handles partial-row fills; usually same as row_color).
                bg = _lookup_color(rects, b) or row_color or ""
                bold = "font-weight:bold;" if bg else ""
                extra_border = ""
                # Blue-tinted rows (#DDEEFF) get double border like K rows.
                if bg and bg.upper() == "#DDEEFF":
                    extra_border = "border-top:1.2px solid #000;border-bottom:1.2px solid #000;"
                # Total rows (#F0F0F0) get top rule.
                elif bg and bg.upper() == "#F0F0F0":
                    extra_border = "border-top:1px solid #000;"
                inline = f"background:{bg};" if bg else ""
                style = f"{pos}{inline}{bold}{extra_border}"
                classes = "cell"
            else:
                style = pos
                classes = f"cell r{fb_cls}"

            # --- text colour / italic for percentage rows ---
            text = cell.text or ""
            italic = _PCT_RE.search(text) and col_idx > 0

            open_tag = f'<div class="{classes}" style="{style}'
            if italic:
                open_tag += "font-style:italic;color:#444;"
            open_tag += '">'

            # --- alignment: col 0 is left (label), rest are right-aligned ---
            num_cls = "" if col_idx == 0 else " num"
            if num_cls and use_rects:
                # num is a CSS class we still need; inject it
                open_tag = open_tag.replace('class="cell"', 'class="cell num"', 1)
            elif num_cls and not use_rects:
                open_tag = open_tag.replace(f'class="cell r{fb_cls}"',
                                            f'class="cell r{fb_cls} num"', 1)

            # Recurse into nested tables
            inner = "".join(
                _render_table(c, rects)
                for c in cell.children
                if c.kind == "table"
            )
            parts.append(f"{open_tag}{_esc(cell.text)}{inner}</div>")

    return "".join(parts)


def _render_node(node: DocNode, rects: list[_RectEntry]) -> str:
    kind = node.kind

    if kind == "document":
        return "".join(_render_node(c, rects) for c in node.children)

    if kind == "page":
        b = _single_bbox(node)
        w = (b.x1 - b.x0) * _S
        h = (b.y1 - b.y0) * _S
        inner = "".join(_render_node(c, rects) for c in node.children)
        return f'<div class="page" style="width:{w:.0f}px;height:{h:.0f}px;">{inner}</div>\n'

    if kind == "heading":
        b = _single_bbox(node)
        level = max(1, min(4, node.attrs.get("level", 2)))
        return f'<div class="tb tb-h{level}" style="{_bbox_style(b)}">{_esc(node.text)}</div>'

    if kind == "paragraph":
        b = _single_bbox(node)
        return f'<div class="tb" style="{_bbox_style(b)}">{_esc(node.text)}</div>'

    if kind == "section":
        return "".join(_render_node(c, rects) for c in node.children)

    if kind == "table":
        return _render_table(node, rects)

    if kind in ("list", "list_item"):
        b = _single_bbox(node)
        bullet = "• " if kind == "list_item" else ""
        return f'<div class="tb" style="{_bbox_style(b)}">{bullet}{_esc(node.text)}</div>'

    if kind == "figure":
        b = _single_bbox(node)
        path = _esc(node.attrs.get("path", ""))
        return (
            f'<div class="tb" style="{_bbox_style(b)}">'
            f'<img src="{path}" style="max-width:100%;max-height:100%;"></div>'
        )

    return ""


def to_html(tree: DocNode, pdf_path: Path | str | None = None) -> str:
    """Render *tree* as a self-contained HTML document.

    Args:
        tree:     Parsed document tree.
        pdf_path: Path to the original PDF.  When supplied, background fill
                  colours are extracted directly from the PDF so they match
                  the source exactly.  Omit for a heuristic-only render.
    """
    rects: list[_RectEntry] = []
    if pdf_path is not None:
        rects = _load_rect_colors(Path(pdf_path))

    body = _render_node(tree, rects)
    return _DOC_TMPL.format(css=_CSS, body=body)
