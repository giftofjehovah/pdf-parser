"""HTML renderer — page-faithful absolute layout using bbox coordinates.

Each page becomes a white box sized to the PDF page dimensions (scaled 1.5×).
Text elements and table cells are absolutely positioned from their BBox, so
column widths, row heights, and element placement match the source document.
Row colours and font weight are recovered via text-pattern heuristics.
"""

from __future__ import annotations

import html as _h
import re

from pdf_parser.model import BBox, DocNode

# PDF points → CSS pixels.  1 pt = 1/72 in; at 96 dpi → 96/72 = 1.333.
# 1.5 gives a comfortable on-screen size while staying proportional.
_S = 1.5


# ---------------------------------------------------------------------------
# Palette — mirrors the synthetic fixture builder so the output is faithful
# ---------------------------------------------------------------------------
_BG: dict[str, str] = {
    "H": "#D0D0D0",   # column header
    "S": "#E8E8E8",   # section header (all data cells empty)
    "T": "#F0F0F0",   # subtotal
    "K": "#DDEEFF",   # key metric (Gross Profit, Net Income …)
    "P": "white",     # percentage / rate row (italic)
    "I": "white",     # normal indented item
}

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
/* text blocks (headings, paragraphs) */
.tb {{
  position: absolute;
  overflow: hidden;
  white-space: nowrap;
}}
.tb-h1 {{ font-size: {14 * _S:.2f}px; font-weight: bold; }}
.tb-h2 {{ font-size: {12 * _S:.2f}px; font-weight: bold; }}
.tb-h3 {{ font-size: {11 * _S:.2f}px; font-weight: bold; }}
.tb-h4 {{ font-size: {10 * _S:.2f}px; font-weight: bold; }}
/* table cells */
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
/* row-type modifiers */
.rH {{ background: #D0D0D0; font-weight: bold; justify-content: center; }}
.rS {{ background: #E8E8E8; font-weight: bold; font-size: {7.0 * _S:.2f}px; }}
.rT {{ background: #F0F0F0; font-weight: bold; border-top-color: #000 !important; }}
.rK {{ background: #DDEEFF; font-weight: bold;
       border-top: 1.2px solid #000 !important;
       border-bottom: 1.2px solid #000 !important; }}
.rP {{ font-style: italic; color: #444; }}
/* numeric columns: right-align */
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
# Helpers
# ---------------------------------------------------------------------------

def _esc(s: str | None) -> str:
    return _h.escape(s or "", quote=False)


def _px(pt: float) -> str:
    return f"{pt * _S:.1f}px"


def _bbox_style(b: BBox) -> str:
    x = b.x0 * _S
    y = b.y0 * _S
    w = (b.x1 - b.x0) * _S
    h = (b.y1 - b.y0) * _S
    return f"left:{x:.1f}px;top:{y:.1f}px;width:{w:.1f}px;height:{h:.1f}px;"


def _single_bbox(node: DocNode) -> BBox:
    """Return the BBox for absolute positioning; handles list[BBox] by using the hull."""
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


# ---------------------------------------------------------------------------
# Row classification
# ---------------------------------------------------------------------------

_PCT_RE = re.compile(r"[-\d]+\.\d+%")
_NUM_RE = re.compile(r"^[\s$\(\)\-\d,\.%\u2014FY]+$")


def _classify_row(row: DocNode, row_idx: int) -> str:
    """Return one of H / S / T / K / P / I for this row."""
    if row_idx == 0:
        return "H"

    cells = row.children
    if not cells:
        return "I"

    label = (cells[0].text or "").strip()
    data = [(c.text or "").strip() for c in cells[1:]]
    all_empty = all(not t for t in data)

    if all_empty and label:
        return "S"

    if any(_PCT_RE.search(t) for t in data if t):
        return "P"

    ll = label.lower()
    if ll.startswith("total") or "  total" in ll:
        return "T"

    # Non-indented rows with data that don't begin with "Total" are key metrics.
    if not label.startswith("  ") and any(data):
        return "K"

    return "I"


def _is_numeric_col(cell: DocNode, row_type: str) -> bool:
    """True when the cell should be right-aligned (numeric column)."""
    if row_type in ("H", "S"):
        return False
    b = _single_bbox(cell)
    # Column 0 (label) is identifiable as the leftmost column.
    # We use the cell's left edge relative to the row bbox; if the cell starts
    # very close to the row's own left edge it's the label column.
    row_x0 = b.x0  # conservative — checked per cell
    # Fetch parent row x0 from siblings if possible; fall back to text heuristic.
    text = (cell.text or "").strip()
    if not text:
        return True  # empty data cells in numeric columns
    # If the cell text looks like a label (has letters beyond FY/EPS abbreviations),
    # treat as label col.
    return bool(_NUM_RE.match(text))


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_table(table: DocNode) -> str:
    parts: list[str] = []
    for row_idx, row in enumerate(table.children):
        rtype = _classify_row(row, row_idx)
        rcls = f"r{rtype}"
        for cell in row.children:
            b = _single_bbox(cell)
            style = _bbox_style(b)
            num = " num" if _is_numeric_col(cell, rtype) else ""
            text = _esc(cell.text)
            # Recurse into nested tables
            inner = "".join(_render_table(c) for c in cell.children if c.kind == "table")
            parts.append(
                f'<div class="cell {rcls}{num}" style="{style}">{text}{inner}</div>'
            )
    return "".join(parts)


def _render_node(node: DocNode) -> str:
    kind = node.kind

    if kind == "document":
        return "".join(_render_node(c) for c in node.children)

    if kind == "page":
        b = _single_bbox(node)
        w = (b.x1 - b.x0) * _S
        h = (b.y1 - b.y0) * _S
        inner = "".join(_render_node(c) for c in node.children)
        return f'<div class="page" style="width:{w:.0f}px;height:{h:.0f}px;">{inner}</div>\n'

    if kind == "heading":
        b = _single_bbox(node)
        level = max(1, min(4, node.attrs.get("level", 2)))
        return (
            f'<div class="tb tb-h{level}" style="{_bbox_style(b)}">'
            f'{_esc(node.text)}</div>'
        )

    if kind == "paragraph":
        b = _single_bbox(node)
        return f'<div class="tb" style="{_bbox_style(b)}">{_esc(node.text)}</div>'

    if kind == "section":
        return "".join(_render_node(c) for c in node.children)

    if kind == "table":
        return _render_table(node)

    if kind in ("list", "list_item"):
        b = _single_bbox(node)
        bullet = "• " if kind == "list_item" else ""
        return f'<div class="tb" style="{_bbox_style(b)}">{bullet}{_esc(node.text)}</div>'

    if kind == "figure":
        b = _single_bbox(node)
        path = _esc(node.attrs.get("path", ""))
        return f'<div class="tb" style="{_bbox_style(b)}"><img src="{path}" style="max-width:100%;max-height:100%;"></div>'

    return ""


def to_html(tree: DocNode) -> str:
    body = _render_node(tree)
    return _DOC_TMPL.format(css=_CSS, body=body)
