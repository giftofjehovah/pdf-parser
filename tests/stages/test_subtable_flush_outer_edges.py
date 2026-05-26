"""Tests for fixture 24: nested sub-tables flush with the outer frame's
top and bottom edges.

This is the worst-case manifestation of the page-edge pattern: a multi-page
outer table cut at a page boundary leaves the first / last inner sub-table
on that page slice with its top / bottom edge sitting at the same
y-coordinate (and often the same x-rails) as the outer frame.

Bottom-up canonical: pdfplumber's line strategy fuses the shared edges into
a single grid containing both inner sub-tables and the inter-paragraph cell.
The closed_rect outer frame has no cap-band evidence so ``_frame_cells``
rejects the wrapper promotion (documented Phase-10+ residual: legacy reaches
the 1x1 wrapper via ``_try_decompose_megatable``, which bottom-up does not
port).  Result: ONE 7x2 table where the NOTE paragraph becomes the text of
the row-3 first cell and the inner sub-tables collapse into rows 0-2 and
4-6.  All source text is preserved.

These tests pin that behavioural contract: no source text is lost, the row
order matches reading order, and the NOTE paragraph survives as a cell
text on its own row (not absorbed into a neighbouring data row).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import DocNode
from pdf_parser.pipeline import parse

PDF = Path("tests/golden/synthetic/24_subtable_flush_outer_edges/source.pdf")


def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _tables(root: DocNode) -> list[DocNode]:
    return [n for n in _walk(root) if n.kind == "table"]


def _row_texts(row: DocNode) -> list[str]:
    return [(cell.text or "") for cell in row.children if cell.kind == "cell"]


def _grid(table: DocNode) -> list[list[str]]:
    return [_row_texts(r) for r in table.children if r.kind == "row"]


@pytest.fixture(scope="module")
def tree() -> DocNode:
    return parse(PDF)


@pytest.fixture(scope="module")
def fused_table(tree) -> DocNode:
    tables = _tables(tree)
    assert len(tables) == 1, (
        f"bottom-up canonical: 1 fused table, got {len(tables)}.  "
        "If this jumps to 3, ``_frame_cells`` started promoting the "
        "closed_rect wrapper or megatable-decomposition came back."
    )
    return tables[0]


def test_table_is_seven_rows_two_columns(fused_table):
    """Fused shape: 3 rows sub-table A + 1 row NOTE + 3 rows sub-table B."""
    assert fused_table.attrs["n_rows"] == 7
    assert fused_table.attrs["n_cols"] == 2


def test_sub_table_a_rows_preserved(fused_table):
    """Rows 0-2 carry sub-table A's header + 2 data rows verbatim."""
    grid = _grid(fused_table)
    assert grid[0] == ["Item", "Qty"]
    assert grid[1] == ["Widget A", "10"]
    assert grid[2] == ["Widget B", "5"]


def test_sub_table_b_rows_preserved(fused_table):
    """Rows 4-6 carry sub-table B's header + 2 data rows verbatim."""
    grid = _grid(fused_table)
    assert grid[4] == ["Month", "Sales"]
    assert grid[5] == ["Jan", "$500"]
    assert grid[6] == ["Feb", "$700"]


def test_note_paragraph_text_survives_as_row_3_cell(fused_table):
    """The inter-paragraph NOTE must NOT be silently dropped — it survives
    as the row-3 first-column cell text.  Wrapped lines join via newline
    because pdfplumber emits the paragraph as a single line-detected cell
    spanning the table width.
    """
    grid = _grid(fused_table)
    note_text = grid[3][0]
    # Normalise wrapped-line newlines so the substring check is robust to
    # whatever line-break shape pdfplumber lands on.
    flat = " ".join(note_text.split())
    assert "NOTE: This paragraph sits between two sub-tables" in flat, (
        f"NOTE row text was lost or corrupted: {note_text!r}"
    )
    assert "paragraph node inside the outer cell" in flat, (
        f"end of NOTE paragraph was dropped: {note_text!r}"
    )


def test_no_source_text_is_lost(tree):
    """Concatenate every leaf text in the tree; every source phrase must
    appear at least once.  Catches regressions where the fused-row layout
    silently drops a sub-table data value or the NOTE paragraph.
    """
    leaf_texts = [
        (n.text or "") for n in _walk(tree) if (n.text or "").strip()
    ]
    joined = " ".join(leaf_texts)
    for needle in (
        "Item", "Qty", "Widget A", "10", "Widget B", "5",
        "Month", "Sales", "Jan", "$500", "Feb", "$700",
        "NOTE: This paragraph sits between two sub-tables",
    ):
        assert needle in joined, f"source text {needle!r} missing from parsed tree"
