"""Tests for fixture 25: sub-tables flush with the outer frame on ONE
vertical edge while inset horizontally from the outer's rails.

Two pages; each page hosts its own independent closed outer table:

  Page 1 — outer table cell holds [paragraph, sub_a, paragraph, sub_b].
           Sub_b's bottom edge sits flush with the outer's bottom edge.
           Sub_a is centred vertically and is not flush to anything.

  Page 2 — outer table cell holds [sub_c, paragraph, sub_d, paragraph].
           Sub_c's top edge sits flush with the outer's top edge.
           Sub_d is centred vertically and is not flush to anything.

This exercises the recursive-detection edge case where the cell's 1 pt
shrink would crop away a flush sub-table's outermost horizontal — the
fix is to skip the shrink for frame content cells (cells whose parent
region has no internal grid lines).  Without the skip, page 1's sub_b
loses its bottom data row and page 2's sub_c loses its top header row.

The two outers are independent (no cross-page stitching) — they must
surface as two separate top-level tables, one per page.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import BBox, DocNode
from pdf_parser.pipeline import parse

PDF = Path("tests/golden/synthetic/25_subtable_flush_outer_vertical_only/source.pdf")

# Vertical-edge tolerance in pt.  ReportLab places sub-tables flush to
# the cell content area (no padding) but the outer's BOX line has a
# half-stroke-width offset (0.75 / 2 ≈ 0.4 pt) that can shift the cell
# bbox by ≤ 1 pt relative to the rendered horizontal.  1 pt is plenty.
FLUSH_TOL = 1.0


def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _tables(root: DocNode) -> list[DocNode]:
    return [n for n in _walk(root) if n.kind == "table"]


def _header_texts(table: DocNode) -> list[str]:
    rows = [c for c in table.children if c.kind == "row"]
    if not rows:
        return []
    return [c.text or "" for c in rows[0].children if c.kind == "cell"]


def _cell_texts(table: DocNode) -> set[str]:
    return {c.text for r in table.children for c in r.children if c.text}


@pytest.fixture(scope="module")
def tree() -> DocNode:
    return parse(PDF)


@pytest.fixture(scope="module")
def page1_outer(tree) -> DocNode:
    page = tree.children[0]
    tables = [n for n in page.children if n.kind == "table"]
    assert len(tables) == 1, f"page 1 must have exactly one top-level table, got {len(tables)}"
    return tables[0]


@pytest.fixture(scope="module")
def page2_outer(tree) -> DocNode:
    page = tree.children[1]
    tables = [n for n in page.children if n.kind == "table"]
    assert len(tables) == 1, f"page 2 must have exactly one top-level table, got {len(tables)}"
    return tables[0]


# ---------------------------------------------------------------------------
# Top-level shape
# ---------------------------------------------------------------------------


def test_two_pages(tree):
    pages = [n for n in _walk(tree) if n.kind == "page"]
    assert len(pages) == 2, f"expected 2 pages, got {len(pages)}"


def test_one_outer_per_page(page1_outer, page2_outer):
    """Each page hosts its own independent outer frame; the two are
    NOT eligible for cross-page stitching."""
    p1 = page1_outer.bbox if isinstance(page1_outer.bbox, BBox) else page1_outer.bbox[0]
    p2 = page2_outer.bbox if isinstance(page2_outer.bbox, BBox) else page2_outer.bbox[0]
    assert p1.page == 0
    assert p2.page == 1


def test_total_six_tables(tree):
    """Outer x 2 + sub_a + sub_b + sub_c + sub_d = 6 table nodes total.
    Pre-fix on page 1, sub_b lost rows due to the 1pt cell shrink eating
    its flush bottom horizontal; the lost row surfaced as a stray
    paragraph and the table count stayed at 6 but the structure was
    wrong.  This count is a coarse upper bound; sibling tests pin row
    contents."""
    tables = _tables(tree)
    assert len(tables) == 6, (
        f"expected 6 tables (2 outer + 4 nested), got {len(tables)}"
    )


# ---------------------------------------------------------------------------
# Page 1: sub_b flush to BOTTOM, sub_a not flush.
# ---------------------------------------------------------------------------


def test_page1_outer_has_two_nested(page1_outer):
    nested = [t for t in _tables(page1_outer) if t is not page1_outer]
    assert len(nested) == 2, (
        f"page 1 outer must contain exactly 2 nested sub-tables, got {len(nested)}"
    )


def test_page1_sub_b_bottom_flush_with_outer(page1_outer):
    """sub_b.bbox.y1 must equal outer.bbox.y1 within FLUSH_TOL — this is
    the regression check for the 1pt shrink stripping sub_b's bottom
    edge.  Pre-fix sub_b.bbox.y1 was 296 vs outer.y1=314 (an 18pt gap)
    because the last row "Feb / $700" never made it into the table."""
    sub_b = next(
        t for t in _tables(page1_outer)
        if tuple(_header_texts(t)) == ("Month", "Sales")
    )
    sub_b_bbox = sub_b.bbox if isinstance(sub_b.bbox, BBox) else sub_b.bbox[0]
    outer_bbox = page1_outer.bbox if isinstance(page1_outer.bbox, BBox) else page1_outer.bbox[0]
    assert abs(sub_b_bbox.y1 - outer_bbox.y1) <= FLUSH_TOL, (
        f"sub_b.bottom {sub_b_bbox.y1} not flush with outer.bottom {outer_bbox.y1}"
    )


def test_page1_sub_b_has_all_three_rows(page1_outer):
    """Direct content check: the bottom-flush sub-table must keep all
    three rows of source data.  Pre-fix the "Feb / $700" row was lost
    because the cell-shrink cropped the sub-table's bottom horizontal."""
    sub_b = next(
        t for t in _tables(page1_outer)
        if tuple(_header_texts(t)) == ("Month", "Sales")
    )
    texts = _cell_texts(sub_b)
    for expected in ("Month", "Sales", "Jan", "$500", "Feb", "$700"):
        assert expected in texts, (
            f"'{expected}' missing from sub_b cells: {sorted(texts)}"
        )


def test_page1_sub_a_not_flush_to_outer_edges(page1_outer):
    """sub_a is centred vertically with paragraphs on either side; its
    top and bottom MUST be strictly inside the outer frame."""
    sub_a = next(
        t for t in _tables(page1_outer)
        if tuple(_header_texts(t)) == ("Item", "Qty")
    )
    sub_a_bbox = sub_a.bbox if isinstance(sub_a.bbox, BBox) else sub_a.bbox[0]
    outer_bbox = page1_outer.bbox if isinstance(page1_outer.bbox, BBox) else page1_outer.bbox[0]
    assert sub_a_bbox.y0 > outer_bbox.y0 + FLUSH_TOL, "sub_a should not be top-flush"
    assert sub_a_bbox.y1 < outer_bbox.y1 - FLUSH_TOL, "sub_a should not be bottom-flush"


def test_page1_sub_tables_horizontally_inset(page1_outer):
    """All nested sub-tables sit inside the outer's horizontal padding —
    sub.left > outer.left and sub.right < outer.right.  This is the
    'horizontal NOT flush' axis the fixture exercises in contrast with
    fixture 24."""
    outer_bbox = page1_outer.bbox if isinstance(page1_outer.bbox, BBox) else page1_outer.bbox[0]
    for sub in _tables(page1_outer):
        if sub is page1_outer:
            continue
        sb = sub.bbox if isinstance(sub.bbox, BBox) else sub.bbox[0]
        assert sb.x0 > outer_bbox.x0 + FLUSH_TOL, (
            f"sub-table {_header_texts(sub)!r} not horizontally inset on left"
        )
        assert sb.x1 < outer_bbox.x1 - FLUSH_TOL, (
            f"sub-table {_header_texts(sub)!r} not horizontally inset on right"
        )


def test_page1_intro_paragraph_preserved(page1_outer):
    """The intro paragraph at the top of the outer cell ("NOTE-TOP")
    must survive as one paragraph node."""
    cell = page1_outer.children[0].children[0]
    paragraphs = [c for c in cell.children if c.kind == "paragraph"]
    joined = " ".join(p.text or "" for p in paragraphs)
    assert "NOTE-TOP" in joined, f"intro paragraph missing: {joined!r}"
    assert "top of the page-1 outer cell" in joined


def test_page1_between_paragraph_preserved(page1_outer):
    cell = page1_outer.children[0].children[0]
    paragraphs = [c for c in cell.children if c.kind == "paragraph"]
    joined = " ".join(p.text or "" for p in paragraphs)
    assert "NOTE-MID1" in joined, f"between paragraph missing: {joined!r}"


# ---------------------------------------------------------------------------
# Page 2: sub_c flush to TOP, sub_d not flush.
# ---------------------------------------------------------------------------


def test_page2_outer_has_two_nested(page2_outer):
    nested = [t for t in _tables(page2_outer) if t is not page2_outer]
    assert len(nested) == 2, (
        f"page 2 outer must contain exactly 2 nested sub-tables, got {len(nested)}"
    )


def test_page2_sub_c_top_flush_with_outer(page2_outer):
    """sub_c.bbox.y0 must equal outer.bbox.y0 within FLUSH_TOL — the
    symmetric regression check for the cell shrink stripping sub_c's
    top edge (which previously caused sub_c to be detected with a
    bbox starting at outer.y0+1)."""
    sub_c = next(
        t for t in _tables(page2_outer)
        if tuple(_header_texts(t)) == ("Step", "Owner")
    )
    sub_c_bbox = sub_c.bbox if isinstance(sub_c.bbox, BBox) else sub_c.bbox[0]
    outer_bbox = page2_outer.bbox if isinstance(page2_outer.bbox, BBox) else page2_outer.bbox[0]
    assert abs(sub_c_bbox.y0 - outer_bbox.y0) <= FLUSH_TOL, (
        f"sub_c.top {sub_c_bbox.y0} not flush with outer.top {outer_bbox.y0}"
    )


def test_page2_sub_c_has_all_three_rows(page2_outer):
    sub_c = next(
        t for t in _tables(page2_outer)
        if tuple(_header_texts(t)) == ("Step", "Owner")
    )
    texts = _cell_texts(sub_c)
    for expected in ("Step", "Owner", "1", "Alice", "2", "Bob"):
        assert expected in texts, (
            f"'{expected}' missing from sub_c cells: {sorted(texts)}"
        )


def test_page2_sub_d_not_flush_to_outer_edges(page2_outer):
    sub_d = next(
        t for t in _tables(page2_outer)
        if tuple(_header_texts(t)) == ("City", "Zone")
    )
    sub_d_bbox = sub_d.bbox if isinstance(sub_d.bbox, BBox) else sub_d.bbox[0]
    outer_bbox = page2_outer.bbox if isinstance(page2_outer.bbox, BBox) else page2_outer.bbox[0]
    assert sub_d_bbox.y0 > outer_bbox.y0 + FLUSH_TOL, "sub_d should not be top-flush"
    assert sub_d_bbox.y1 < outer_bbox.y1 - FLUSH_TOL, "sub_d should not be bottom-flush"


def test_page2_outro_paragraph_preserved_as_single_paragraph(page2_outer):
    """Regression guard for the cell-bullet false positive: pre-fix, the
    NOTE-BOT paragraph wrapped to a final visual line beginning with
    "outer" (a word, not a bullet) which got classified as a list_item
    and broken off from the rest of the paragraph because "o" was in
    the cell-level bullet set without a trailing-space guard.  After
    tightening the check, the paragraph stays whole."""
    cell = page2_outer.children[0].children[0]
    paragraphs = [c for c in cell.children if c.kind == "paragraph"]
    list_items = [c for c in cell.children if c.kind == "list_item"]
    note_bot = [p for p in paragraphs if "NOTE-BOT" in (p.text or "")]
    assert note_bot, "NOTE-BOT paragraph missing from page 2 outer cell"
    full_text = " ".join(p.text or "" for p in note_bot)
    assert "outer frame's top edge" in full_text, (
        f"NOTE-BOT paragraph fragmented; final clause missing from text: {full_text!r}"
    )
    assert not list_items, (
        f"no list_item nodes expected on page 2 outer cell, got "
        f"{[c.text for c in list_items]!r}"
    )


def test_page2_sub_tables_horizontally_inset(page2_outer):
    outer_bbox = page2_outer.bbox if isinstance(page2_outer.bbox, BBox) else page2_outer.bbox[0]
    for sub in _tables(page2_outer):
        if sub is page2_outer:
            continue
        sb = sub.bbox if isinstance(sub.bbox, BBox) else sub.bbox[0]
        assert sb.x0 > outer_bbox.x0 + FLUSH_TOL, (
            f"sub-table {_header_texts(sub)!r} not horizontally inset on left"
        )
        assert sb.x1 < outer_bbox.x1 - FLUSH_TOL, (
            f"sub-table {_header_texts(sub)!r} not horizontally inset on right"
        )


# ---------------------------------------------------------------------------
# Cross-page invariants
# ---------------------------------------------------------------------------


def test_two_outers_are_independent(page1_outer, page2_outer):
    """The two outers must NOT be stitched into one cross-page table —
    they are distinct closed frames on different pages with no shared
    column anchors."""
    assert page1_outer is not page2_outer
    # Each outer's bbox is a single BBox (not a list-of-bboxes that the
    # stitcher would produce on a cross-page merge).
    assert isinstance(page1_outer.bbox, BBox), (
        f"page 1 outer bbox should be single BBox (no stitch), got {type(page1_outer.bbox).__name__}"
    )
    assert isinstance(page2_outer.bbox, BBox), (
        f"page 2 outer bbox should be single BBox (no stitch), got {type(page2_outer.bbox).__name__}"
    )
