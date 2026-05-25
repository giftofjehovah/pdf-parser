"""Stage 5: merge tables that continue across pages via column-anchor + header match."""

from __future__ import annotations

from pdf_parser.model import BBox, DocNode

COLUMN_ANCHOR_TOL = 4.0   # points
# A table that ends before this fraction of the page height from the bottom is
# complete on that page and must NOT be stitched to a same-column table on the
# next page.  Standard reportlab bottom margin is 1 in (72 pt) on a 792 pt LETTER
# page, so a table cut at the page break ends at ~720 pt (≈ 91% down the page).
# Using 0.15 means any table that ends before the bottom 15% (≥ y1 < 673 pt on
# LETTER) is treated as complete.  Only applied when page_height is stored in
# the table's attrs; synthetic tables built in tests are unaffected.
BOTTOM_MARGIN_FRAC = 0.15


def _col_anchors(table: DocNode) -> list[tuple[float, float]]:
    """Return (x0, x1) per logical column.

    Scans every row and picks the one with the most non-covered cells.
    This avoids colspan rows (where one cell spans the full table width)
    misreporting the column structure and preventing a legitimate stitch.
    """
    best: list[tuple[float, float]] = []
    for row in table.children:
        anchors = [
            (c.bbox.x0, c.bbox.x1)
            for c in row.children
            if not c.attrs.get("covered")
        ]
        if len(anchors) > len(best):
            best = anchors
    return best


def _anchors_match(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(a[i][0] - b[i][0]) <= COLUMN_ANCHOR_TOL and abs(a[i][1] - b[i][1]) <= COLUMN_ANCHOR_TOL
               for i in range(len(a)))


def _header_signature(table: DocNode) -> tuple[str, ...]:
    sig = table.attrs.get("header_signature")
    return tuple(sig) if sig else ()


def _first_bbox(node: DocNode) -> BBox:
    return node.bbox if isinstance(node.bbox, BBox) else node.bbox[0]


def _source_extractor(node: DocNode) -> str:
    """Source extractor name, ignoring any ``+stitch`` suffix from a prior merge."""
    return node.provenance.get("extractor", "").removesuffix("+stitch")


def _can_merge(prev: DocNode, nxt: DocNode) -> bool:
    # Stitching is intra-extractor only: legacy fragments merge with other
    # legacy fragments, anchor candidates merge with other anchor candidates.
    # Without this, a legacy fragment ending near a page break could silently
    # merge with an anchor candidate on the next page whose column anchors
    # happen to match within COLUMN_ANCHOR_TOL — a real risk because the
    # tolerance is loose (4 pt) and anchor candidates carry no header
    # signature to fall back on.
    if _source_extractor(prev) != _source_extractor(nxt):
        return False
    if not _anchors_match(_col_anchors(prev), _col_anchors(nxt)):
        return False
    p_page = prev.bbox[-1].page if isinstance(prev.bbox, list) else prev.bbox.page
    n_page = _first_bbox(nxt).page
    if n_page != p_page + 1:
        return False
    # Guard against stitching two independent tables that happen to share column
    # anchors: a table cut by a page break fills near the page bottom; a table
    # that ends well before the bottom is complete and should not be merged.
    page_height: float | None = prev.attrs.get("page_height")
    if page_height:
        prev_last_bbox = prev.bbox[-1] if isinstance(prev.bbox, list) else prev.bbox
        if prev_last_bbox.y1 < page_height * (1.0 - BOTTOM_MARGIN_FRAC):
            return False
    return True


def _merge_two(prev: DocNode, nxt: DocNode) -> DocNode:
    prev_bboxes = prev.bbox if isinstance(prev.bbox, list) else [prev.bbox]
    next_bbox = _first_bbox(nxt)
    rows_next = list(nxt.children)
    if _header_signature(prev) and _header_signature(nxt) == _header_signature(prev):
        rows_next = rows_next[1:]  # drop duplicate header
    # Reindex row indices and ensure each row's attrs.page is set.
    rebuilt_rows: list[DocNode] = []
    for row in list(prev.children) + rows_next:
        new_attrs = dict(row.attrs)
        new_attrs["row_index"] = len(rebuilt_rows)
        new_attrs.setdefault("page", _first_bbox(row).page)
        rebuilt_rows.append(DocNode(
            kind=row.kind, bbox=row.bbox, children=row.children, attrs=new_attrs,
            text=row.text, provenance=row.provenance,
        ))
    merged_attrs = dict(prev.attrs)
    merged_attrs["n_rows"] = len(rebuilt_rows)
    merged_attrs["spans_pages"] = sorted({_first_bbox(r).page for r in rebuilt_rows})
    return DocNode(
        kind="table",
        bbox=prev_bboxes + [next_bbox],
        children=rebuilt_rows,
        attrs=merged_attrs,
        provenance={"extractor": f"{_source_extractor(prev)}+stitch", "stage": "stitch_pages"},
    )


def stitch_tables(tables: list[DocNode]) -> list[DocNode]:
    if not tables:
        return []
    out: list[DocNode] = [tables[0]]
    for t in tables[1:]:
        if _can_merge(out[-1], t):
            out[-1] = _merge_two(out[-1], t)
        else:
            out.append(t)
    return out
