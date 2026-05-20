"""Layer 1 structural invariants. Each check returns list of human-readable error strings."""

from __future__ import annotations

from pdf_parser.model import MAX_DEPTH, BBox, DocNode


def _walk(n: DocNode, depth=0):
    yield depth, n
    for c in n.children:
        yield from _walk(c, depth + 1)


def _first_bbox(n: DocNode) -> BBox:
    return n.bbox if isinstance(n.bbox, BBox) else n.bbox[0]


def check_well_formedness(tree: DocNode) -> list[str]:
    errs: list[str] = []
    seen_ids: set[str] = set()
    for depth, n in _walk(tree):
        if depth > MAX_DEPTH + 2:  # +2 for document/page wrappers
            errs.append(f"node {n.id} ({n.kind}) exceeds depth {MAX_DEPTH}")
        if n.id in seen_ids:
            errs.append(f"duplicate id {n.id} on {n.kind}")
        seen_ids.add(n.id)
        if n.kind == "table" and any(c.kind != "row" for c in n.children):
            errs.append(f"table {n.id} has non-row child")
        if n.kind == "row" and any(c.kind != "cell" for c in n.children):
            errs.append(f"row {n.id} has non-cell child")
    return errs


def check_table_shape(tree: DocNode) -> list[str]:
    errs: list[str] = []
    for _, n in _walk(tree):
        if n.kind != "table" or not n.children:
            continue
        row_widths = [len(row.children) for row in n.children]
        # Skip rows whose cells declare a colspan; widths of those rows may legitimately differ.
        unique = {
            w for i, w in enumerate(row_widths)
            if not any(c.attrs.get("colspan") for c in n.children[i].children)
        }
        if len(unique) > 1:
            errs.append(f"table {n.id} has inconsistent row widths {row_widths}")
    return errs


def check_reading_order(tree: DocNode) -> list[str]:
    errs: list[str] = []
    for _, n in _walk(tree):
        if n.kind != "page":
            continue
        ys = [_first_bbox(c).y0 for c in n.children]
        if ys != sorted(ys):
            errs.append(f"page {n.attrs.get('page_index')} children not in reading order")
    return errs


def check_cross_page_integrity(tree: DocNode) -> list[str]:
    errs: list[str] = []
    for _, n in _walk(tree):
        if n.kind != "table" or not isinstance(n.bbox, list):
            continue
        pages = [b.page for b in n.bbox]
        if pages != sorted(pages):
            errs.append(f"table {n.id} bboxes not in page order: {pages}")
        row_indices = [c.attrs.get("row_index") for c in n.children]
        if row_indices != list(range(len(row_indices))):
            errs.append(f"table {n.id} rows not continuously indexed")
    return errs
