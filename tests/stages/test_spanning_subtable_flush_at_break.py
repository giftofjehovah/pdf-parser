"""Tests for fixture 26: page-spanning outer table whose nested sub-table
is ALSO split across the page break, with the inner halves sitting flush
against the outer's bottom edge on page n and the outer's top edge on
page n+1.

The inner sub-table is logically 5 rows (1 header + 4 data).  Three rows
render on page n (header + first two data rows); two rows render on page
n+1 (last two data rows, no header repeat).  The two halves are placed
in adjacent outer rows with zero vertical padding so each half's
boundary edge coincides with the outer table's page-edge horizontal at
that point.

This exercises the recursive nested-table detection in the worst case:
the cell containing the inner half has a regular grid-cell parent (not a
frame), AND the inner sub-table's bottom edge (page n) / top edge
(page n+1) sits flush against that cell's bottom / top.  An earlier
1-pt edge shrink in :func:`_build_cell` would crop those flush
horizontals away and the inner half would surface with one row missing.
The fix drops the shrink and strips the phantom edge columns that
pdfplumber introduces when the un-shrunk crop exposes the parent cell's
vertical rails.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import BBox, DocNode
from pdf_parser.pipeline import parse

PDF = Path("tests/golden/synthetic/26_spanning_subtable_flush_at_break/source.pdf")

# Vertical-edge tolerance in pt.  ReportLab's GRID stroke width plus
# rendering precision can shift the rendered horizontal by ≤ 1 pt
# relative to the cell-bbox y-bound the parser reconstructs.
FLUSH_TOL = 1.0


def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _tables(root: DocNode) -> list[DocNode]:
    return [n for n in _walk(root) if n.kind == "table"]


def _cell_text(cell: DocNode) -> str:
    return cell.text or ""


def _bbox(n: DocNode) -> BBox:
    return n.bbox if isinstance(n.bbox, BBox) else n.bbox[0]


@pytest.fixture(scope="module")
def tree() -> DocNode:
    return parse(PDF)


@pytest.fixture(scope="module")
def outer(tree) -> DocNode:
    """The single page-spanning outer table.  The stitcher merges the two
    per-page halves into one DocNode whose bbox is a list of per-page
    BBoxes."""
    top_level = [n for n in _walk(tree) if n.kind == "table"]
    spanning = [t for t in top_level if isinstance(t.bbox, list) and len(t.bbox) == 2]
    assert len(spanning) == 1, (
        f"expected 1 spanning outer table, got {len(spanning)}: "
        f"{[t.attrs.get('header_signature') for t in spanning]}"
    )
    return spanning[0]


def _row_at_step(outer: DocNode, step: str) -> DocNode:
    """Find the outer row whose first cell holds ``step`` as text."""
    for row in outer.children:
        if row.kind != "row":
            continue
        cells = [c for c in row.children if c.kind == "cell"]
        if cells and _cell_text(cells[0]) == step:
            return row
    raise AssertionError(f"row with Step={step!r} not found")


def _nested_table(row: DocNode) -> DocNode:
    """Return the nested sub-table living inside the row's Inputs cell."""
    cells = [c for c in row.children if c.kind == "cell"]
    assert len(cells) >= 2, f"row has {len(cells)} cells, expected ≥ 2"
    inputs_cell = cells[1]
    nested = [c for c in inputs_cell.children if c.kind == "table"]
    assert len(nested) == 1, (
        f"Inputs cell of row {_cell_text(cells[0])!r} should hold exactly 1 nested "
        f"table, got {len(nested)}"
    )
    return nested[0]


# ---------------------------------------------------------------------------
# Top-level spanning outer
# ---------------------------------------------------------------------------


def test_two_pages(tree):
    pages = [n for n in _walk(tree) if n.kind == "page"]
    assert len(pages) == 2, f"expected 2 pages, got {len(pages)}"


def test_outer_table_spans_both_pages(outer):
    assert isinstance(outer.bbox, list) and len(outer.bbox) == 2
    assert {b.page for b in outer.bbox} == {0, 1}


def test_outer_step_column_complete(outer):
    """All 34 source data rows survive stitching; no row lost at the page
    break even with the nested sub-table straddling it."""
    steps = []
    for row in outer.children:
        if row.kind != "row":
            continue
        cells = [c for c in row.children if c.kind == "cell"]
        if cells:
            steps.append(_cell_text(cells[0]))
    # First row is the header "Step", followed by 1..34.
    assert steps[0] == "Step"
    assert steps[1:] == [str(i) for i in range(1, 35)]


# ---------------------------------------------------------------------------
# Top half on page n: flush BOTTOM
# ---------------------------------------------------------------------------


def test_top_half_present_on_page_0(outer):
    """Row 28 (last on page n) carries the top-half sub-table."""
    row = _row_at_step(outer, "28")
    sub = _nested_table(row)
    assert _bbox(sub).page == 0


def test_top_half_has_three_rows(outer):
    """The top half of the 5-row inner sub-table renders header + 2 data
    rows on page n.  Pre-fix the last data row ("b / 2") was lost because
    the 1-pt cell shrink stripped the bottom horizontal that sub_top's
    last row shares with the outer's bottom edge on page n."""
    sub = _nested_table(_row_at_step(outer, "28"))
    rows = [r for r in sub.children if r.kind == "row"]
    assert len(rows) == 3, f"top half should have 3 rows, got {len(rows)}"


def test_top_half_data_preserved(outer):
    sub = _nested_table(_row_at_step(outer, "28"))
    texts = {_cell_text(c) for row in sub.children for c in row.children if c.kind == "cell"}
    for expected in ("sub-H1", "sub-H2", "a", "1", "b", "2"):
        assert expected in texts, f"'{expected}' missing from top half cells: {sorted(texts)}"


def test_top_half_bottom_flush_with_outer_on_page_n(outer):
    """Sub-table bottom on page n must equal the outer's bottom on page n.
    Regression check for the cell-shrink stripping the flush bottom edge."""
    sub = _nested_table(_row_at_step(outer, "28"))
    sub_b = _bbox(sub)
    # Find the page-0 piece of the outer's bbox.
    outer_p0 = next(b for b in outer.bbox if b.page == 0)
    assert abs(sub_b.y1 - outer_p0.y1) <= FLUSH_TOL, (
        f"top-half.bottom {sub_b.y1} not flush with outer.bottom on page 0 ({outer_p0.y1})"
    )


def test_top_half_top_not_flush(outer):
    """The top half should NOT also be flush at its top — there's a header
    row of the outer table (and other data rows) above it on page n.  Pins
    that the flush condition tested above is asymmetric per the fixture."""
    sub = _nested_table(_row_at_step(outer, "28"))
    sub_b = _bbox(sub)
    outer_p0 = next(b for b in outer.bbox if b.page == 0)
    assert sub_b.y0 > outer_p0.y0 + FLUSH_TOL, (
        f"top-half.top {sub_b.y0} unexpectedly flush with outer.top on page 0"
    )


# ---------------------------------------------------------------------------
# Bottom half on page n+1: flush TOP
# ---------------------------------------------------------------------------


def test_bottom_half_present_on_page_1(outer):
    row = _row_at_step(outer, "29")
    sub = _nested_table(row)
    assert _bbox(sub).page == 1


def test_bottom_half_has_two_rows(outer):
    """The bottom half is just the last two data rows ('c / 3', 'd / 4').
    Pre-fix the first data row ("c / 3") was lost because the 1-pt cell
    shrink stripped the top horizontal that sub_bottom's first row shares
    with the outer's top edge on page n+1."""
    sub = _nested_table(_row_at_step(outer, "29"))
    rows = [r for r in sub.children if r.kind == "row"]
    assert len(rows) == 2, f"bottom half should have 2 rows, got {len(rows)}"


def test_bottom_half_data_preserved(outer):
    sub = _nested_table(_row_at_step(outer, "29"))
    texts = {_cell_text(c) for row in sub.children for c in row.children if c.kind == "cell"}
    for expected in ("c", "3", "d", "4"):
        assert expected in texts, f"'{expected}' missing from bottom half cells: {sorted(texts)}"


def test_bottom_half_top_flush_with_outer_on_page_n_plus_1(outer):
    sub = _nested_table(_row_at_step(outer, "29"))
    sub_b = _bbox(sub)
    outer_p1 = next(b for b in outer.bbox if b.page == 1)
    assert abs(sub_b.y0 - outer_p1.y0) <= FLUSH_TOL, (
        f"bottom-half.top {sub_b.y0} not flush with outer.top on page 1 ({outer_p1.y0})"
    )


def test_bottom_half_bottom_not_flush(outer):
    """Several plain outer rows render below the bottom half on page n+1;
    its own bottom edge must NOT be flush with the outer's bottom on
    page n+1."""
    sub = _nested_table(_row_at_step(outer, "29"))
    sub_b = _bbox(sub)
    outer_p1 = next(b for b in outer.bbox if b.page == 1)
    assert sub_b.y1 < outer_p1.y1 - FLUSH_TOL, (
        f"bottom-half.bottom {sub_b.y1} unexpectedly flush with outer.bottom on page 1"
    )


# ---------------------------------------------------------------------------
# Cross-page continuity of the inner sub-table
# ---------------------------------------------------------------------------


def test_inner_halves_share_column_anchors(outer):
    """The two halves of the logically-continuous 5-row sub-table must
    agree on their inner column x-bounds within FLUSH_TOL — this is the
    signal a downstream nested-stitch pass would use to recognise them
    as one continuing sub-table."""
    top = _nested_table(_row_at_step(outer, "28"))
    bot = _nested_table(_row_at_step(outer, "29"))
    top_b = _bbox(top)
    bot_b = _bbox(bot)
    assert abs(top_b.x0 - bot_b.x0) <= FLUSH_TOL, (
        f"left anchor drift: top={top_b.x0}, bot={bot_b.x0}"
    )
    assert abs(top_b.x1 - bot_b.x1) <= FLUSH_TOL, (
        f"right anchor drift: top={top_b.x1}, bot={bot_b.x1}"
    )


def test_inner_halves_total_five_data_rows(outer):
    """3 + 2 = 5 rows of inner sub-table material survive across the page
    break (sub-H1/sub-H2 header + 4 data rows a-d, 1-4)."""
    top = _nested_table(_row_at_step(outer, "28"))
    bot = _nested_table(_row_at_step(outer, "29"))
    top_rows = [r for r in top.children if r.kind == "row"]
    bot_rows = [r for r in bot.children if r.kind == "row"]
    assert len(top_rows) + len(bot_rows) == 5


def test_inner_halves_land_in_adjacent_merged_rows(outer):
    """The two halves must sit in adjacent outer rows (Step 28 → Step 29)
    with no intervening plain outer row between them, even though the
    page break falls between them.  Pins the page-edge flush layout the
    fixture was authored for."""
    rows = [r for r in outer.children if r.kind == "row"]
    # Locate the indices of rows whose first cell is "28" / "29".
    step_to_idx = {
        _cell_text([c for c in r.children if c.kind == "cell"][0]): i
        for i, r in enumerate(rows)
    }
    assert step_to_idx["29"] == step_to_idx["28"] + 1, (
        f"halves must be in adjacent outer rows, got "
        f"row({step_to_idx['28']!r}) then row({step_to_idx['29']!r})"
    )


# ---------------------------------------------------------------------------
# Total tables
# ---------------------------------------------------------------------------


def test_three_tables_total(tree):
    """Outer + top half + bottom half = 3 table nodes total.  Pre-fix only
    a single mega-table per page made it through (no nested), or one half
    lost its boundary row and showed as a degenerate table."""
    assert len(_tables(tree)) == 3
