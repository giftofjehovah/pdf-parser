"""HTML renderer — page-faithful absolute layout using bbox coordinates.

Each page becomes a white box sized to the PDF page dimensions (scaled 1.5×).
Text elements and table cells are absolutely positioned from their BBox so
column widths, row heights, and element placement match the source document.

Two structural invariants maintained here:

1. **Page containment**: table cells are rendered in the page div that
   corresponds to their ``BBox.page``.  For stitched (multi-page) tables the
   table node lives as a child of page 0, but rows from page 1 are injected
   into page 1's div via a pre-pass that groups rows by page.

2. **Nested table coordinate origin**: inner table cells carry page-absolute
   coordinates.  When rendered inside an outer cell div (a CSS positioned
   ancestor) those coordinates must be shifted by the outer cell's own origin
   so that inner cells land at the correct position relative to their parent.

When *pdf_path* is supplied to :func:`to_html`, background fill colours are
read directly from the PDF so they match the source exactly.
"""

from __future__ import annotations

import html as _h
import re
from collections import defaultdict
from pathlib import Path

from pdf_parser.model import BBox, DocNode

_S = 1.5  # PDF points → CSS pixels

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
  /* Joined wrapped paragraphs use the union bbox of their visual lines:
     width matches the column, height spans all lines.  Allow normal wrapping
     so the joined text re-flows inside that bbox instead of being clipped. */
  white-space: normal;
  overflow: visible;
  line-height: {9 * _S:.2f}px;
}}
.tb-h1 {{ font-size: {14 * _S:.2f}px; font-weight: bold; overflow: visible; }}
.tb-h2 {{ font-size: {12 * _S:.2f}px; font-weight: bold; overflow: visible; }}
.tb-h3 {{ font-size: {11 * _S:.2f}px; font-weight: bold; overflow: visible; }}
.tb-h4 {{ font-size: {10 * _S:.2f}px; font-weight: bold; overflow: visible; }}
.cell {{
  position: absolute;
  overflow: hidden;
  white-space: pre-line;
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
# Helpers
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


def _bbox_pos(b: BBox, x_off: float = 0.0, y_off: float = 0.0) -> str:
    """Absolute-position style string, optionally offset by a parent origin."""
    x = (b.x0 - x_off) * _S
    y = (b.y0 - y_off) * _S
    w = (b.x1 - b.x0) * _S
    h = (b.y1 - b.y0) * _S
    return f"left:{x:.1f}px;top:{y:.1f}px;width:{w:.1f}px;height:{h:.1f}px;"


# ---------------------------------------------------------------------------
# PDF colour extraction
# ---------------------------------------------------------------------------

def _color_to_hex(color) -> str | None:
    if color is None:
        return None
    if isinstance(color, (int, float)):
        v = round(color * 255)
        return None if v >= 255 else f"#{v:02X}{v:02X}{v:02X}"
    if len(color) == 3:
        r, g, b = (round(c * 255) for c in color)
        return None if (r >= 255 and g >= 255 and b >= 255) else f"#{r:02X}{g:02X}{b:02X}"
    if len(color) == 4:   # CMYK
        c, m, y, k = color
        r = round((1 - c) * (1 - k) * 255)
        g = round((1 - m) * (1 - k) * 255)
        b = round((1 - y) * (1 - k) * 255)
        return None if (r >= 255 and g >= 255 and b >= 255) else f"#{r:02X}{g:02X}{b:02X}"
    return None


# (x0, top, x1, bottom, page_index, hex_color)
_RectEntry = tuple[float, float, float, float, int, str]


def _load_rect_colors(pdf_path: Path) -> list[_RectEntry]:
    import pdfplumber
    entries: list[_RectEntry] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_idx, page in enumerate(pdf.pages):
            for rect in page.rects:
                if not rect.get("fill"):
                    continue
                color = _color_to_hex(rect.get("non_stroking_color"))
                if color is None:
                    continue
                entries.append((rect["x0"], rect["top"], rect["x1"], rect["bottom"],
                                 page_idx, color))
    return entries


# image_id → "data:<mime>;base64,<b64>"
_ImageMap = dict[int, str]


def _stream_to_uri(stream) -> str:
    """Convert a pdfminer PDFStream to a base64 data URI string."""
    import base64
    import io

    from PIL import Image as PilImage

    filters = stream.attrs.get("Filter")
    if filters is not None:
        flist = filters if isinstance(filters, list) else [filters]
        if any("DCTDecode" in str(f) for f in flist):
            data = stream.get_data()
            b64 = base64.b64encode(data).decode()
            return f"data:image/jpeg;base64,{b64}"
    # Decompress to raw pixels and re-encode as PNG via Pillow.
    raw = stream.get_data()
    width = int(stream.attrs.get("Width", 0))
    height = int(stream.attrs.get("Height", 0))
    cs = str(stream.attrs.get("ColorSpace", "DeviceRGB"))
    mode = "L" if "Gray" in cs else "CMYK" if "CMYK" in cs else "RGB"
    img = PilImage.frombytes(mode, (width, height), raw)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/png;base64,{b64}"


def _load_images(pdf_path: Path) -> _ImageMap:
    """Extract all embedded images from the PDF as base64 data URIs."""
    import pdfplumber

    data: _ImageMap = {}
    stream_to_id: dict[int, int] = {}
    image_counter = 0
    seen_page_streams: set[tuple[int, int]] = set()

    with pdfplumber.open(str(pdf_path)) as doc:
        for page_idx, page in enumerate(doc.pages):
            for img in page.images:
                stream = img.get("stream")
                if stream is None:
                    continue
                sid = id(stream)
                if (page_idx, sid) in seen_page_streams:
                    continue
                seen_page_streams.add((page_idx, sid))
                if sid not in stream_to_id:
                    stream_to_id[sid] = image_counter
                    image_counter += 1
                image_id = stream_to_id[sid]
                if image_id not in data:
                    try:
                        data[image_id] = _stream_to_uri(stream)
                    except Exception:
                        pass
    return data


def _lookup_color(rects: list[_RectEntry], bbox: BBox) -> str | None:
    cx = (bbox.x0 + bbox.x1) / 2
    cy = (bbox.y0 + bbox.y1) / 2
    best_area = 0.0
    best: str | None = None
    for x0, y0, x1, y1, page, color in rects:
        if page != bbox.page:
            continue
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            area = (x1 - x0) * (y1 - y0)
            if area > best_area:
                best_area = area
                best = color
    return best


# ---------------------------------------------------------------------------
# Heuristic row classification (fallback when pdf_path absent)
# ---------------------------------------------------------------------------

_PCT_RE = re.compile(r"[-\d]+\.\d+%")
_NUM_RE = re.compile(r"^[\s$\(\)\-\d,\.%\u2014FY]+$")


def _classify_row_heuristic(row: DocNode) -> str:
    """Classify using text patterns; uses the stored row_index attr."""
    row_idx = row.attrs.get("row_index", 0)
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


# ---------------------------------------------------------------------------
# Multi-page table pre-pass
# ---------------------------------------------------------------------------

def _collect_multipage_rows(
    node: DocNode,
    extras: dict[int, list[tuple[DocNode, list[DocNode]]]],
) -> None:
    """Walk *node* and register stitched-table rows into *extras* by page.

    For a stitched table whose node lives under page 0 but has rows on page 1,
    we record those page-1 rows so the page-1 renderer can emit them.
    """
    if node.kind == "table" and isinstance(node.bbox, list):
        home_page = node.bbox[0].page
        by_page: dict[int, list[DocNode]] = defaultdict(list)
        for row in node.children:
            pg = _single_bbox(row).page
            if pg != home_page:
                by_page[pg].append(row)
        for pg, rows in by_page.items():
            extras.setdefault(pg, []).append((node, rows))

    for child in node.children:
        _collect_multipage_rows(child, extras)


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

def _render_table_rows(
    table: DocNode,
    rows: list[DocNode],
    rects: list[_RectEntry],
    x_off: float = 0.0,
    y_off: float = 0.0,
) -> str:
    """Render a list of rows (all on the same page) as absolutely-positioned cells.

    *x_off* / *y_off* are the outer cell's page-origin when rendering nested
    tables; pass 0 for top-level tables.
    """
    use_rects = bool(rects)
    parts: list[str] = []

    for row in rows:
        rtype = _classify_row_heuristic(row) if not use_rects else ""

        for col_idx, cell in enumerate(row.children):
            if cell.attrs.get("covered"):
                continue
            b = _single_bbox(cell)
            pos = _bbox_pos(b, x_off, y_off)

            if use_rects:
                bg = _lookup_color(rects, b) or ""
                bold = "font-weight:bold;" if bg else ""
                extra_border = ""
                if bg and bg.upper() == "#DDEEFF":
                    extra_border = "border-top:1.2px solid #000;border-bottom:1.2px solid #000;"
                elif bg and bg.upper() == "#F0F0F0":
                    extra_border = "border-top:1px solid #000;"
                bg_css = f"background:{bg};" if bg else ""
                style = f"{pos}{bg_css}{bold}{extra_border}"
                is_right = cell.attrs.get("align") == "right"
                tag_open = f'<div class="cell{" num" if is_right else ""}" style="{style}'
            else:
                style = pos
                is_right = cell.attrs.get("align") == "right"
                num = " num" if is_right else ""
                tag_open = f'<div class="cell r{rtype}{num}" style="{style}'

            # Italic for percentage rows (non-label cells only)
            if _PCT_RE.search(cell.text or "") and col_idx > 0:
                tag_open += "font-style:italic;color:#444;"

            tag_open += '">'

            # Recurse into nested children — offset coordinates by this cell's
            # origin so positions are relative to the parent cell.  Tables
            # recurse into _render_table_rows; paragraphs / headings / list
            # items (e.g. a NOTE paragraph between two nested sub-tables) are
            # emitted as positioned text boxes so they don't get silently
            # dropped.
            nested_parts: list[str] = []
            for c in cell.children:
                if c.kind == "table":
                    nested_parts.append(
                        _render_table_rows(c, c.children, rects,
                                           x_off=b.x0, y_off=b.y0)
                    )
                elif c.kind in ("paragraph", "list_item"):
                    cb = _single_bbox(c)
                    nested_parts.append(
                        f'<div class="tb" style="{_bbox_pos(cb, b.x0, b.y0)}">'
                        f'{_esc(c.text)}</div>'
                    )
                elif c.kind == "heading":
                    cb = _single_bbox(c)
                    lvl = max(1, min(4, c.attrs.get("level", 2)))
                    nested_parts.append(
                        f'<div class="tb tb-h{lvl}" '
                        f'style="{_bbox_pos(cb, b.x0, b.y0)}">'
                        f'{_esc(c.text)}</div>'
                    )
            nested_html = "".join(nested_parts)

            parts.append(f"{tag_open}{_esc(cell.text)}{nested_html}</div>")

    return "".join(parts)


def _render_table(
    table: DocNode,
    rects: list[_RectEntry],
    page_index: int,
) -> str:
    """Render table rows that belong to *page_index* only."""
    rows = [r for r in table.children if _single_bbox(r).page == page_index]
    return _render_table_rows(table, rows, rects)


# ---------------------------------------------------------------------------
# Document rendering
# ---------------------------------------------------------------------------

def _render_node(
    node: DocNode,
    rects: list[_RectEntry],
    page_index: int,
    extras: dict[int, list[tuple[DocNode, list[DocNode]]]],
    images: _ImageMap,
) -> str:
    kind = node.kind

    if kind == "document":
        return "".join(_render_node(c, rects, page_index, extras, images) for c in node.children)

    if kind == "page":
        pi = node.attrs.get("page_index", 0)
        b = _single_bbox(node)
        w = (b.x1 - b.x0) * _S
        h = (b.y1 - b.y0) * _S
        # Render this page's own children (tables filtered to this page)
        inner = "".join(_render_node(c, rects, pi, extras, images) for c in node.children)
        # Inject rows from stitched tables whose home page < pi
        for tbl, rows in extras.get(pi, []):
            inner += _render_table_rows(tbl, rows, rects)
        return f'<div class="page" style="width:{w:.0f}px;height:{h:.0f}px;">{inner}</div>\n'

    if kind == "heading":
        b = _single_bbox(node)
        level = max(1, min(4, node.attrs.get("level", 2)))
        return f'<div class="tb tb-h{level}" style="{_bbox_pos(b)}">{_esc(node.text)}</div>'

    if kind == "paragraph":
        b = _single_bbox(node)
        return f'<div class="tb" style="{_bbox_pos(b)}">{_esc(node.text)}</div>'

    if kind == "section":
        return "".join(_render_node(c, rects, page_index, extras, images) for c in node.children)

    if kind == "table":
        return _render_table(node, rects, page_index)

    if kind == "list":
        return "".join(_render_node(c, rects, page_index, extras, images) for c in node.children)

    if kind == "list_item":
        b = _single_bbox(node)
        return f'<div class="tb" style="{_bbox_pos(b)}">{_esc(node.text)}</div>'

    if kind == "figure":
        b = _single_bbox(node)
        image_id = node.attrs.get("image_id")
        src = images.get(image_id, "") if images and image_id is not None else ""
        if not src:
            src = _esc(node.attrs.get("path", ""))
        return (
            f'<div class="tb" style="{_bbox_pos(b)}">'
            f'<img src="{src}" style="max-width:100%;max-height:100%;'
            f'object-fit:contain;" alt=""></div>'
        )

    return ""


def to_html(tree: DocNode, pdf_path: Path | str | None = None) -> str:
    """Render *tree* as a self-contained HTML document.

    Args:
        tree:     Parsed document tree.
        pdf_path: Path to the original PDF.  When supplied, background fill
                  colours are extracted directly from the PDF so they match
                  the source exactly, and embedded images are inlined as
                  base64 data URIs.  Omit for a heuristic-only render.
    """
    rects: list[_RectEntry] = []
    images: _ImageMap = {}
    if pdf_path is not None:
        p = Path(pdf_path)
        rects = _load_rect_colors(p)
        images = _load_images(p)

    # Pre-collect stitched-table rows that belong to pages > their home page.
    extras: dict[int, list[tuple[DocNode, list[DocNode]]]] = {}
    _collect_multipage_rows(tree, extras)

    body = _render_node(tree, rects, page_index=0, extras=extras, images=images)
    return _DOC_TMPL.format(css=_CSS, body=body)
