"""Tests for fixture 24: nested sub-tables flush with the outer frame's
top and bottom edges.

This is the worst-case manifestation of the page-edge pattern: a multi-page
outer table cut at a page boundary leaves the first / last inner sub-table
on that page slice with its top / bottom edge sitting at the same
y-coordinate (and often the same x-rails) as the outer frame.

Bottom-up canonical (post-bay-drop): pdfplumber's line strategy emits the
inter-paragraph as a wide bordered cell that fuses the two stacked sub-grids
into one 7×2 table.  ``aggregate._drop_frame_middle_bays`` recognises that
wide cell as a frame-interior closure (flanked by multi-column grids above
AND below) and drops it.  The remaining 12 cells then split into two
sibling sub-tables on the same page; the NOTE paragraph survives because
``segment`` extracts it from the raw page text and ``build_tree`` interleaves
it between the two sub-tables in reading order — mirroring fixture 25.

These tests pin that contract: two sibling tables surface as page-level
children, the NOTE paragraph sits between them, and no source text is
lost.
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
def page0(tree) -> DocNode:
    pages = [n for n in _walk(tree) if n.kind == "page"]
    assert len(pages) == 1, f"expected 1 page, got {len(pages)}"
    return pages[0]


# ---------------------------------------------------------------------------
# Two sibling sub-tables on the page, in reading order
# ---------------------------------------------------------------------------


def test_two_sibling_sub_tables_at_page_level(page0):
    """Bay drop produces two top-level sibling tables (3×2 each)."""
    tables = [c for c in page0.children if c.kind == "table"]
    assert len(tables) == 2, (
        f"expected 2 sibling tables on page; got {len(tables)}: "
        f"{[(t.attrs.get('n_rows'), t.attrs.get('n_cols')) for t in tables]}"
    )
    for t in tables:
        assert t.attrs["n_rows"] == 3
        assert t.attrs["n_cols"] == 2


def test_first_sub_table_is_item_qty(page0):
    tables = [c for c in page0.children if c.kind == "table"]
    grid = _grid(tables[0])
    assert grid[0] == ["Item", "Qty"]
    assert grid[1] == ["Widget A", "10"]
    assert grid[2] == ["Widget B", "5"]


def test_second_sub_table_is_month_sales(page0):
    tables = [c for c in page0.children if c.kind == "table"]
    grid = _grid(tables[1])
    assert grid[0] == ["Month", "Sales"]
    assert grid[1] == ["Jan", "$500"]
    assert grid[2] == ["Feb", "$700"]


# ---------------------------------------------------------------------------
# NOTE paragraph between the two sub-tables, in reading order
# ---------------------------------------------------------------------------


def test_note_paragraph_is_page_child_between_sub_tables(page0):
    """The NOTE paragraph sits between the two sub-tables on the page —
    not inside either of them and not absorbed as cell text.
    """
    children = page0.children
    table_idxs = [i for i, c in enumerate(children) if c.kind == "table"]
    assert len(table_idxs) == 2, "expected exactly two top-level tables on page"
    between_idxs = list(range(table_idxs[0] + 1, table_idxs[1]))
    assert between_idxs, "expected ≥1 sibling between the two tables"
    note_nodes = [
        children[i] for i in between_idxs
        if children[i].kind in ("paragraph", "list_item")
        and "NOTE:" in (children[i].text or "")
    ]
    assert note_nodes, (
        f"NOTE paragraph not found between sub-tables; siblings between: "
        f"{[(children[i].kind, (children[i].text or '')[:40]) for i in between_idxs]}"
    )


def test_note_paragraph_text_complete(page0):
    """The NOTE paragraph carries the full source phrase, not a truncation."""
    notes = [
        n for n in _walk(page0)
        if n.kind == "paragraph" and "NOTE:" in (n.text or "")
    ]
    assert notes, "no NOTE paragraph found anywhere under the page"
    full = " ".join(" ".join((n.text or "").split()) for n in notes)
    assert "NOTE: This paragraph sits between two sub-tables" in full, full
    assert "paragraph node inside the outer cell" in full, full


# ---------------------------------------------------------------------------
# Belt-and-braces: no source text is lost in the reshape
# ---------------------------------------------------------------------------


def test_no_source_text_is_lost(tree):
    """Concatenate every leaf text in the tree; every source phrase must
    appear at least once.  Catches regressions where the bay drop silently
    discards a sub-table data value or the NOTE paragraph.
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
