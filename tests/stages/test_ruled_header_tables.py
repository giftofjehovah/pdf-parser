"""Tests for fixtures 18-20: ruled-header / open-body tables.

Three variants exercise the same parser path
(``pdf_parser.stages.detect_tables._redistribute_ruled_header_body``):

  * ``18_ruled_header_open_body`` — header has cell borders + a rule under it;
    body has *no* lines at all.  pdfplumber's line strategy detects only the
    header, so the parser must extend the table downward and bin the body
    words to the header's column x-bounds.

  * ``19_ruled_header_framed_body`` — outer box wraps the table; header has
    internal column separators; body has no internal verticals.  pdfplumber
    detects header + a single merged-cell body row that contains all body
    text.  Parser must split that cell into per-row sub-rows and bin words
    to columns.

  * ``20_ruled_header_row_strips`` — outer box + header column separators +
    horizontal rules between every body row.  pdfplumber detects N body
    rows but each has one merged cell.  Parser must bin per-row words to
    columns.

All three should surface as a single table with the expected (rows, cols)
shape and exact cell contents.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import DocNode
from pdf_parser.pipeline import parse


# ---------------------------------------------------------------------------
# Helpers (kept local to avoid coupling with other stages' test helpers).
# ---------------------------------------------------------------------------

def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _tables(node: DocNode) -> list[DocNode]:
    return [n for n in _walk(node) if n.kind == "table"]


def _rows(table: DocNode) -> list[DocNode]:
    return [c for c in table.children if c.kind == "row"]


def _row_texts(row: DocNode) -> list[str]:
    return [(c.text or "") for c in row.children if c.kind == "cell"]


def _grid(table: DocNode) -> list[list[str]]:
    return [_row_texts(r) for r in _rows(table)]


# ---------------------------------------------------------------------------
# Fixture 18 — open-body table.
# ---------------------------------------------------------------------------

PDF_18 = Path("tests/golden/synthetic/18_ruled_header_open_body/source.pdf")

EXPECTED_GRID_18 = [
    ["Name",  "Score", "Grade"],
    ["Alice", "95",    "A"],
    ["Bob",   "82",    "B-"],
    ["Carol", "91",    "A-"],
    ["Dave",  "76",    "C+"],
]


def test_18_single_table_detected():
    tree = parse(PDF_18)
    tables = _tables(tree)
    assert len(tables) == 1, f"expected 1 table, got {len(tables)}"


def test_18_table_shape_is_five_rows_three_cols():
    tree = parse(PDF_18)
    table = _tables(tree)[0]
    assert table.attrs["n_rows"] == 5
    assert table.attrs["n_cols"] == 3


def test_18_grid_contents_exact():
    tree = parse(PDF_18)
    assert _grid(_tables(tree)[0]) == EXPECTED_GRID_18


def test_18_header_signature_matches():
    tree = parse(PDF_18)
    assert tuple(_tables(tree)[0].attrs["header_signature"]) == tuple(EXPECTED_GRID_18[0])


def test_18_body_cells_carry_distinct_text():
    """Each body cell must hold one value, not the row's concatenated text."""
    tree = parse(PDF_18)
    body_rows = _grid(_tables(tree)[0])[1:]
    for row in body_rows:
        assert all(" " not in c for c in row), f"merged-cell concatenation in row {row}"


# ---------------------------------------------------------------------------
# Fixture 19 — framed-body table.
# ---------------------------------------------------------------------------

PDF_19 = Path("tests/golden/synthetic/19_ruled_header_framed_body/source.pdf")

EXPECTED_GRID_19 = [
    ["Region", "Q1",  "Q2",  "Q3",  "Q4"],
    ["North",  "120", "135", "150", "162"],
    ["South",  "98",  "104", "111", "120"],
    ["East",   "87",  "92",  "101", "118"],
    ["West",   "143", "149", "156", "171"],
]


def test_19_single_table_detected():
    tree = parse(PDF_19)
    tables = _tables(tree)
    assert len(tables) == 1, f"expected 1 table, got {len(tables)}"


def test_19_table_shape_is_five_rows_five_cols():
    tree = parse(PDF_19)
    table = _tables(tree)[0]
    assert table.attrs["n_rows"] == 5
    assert table.attrs["n_cols"] == 5


def test_19_grid_contents_exact():
    tree = parse(PDF_19)
    assert _grid(_tables(tree)[0]) == EXPECTED_GRID_19


def test_19_no_nested_table_inside_merged_body_cell():
    """Regression: before the redistribution pass, the merged body cell was
    re-detected as a nested table.  Now there must be exactly one table in
    the tree."""
    tree = parse(PDF_19)
    assert len(_tables(tree)) == 1


def test_19_numeric_columns_are_separated():
    """Each Q1..Q4 column must contain exactly one number per body row."""
    tree = parse(PDF_19)
    body = _grid(_tables(tree)[0])[1:]
    for row in body:
        for cell in row[1:]:
            assert cell.isdigit(), f"non-atomic numeric cell: {cell!r} in row {row}"


# ---------------------------------------------------------------------------
# Fixture 20 — row-strips table.
# ---------------------------------------------------------------------------

PDF_20 = Path("tests/golden/synthetic/20_ruled_header_row_strips/source.pdf")

EXPECTED_GRID_20 = [
    ["Item",   "Qty", "Price"],
    ["Apple",  "3",   "$1.00"],
    ["Banana", "6",   "$0.50"],
    ["Cherry", "12",  "$2.25"],
    ["Date",   "4",   "$3.10"],
]


def test_20_single_table_detected():
    tree = parse(PDF_20)
    tables = _tables(tree)
    assert len(tables) == 1, f"expected 1 table, got {len(tables)}"


def test_20_table_shape_is_five_rows_three_cols():
    tree = parse(PDF_20)
    table = _tables(tree)[0]
    assert table.attrs["n_rows"] == 5
    assert table.attrs["n_cols"] == 3


def test_20_grid_contents_exact():
    tree = parse(PDF_20)
    assert _grid(_tables(tree)[0]) == EXPECTED_GRID_20


def test_20_price_column_preserves_dollar_sign():
    """The Price column must retain the leading '$' on every body row."""
    tree = parse(PDF_20)
    body = _grid(_tables(tree)[0])[1:]
    for row in body:
        assert row[2].startswith("$"), f"missing $ in Price cell: {row[2]!r}"


# ---------------------------------------------------------------------------
# Cross-fixture invariants.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pdf,expected_rows,expected_cols", [
    (PDF_18, 5, 3),
    (PDF_19, 5, 5),
    (PDF_20, 5, 3),
])
def test_table_is_top_level_under_page(pdf: Path, expected_rows: int, expected_cols: int):
    """All three fixtures should produce one table that sits directly under a
    page node — no spurious nesting introduced by the redistribution pass."""
    tree = parse(pdf)
    page_tables = [
        c for page in tree.children if page.kind == "page"
        for c in page.children if c.kind == "table"
    ]
    assert len(page_tables) == 1
    t = page_tables[0]
    assert t.attrs["n_rows"] == expected_rows
    assert t.attrs["n_cols"] == expected_cols
