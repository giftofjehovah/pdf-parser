"""Tests for fixture 21: vertical merge via invisible (background-coloured) row
separators.

The PDF draws a full grid in black and then overdraws two row separators in
column 0 with white.  Visually, the three covered rows in column 0 collapse
into one merged cell containing three lines of text; the PDF data, however,
still encodes three split rows.  The parser must subtract the background-
coloured stroke overdraws from pdfplumber's edge set (see
``pdf_parser.stages.detect_tables._visible_edges``) so the merged cell is
honoured downstream as a rowspan-3 cell with two ``covered`` neighbours.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import DocNode
from pdf_parser.pipeline import parse
from pdf_parser.stages.detect_tables import (
    _interval_subtract,
    _is_background_color,
    _visible_edges,
)

import pdfplumber


# ---------------------------------------------------------------------------
# Helpers (local; mirror the conventions in test_ruled_header_tables.py).
# ---------------------------------------------------------------------------

def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _tables(node: DocNode) -> list[DocNode]:
    return [n for n in _walk(node) if n.kind == "table"]


def _rows(table: DocNode) -> list[DocNode]:
    return [c for c in table.children if c.kind == "row"]


def _cells(row: DocNode) -> list[DocNode]:
    return [c for c in row.children if c.kind == "cell"]


def _row_texts(row: DocNode) -> list[str]:
    return [(c.text or "") for c in _cells(row)]


def _grid(table: DocNode) -> list[list[str]]:
    return [_row_texts(r) for r in _rows(table)]


# ---------------------------------------------------------------------------
# Unit tests on the helpers (do not require the fixture PDF).
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("color,expected", [
    ((1, 1, 1),     True),    # pure white RGB
    ((0.96, 0.97, 0.98), True),  # near-white
    ((0, 0, 0),     False),   # black RGB
    ((0.5, 0.5, 0.5), False), # mid-grey
    ((1,),          True),    # white in 1-channel grey
    (1.0,           True),    # scalar grey at 1.0
    (0.0,           False),   # scalar black
    ((0, 0, 0, 0),  True),    # CMYK white
    ((1, 1, 1, 1),  False),   # CMYK "deep" — not background
    (None,          False),   # default-to-black per PDF spec
])
def test_is_background_color(color, expected):
    assert _is_background_color(color) is expected


def test_interval_subtract_carves_hole_in_middle():
    assert _interval_subtract([(0.0, 10.0)], [(3.0, 5.0)]) == [(0.0, 3.0), (5.0, 10.0)]


def test_interval_subtract_no_holes_returns_normalized_base():
    assert _interval_subtract([(2.0, 5.0), (10.0, 1.0)], []) == [(2.0, 5.0), (1.0, 10.0)]


def test_interval_subtract_overlapping_holes_are_merged_first():
    assert _interval_subtract([(0.0, 10.0)], [(2.0, 4.0), (3.0, 6.0)]) == [
        (0.0, 2.0), (6.0, 10.0),
    ]


def test_interval_subtract_hole_covers_base_returns_empty():
    assert _interval_subtract([(2.0, 5.0)], [(0.0, 10.0)]) == []


def test_interval_subtract_hole_outside_base_is_noop():
    assert _interval_subtract([(2.0, 5.0)], [(10.0, 20.0)]) == [(2.0, 5.0)]


# ---------------------------------------------------------------------------
# End-to-end on fixture 21.
# ---------------------------------------------------------------------------

PDF_21 = Path("tests/golden/synthetic/21_vertical_merge_invisible_lines/source.pdf")


def test_21_visible_edges_detects_overdraws_and_subtracts_them():
    """At the detection boundary, the white col-0 segments are present in the
    raw line list and the helper marks the page as overdrawn.  The surviving
    horizontal edge segments must omit the col-0 sub-segment for the two
    overdrawn rows while preserving the col-1..-3 portions."""
    with pdfplumber.open(str(PDF_21)) as pdf:
        page = pdf.pages[0]
        h_vis, v_vis, had = _visible_edges(page)
    assert had, "fixture 21 must contain at least one background-coloured line"
    # Two rows (Pacific→Northwest, Northwest→Division) have their col-0
    # segment overdrawn.  Visible h-edges at those y-values must start at
    # x≈266 (col-1 left edge) — never at x≈166 (col-0 left edge).
    overdrawn_ys = {224.0, 244.0}
    for ln in h_vis:
        if round(ln["top"], 1) in overdrawn_ys:
            assert ln["x0"] > 200.0, (
                f"horizontal edge at y={ln['top']} should start past col-0, "
                f"got x0={ln['x0']}"
            )
    # Every other detected y must still produce at least one full-width edge.
    assert any(ln["top"] == 184.0 for ln in h_vis), "table top must survive"
    assert any(ln["top"] == 284.0 for ln in h_vis), "table bottom must survive"
    # Vertical lines are not overdrawn, so all five outer columns persist.
    xs = sorted({round(ln["x0"], 1) for ln in v_vis})
    assert xs == [166.0, 266.0, 326.0, 386.0, 446.0]


def test_21_single_table_detected():
    tree = parse(PDF_21)
    tables = _tables(tree)
    assert len(tables) == 1, f"expected 1 table, got {len(tables)}"


def test_21_table_shape_is_five_rows_four_cols():
    tree = parse(PDF_21)
    table = _tables(tree)[0]
    assert table.attrs["n_rows"] == 5
    assert table.attrs["n_cols"] == 4


def test_21_header_signature_matches():
    tree = parse(PDF_21)
    assert tuple(_tables(tree)[0].attrs["header_signature"]) == (
        "Region", "Q1", "Q2", "Q3",
    )


def test_21_merged_cell_text_is_one_string_with_three_lines():
    """The merged col-0 cell across rows 1..3 must hold a single text value
    containing the three visually-stacked words, separated by newlines.  This
    is the load-bearing assertion for the whole feature."""
    tree = parse(PDF_21)
    rows = _rows(_tables(tree)[0])
    row1_col0 = _cells(rows[1])[0]
    assert row1_col0.text == "Pacific\nNorthwest\nDivision"


def test_21_covered_cells_are_marked_in_continuation_rows():
    """Rows 2 and 3 (continuations of the merged region) must carry
    ``attrs.covered == True`` on column 0 — the canonical marker the renderer
    uses to drop them from the HTML/Markdown output as duplicates."""
    tree = parse(PDF_21)
    rows = _rows(_tables(tree)[0])
    for r_idx in (2, 3):
        cells = _cells(rows[r_idx])
        assert cells[0].attrs.get("covered") is True, (
            f"row {r_idx} col 0 must be marked covered, attrs={cells[0].attrs}"
        )
        # The other columns are independent data rows, NOT covered.
        for c_idx in (1, 2, 3):
            assert not cells[c_idx].attrs.get("covered"), (
                f"row {r_idx} col {c_idx} should not be covered"
            )


def test_21_adjacent_column_data_stays_on_its_own_row():
    """The Q1..Q3 values in rows 1..3 must remain in their original row.  This
    catches the regression where the col-0 merge would spill an adjacent row's
    quarterly number into the merged cell's neighbours."""
    tree = parse(PDF_21)
    grid = _grid(_tables(tree)[0])
    # rows 1..3 quarterly columns
    assert [row[1:] for row in grid[1:4]] == [
        ["100", "110", "120"],
        ["200", "210", "220"],
        ["300", "310", "320"],
    ]


def test_21_row_4_is_independent_not_swallowed_by_merge():
    """Marketing must remain its own row — the merge stops at row 3, where
    the row separator is *not* overdrawn."""
    tree = parse(PDF_21)
    grid = _grid(_tables(tree)[0])
    assert grid[4] == ["Marketing", "400", "410", "420"]


def test_21_no_nested_table_inside_merged_cell():
    """The merged col-0 cell holds plain text, not a nested table — regression
    against the (pre-fix) misdetection that would re-enter the merged region
    as a table-in-a-cell."""
    tree = parse(PDF_21)
    table = _tables(tree)[0]
    row1_col0 = _cells(_rows(table)[1])[0]
    assert all(c.kind != "table" for c in row1_col0.children)


def test_21_table_is_top_level_under_page():
    """Single table directly under page — no spurious siblings introduced by
    the visible-edge filter."""
    tree = parse(PDF_21)
    page_tables = [
        c for page in tree.children if page.kind == "page"
        for c in page.children if c.kind == "table"
    ]
    assert len(page_tables) == 1
    assert page_tables[0].attrs["n_rows"] == 5
    assert page_tables[0].attrs["n_cols"] == 4
