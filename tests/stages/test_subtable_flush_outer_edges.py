"""Tests for fixture 24: nested sub-tables flush with the outer frame's
top and bottom edges.

This is the worst-case manifestation of the page-edge pattern: a multi-page
outer table cut at a page boundary leaves the first/last inner sub-table on
that page slice with its top/bottom edge sitting at the same y-coordinate
(and often the same x-rails) as the outer frame.  pdfplumber's line strategy
fuses the shared edges into a single "mega-table" grid that loses the outer
and merges the sub-tables column-for-column unless the parser decomposes the
mega-table back into an outer-frame + per-cluster sub-tables.

Before the fix, the fixture parsed as a single 7-row × 2-column table with
the between-paragraph text sliced along the inner column divider and the
outer frame missing entirely from the tree.  After the fix, parsing
recovers exactly the structure the source PDF was authored with.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import BBox, DocNode
from pdf_parser.pipeline import parse

PDF = Path("tests/golden/synthetic/24_subtable_flush_outer_edges/source.pdf")


def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _tables(root: DocNode) -> list[DocNode]:
    return [n for n in _walk(root) if n.kind == "table"]


def _all_texts(root: DocNode) -> list[str]:
    return [n.text for n in _walk(root) if n.text]


def _header_texts(table: DocNode) -> list[str]:
    rows = [c for c in table.children if c.kind == "row"]
    if not rows:
        return []
    return [c.text or "" for c in rows[0].children if c.kind == "cell"]


@pytest.fixture(scope="module")
def tree() -> DocNode:
    return parse(PDF)


# ---------------------------------------------------------------------------
# Structural assertions: outer frame + 2 nested sub-tables.
# ---------------------------------------------------------------------------


def test_exactly_three_tables(tree):
    """Outer frame + sub-table A + sub-table B.  Before the fix the parser
    only saw one giant 7-row table; the outer and one of the sub-tables
    were swallowed."""
    assert len(_tables(tree)) == 3


def test_one_top_level_table(tree):
    """Only the outer frame is a child of the page.  Both sub-tables must
    nest INSIDE the outer's content cell, not surface as siblings of the
    outer (which would happen if dominance-filtering ran on the
    pre-decomposition mega-table)."""
    page = tree.children[0]
    top_tables = [n for n in page.children if n.kind == "table"]
    assert len(top_tables) == 1, (
        f"expected one top-level outer table, got {len(top_tables)}"
    )


def test_outer_table_has_one_row_one_cell(tree):
    """The synthesised frame is a 1×1 content shell — its single cell
    holds the nested sub-tables and the between-paragraph as children."""
    page = tree.children[0]
    outer = next(n for n in page.children if n.kind == "table")
    rows = [r for r in outer.children if r.kind == "row"]
    assert len(rows) == 1, f"outer frame must have 1 row, got {len(rows)}"
    cells = [c for c in rows[0].children if c.kind == "cell"]
    assert len(cells) == 1, f"outer frame must have 1 cell, got {len(cells)}"


def test_sub_tables_are_nested(tree):
    """sub_a (Item/Qty) and sub_b (Month/Sales) live inside the outer
    frame's content cell, not as top-level page children."""
    page = tree.children[0]
    outer = next(n for n in page.children if n.kind == "table")
    nested_headers = {tuple(_header_texts(t)) for t in _tables(outer) if t is not outer}
    assert ("Item", "Qty") in nested_headers, (
        f"sub-table A must nest inside the outer frame, got {nested_headers!r}"
    )
    assert ("Month", "Sales") in nested_headers, (
        f"sub-table B must nest inside the outer frame, got {nested_headers!r}"
    )


def test_outer_bbox_encloses_both_sub_tables(tree):
    """The outer frame's bbox must contain both inner sub-tables; this
    proves the decomposer kept the original closed-BOX outline rather
    than collapsing the bbox to just one cluster."""
    page = tree.children[0]
    outer = next(n for n in page.children if n.kind == "table")
    assert isinstance(outer.bbox, BBox), "outer frame bbox should be a single BBox"
    inner_tables = [t for t in _tables(outer) if t is not outer]
    for inner in inner_tables:
        ib = inner.bbox if isinstance(inner.bbox, BBox) else inner.bbox[0]
        assert (outer.bbox.x0 <= ib.x0 + 1
                and outer.bbox.y0 <= ib.y0 + 1
                and outer.bbox.x1 >= ib.x1 - 1
                and outer.bbox.y1 >= ib.y1 - 1), (
            f"inner table {ib} not enclosed by outer {outer.bbox}"
        )


# ---------------------------------------------------------------------------
# Content preservation: sub-table data + between-paragraph text.
# ---------------------------------------------------------------------------


def test_sub_table_a_data_preserved(tree):
    """Sub-table A (Item/Qty) data cells must round-trip cleanly.  The
    pre-fix merged grid produced "Widget A" and "10" as separate cells in
    a 2-column flat table, so these texts WOULD survive — but they would
    sit at the wrong tree depth.  This test pins that they are present
    AS cells of the Item/Qty table."""
    page = tree.children[0]
    outer = next(n for n in page.children if n.kind == "table")
    sub_a = next(
        t for t in _tables(outer)
        if tuple(_header_texts(t)) == ("Item", "Qty")
    )
    cell_texts = {c.text for r in sub_a.children for c in r.children if c.text}
    for expected in ("Item", "Qty", "Widget A", "10", "Widget B", "5"):
        assert expected in cell_texts, (
            f"'{expected}' missing from sub-table A cells: {sorted(cell_texts)}"
        )


def test_sub_table_b_data_preserved(tree):
    page = tree.children[0]
    outer = next(n for n in page.children if n.kind == "table")
    sub_b = next(
        t for t in _tables(outer)
        if tuple(_header_texts(t)) == ("Month", "Sales")
    )
    cell_texts = {c.text for r in sub_b.children for c in r.children if c.text}
    for expected in ("Month", "Sales", "Jan", "$500", "Feb", "$700"):
        assert expected in cell_texts, (
            f"'{expected}' missing from sub-table B cells: {sorted(cell_texts)}"
        )


def test_between_paragraph_preserved(tree):
    """The "NOTE: ..." paragraph between sub_a and sub_b must survive as
    a paragraph node inside the outer cell.  Pre-fix the text was
    column-binned into per-line cell fragments of the mega-table."""
    page = tree.children[0]
    outer = next(n for n in page.children if n.kind == "table")
    cell = outer.children[0].children[0]
    paragraphs = [c for c in cell.children if c.kind == "paragraph"]
    assert paragraphs, "no paragraph nodes inside the outer cell"
    joined = " ".join(p.text or "" for p in paragraphs)
    for phrase in (
        "NOTE:",
        "between two sub-tables",
        "flush against the outer frame",
        "must survive parsing",
    ):
        assert phrase in joined, f"phrase {phrase!r} missing from between-paragraph: {joined!r}"


def test_between_paragraph_sits_between_sub_tables(tree):
    """Inside the outer cell, the children must order as
    [sub_a table, paragraph(s), sub_b table] — the same visual order as
    the source PDF."""
    page = tree.children[0]
    outer = next(n for n in page.children if n.kind == "table")
    cell = outer.children[0].children[0]
    kinds = [c.kind for c in cell.children]
    # Pluck the positions of the two tables and at least one paragraph.
    table_idx = [i for i, k in enumerate(kinds) if k == "table"]
    para_idx = [i for i, k in enumerate(kinds) if k == "paragraph"]
    assert len(table_idx) == 2, f"expected 2 nested tables in cell, got kinds={kinds!r}"
    assert para_idx, f"expected ≥1 paragraph between the nested tables, got kinds={kinds!r}"
    assert table_idx[0] < min(para_idx) < table_idx[1], (
        f"paragraph must appear between the two nested tables, got order={kinds!r}"
    )


def test_no_paragraph_text_leaks_into_sub_table_cells(tree):
    """A regression guard: the "NOTE:" phrase must NOT appear inside any
    sub-table cell.  Before the fix, the words were word-binned into
    sub_a / sub_b column cells alongside the data."""
    page = tree.children[0]
    outer = next(n for n in page.children if n.kind == "table")
    for sub in _tables(outer):
        if sub is outer:
            continue
        for row in sub.children:
            for c in row.children:
                if c.text:
                    assert "NOTE:" not in c.text, (
                        f"between-paragraph text leaked into nested table cell: {c.text!r}"
                    )


# ---------------------------------------------------------------------------
# Top-level invariants.
# ---------------------------------------------------------------------------


def test_single_page(tree):
    pages = [n for n in _walk(tree) if n.kind == "page"]
    assert len(pages) == 1


def test_heading_preserved(tree):
    texts = _all_texts(tree)
    assert any("Sub-Tables Flush With Outer Frame Edges" in t for t in texts), (
        "fixture heading missing from output"
    )
