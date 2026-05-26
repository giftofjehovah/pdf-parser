"""Tests for fixture 25: sub-tables flush with the outer frame on ONE
vertical edge while inset horizontally from the outer's rails.

Two pages; each page hosts its own independent closed outer table:

  Page 1 — outer holds [NOTE-TOP, sub_a, NOTE-MID1, sub_b].
           Sub_b's bottom is flush with the outer's bottom.

  Page 2 — outer holds [sub_c, NOTE-MID2, sub_d, NOTE-BOT].
           Sub_c's top is flush with the outer's top.

Bottom-up canonical (after the Phase-10 text-gap-split fix in
``aggregate_tables._gap_has_between_text``): per page, the two inner
sub-tables emerge as siblings and the inter-paragraph prose (NOTE-MID1 /
NOTE-MID2) is preserved as a paragraph node between them.  NOTE-TOP and
NOTE-BOT remain as bookend paragraphs.  The closed_rect outer frame is
NOT promoted into a 1x1 wrapper because ``_frame_cells`` rejects pure
closed_rect (documented Phase-10+ residual; legacy reaches the wrapper
via ``_try_decompose_megatable``).

These tests pin the load-bearing contract: every NOTE paragraph survives,
every sub-table emerges with its correct rows, and the two pages stay
independent (no cross-page stitching).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import DocNode
from pdf_parser.pipeline import parse

PDF = Path("tests/golden/synthetic/25_subtable_flush_outer_vertical_only/source.pdf")


def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _tables(root: DocNode) -> list[DocNode]:
    return [n for n in _walk(root) if n.kind == "table"]


def _paragraphs(root: DocNode) -> list[DocNode]:
    return [n for n in _walk(root) if n.kind == "paragraph"]


def _row_texts(row: DocNode) -> list[str]:
    return [(cell.text or "") for cell in row.children if cell.kind == "cell"]


def _grid(table: DocNode) -> list[list[str]]:
    return [_row_texts(r) for r in table.children if r.kind == "row"]


def _tables_on_page(tree: DocNode, page_idx: int) -> list[DocNode]:
    return [t for t in _tables(tree) if t.attrs.get("page") == page_idx]


@pytest.fixture(scope="module")
def tree() -> DocNode:
    return parse(PDF)


# ---------------------------------------------------------------------------
# All four NOTE paragraphs survive parsing (the load-bearing data-loss test).
# ---------------------------------------------------------------------------


def test_all_four_note_paragraphs_preserved(tree):
    """The pre-Phase-10 regression silently dropped NOTE-MID1 + NOTE-MID2
    because the inner sub-tables fused into one 6x2 table whose bbox
    covered both NOTE rows; ``build_tree`` then suppressed them as
    overlapping a detected table region.  The text-gap-split fix in
    ``aggregate_tables._gap_has_between_text`` preserves all four.
    """
    para_texts = " ".join(p.text or "" for p in _paragraphs(tree))
    for needle in ("NOTE-TOP", "NOTE-MID1", "NOTE-MID2", "NOTE-BOT"):
        assert needle in para_texts, (
            f"{needle!r} paragraph lost — Phase-10 text-gap-split regression."
        )


# ---------------------------------------------------------------------------
# Per-page table count and shape.
# ---------------------------------------------------------------------------


def test_page1_has_two_tables(tree):
    page1 = _tables_on_page(tree, 0)
    assert len(page1) == 2, (
        f"page 1 must have exactly 2 sub-tables (sub_a, sub_b), got {len(page1)}"
    )


def test_page2_has_two_tables(tree):
    page2 = _tables_on_page(tree, 1)
    assert len(page2) == 2, (
        f"page 2 must have exactly 2 sub-tables (sub_c, sub_d), got {len(page2)}"
    )


def test_pages_stay_independent_no_cross_page_stitching(tree):
    """The two outer rectangles are independent; no table should span both pages."""
    cross_page = [t for t in _tables(tree) if isinstance(t.bbox, list)]
    assert cross_page == [], (
        f"unexpected cross-page tables: {[t.attrs for t in cross_page]}"
    )


# ---------------------------------------------------------------------------
# Sub-table content (rows + cell text).
# ---------------------------------------------------------------------------


def test_page1_sub_a_content(tree):
    sub_a = _tables_on_page(tree, 0)[0]
    assert _grid(sub_a) == [
        ["Item",     "Qty"],
        ["Widget A", "10"],
        ["Widget B", "5"],
    ]


def test_page1_sub_b_content(tree):
    sub_b = _tables_on_page(tree, 0)[1]
    assert _grid(sub_b) == [
        ["Month", "Sales"],
        ["Jan",   "$500"],
        ["Feb",   "$700"],
    ]


def test_page2_sub_c_content(tree):
    sub_c = _tables_on_page(tree, 1)[0]
    assert _grid(sub_c) == [
        ["Step", "Owner"],
        ["1",    "Alice"],
        ["2",    "Bob"],
    ]


def test_page2_sub_d_content(tree):
    sub_d = _tables_on_page(tree, 1)[1]
    assert _grid(sub_d) == [
        ["City", "Zone"],
        ["NYC",  "East"],
        ["LA",   "West"],
    ]


# ---------------------------------------------------------------------------
# Reading order: NOTE paragraphs interleave with sub-tables vertically.
# ---------------------------------------------------------------------------


def test_page1_reading_order(tree):
    """Page 1 reading order: NOTE-TOP → sub_a → NOTE-MID1 → sub_b."""
    page = tree.children[0]
    interesting = [
        c for c in page.children
        if c.kind in ("paragraph", "table")
    ]
    labels = []
    for n in interesting:
        if n.kind == "paragraph":
            labels.append((n.text or "")[:9])
        else:
            grid = _grid(n)
            labels.append("table:" + grid[0][0] if grid else "table:?")
    assert labels == ["NOTE-TOP:", "table:Item", "NOTE-MID1", "table:Month"], (
        f"unexpected page-1 reading order: {labels}"
    )


def test_page2_reading_order(tree):
    """Page 2 reading order: sub_c → NOTE-MID2 → sub_d → NOTE-BOT."""
    page = tree.children[1]
    interesting = [
        c for c in page.children
        if c.kind in ("paragraph", "table")
    ]
    labels = []
    for n in interesting:
        if n.kind == "paragraph":
            labels.append((n.text or "")[:9])
        else:
            grid = _grid(n)
            labels.append("table:" + grid[0][0] if grid else "table:?")
    assert labels == ["table:Step", "NOTE-MID2", "table:City", "NOTE-BOT:"], (
        f"unexpected page-2 reading order: {labels}"
    )
