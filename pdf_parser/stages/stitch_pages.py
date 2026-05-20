"""Stage 5: merge tables that continue across pages via column-anchor + header match."""

from __future__ import annotations

from pdf_parser.model import BBox, DocNode

COLUMN_ANCHOR_TOL = 4.0   # points
BOTTOM_MARGIN_FRAC = 0.05  # within 5% of page height counts as "near bottom"
TOP_MARGIN_FRAC = 0.10     # within 10% of page height counts as "near top"


def _col_anchors(table: DocNode) -> list[tuple[float, float]]:
    if not table.children or not table.children[0].children:
        return []
    first_row = table.children[0]
    return [(cell.bbox.x0, cell.bbox.x1) for cell in first_row.children]


def _anchors_match(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(a[i][0] - b[i][0]) <= COLUMN_ANCHOR_TOL and abs(a[i][1] - b[i][1]) <= COLUMN_ANCHOR_TOL
               for i in range(len(a)))


def _header_signature(table: DocNode) -> tuple[str, ...]:
    sig = table.attrs.get("header_signature")
    return tuple(sig) if sig else ()


def _first_bbox(table: DocNode) -> BBox:
    return table.bbox if isinstance(table.bbox, BBox) else table.bbox[0]


def _can_merge(prev: DocNode, nxt: DocNode) -> bool:
    if not _anchors_match(_col_anchors(prev), _col_anchors(nxt)):
        return False
    p_page = _first_bbox(prev).page
    n_page = _first_bbox(nxt).page
    if n_page != p_page + 1:
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
    base = len(prev.children)
    for row in list(prev.children) + rows_next:
        new_attrs = dict(row.attrs)
        new_attrs["row_index"] = len(rebuilt_rows)
        new_attrs.setdefault("page", _first_bbox(row).page if isinstance(row.bbox, BBox) else _first_bbox(row).page)
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
        provenance={"extractor": "pdfplumber+stitch", "stage": "stitch_pages"},
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
