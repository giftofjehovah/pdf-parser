"""Tests for fixture 17: text between two sub-tables, spanning a page break.

The fixture extends fixture 16 (text between two nested sub-tables) to a
page-spanning layout.  See ``build_pdfs.build_17_text_between_subtables_spanning``
for the rendering choices.  Two visual properties matter to the test:

  * The outer "section" has continuous left/right side rails and no
    horizontal closing borders around the between-paragraphs, so it reads
    as a single open box that bridges both pages.
  * Between-paragraphs sit in their own outer rows so each side of the
    page break retains the cell's TOPPADDING / BOTTOMPADDING.

Parser consequence: pdfplumber's line-based table detection needs internal
horizontal grid lines to identify the outer frame as a table.  With those
removed, only the two inner sub-tables are detected as table nodes; the
outer "Section Header / Section Footer" framing surfaces as a heading +
paragraph and the between-text as page-level paragraphs.  All *content*
is preserved on both pages — that's what the bulk of these tests assert.
The two ``xfail`` cases at the bottom document the table-detection limit
this layout exposes.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import BBox, DocNode
from pdf_parser.pipeline import parse

PDF = Path("tests/golden/synthetic/17_text_between_subtables_spanning/source.pdf")


def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _all_texts(node: DocNode) -> list[str]:
    return [n.text for n in _walk(node) if n.text]


def _bbox_pages(node: DocNode) -> set[int]:
    if isinstance(node.bbox, BBox):
        return {node.bbox.page}
    return {b.page for b in node.bbox}


def _tables(node: DocNode) -> list[DocNode]:
    return [n for n in _walk(node) if n.kind == "table"]


def _table_header_texts(table: DocNode) -> list[str]:
    rows = [c for c in table.children if c.kind == "row"]
    if not rows:
        return []
    return [c.text or "" for c in rows[0].children if c.kind == "cell"]


# ---------------------------------------------------------------------------
# Content-preservation assertions (must pass on the current parser).
# ---------------------------------------------------------------------------


def test_two_pages():
    """Fixture renders to exactly two pages."""
    tree = parse(PDF)
    pages = [c for c in tree.children if c.kind == "page"]
    assert len(pages) == 2, f"expected 2 pages, got {len(pages)}"


def test_section_header_on_first_page():
    """The 'Section Header' label is captured on page 0."""
    tree = parse(PDF)
    page0 = tree.children[0]
    assert any("Section Header" in t for t in _all_texts(page0)), (
        "'Section Header' missing from page 0"
    )


def test_section_footer_on_second_page():
    """The 'Section Footer' label is captured on page 1."""
    tree = parse(PDF)
    page1 = tree.children[1]
    assert any("Section Footer" in t for t in _all_texts(page1)), (
        "'Section Footer' missing from page 1"
    )


def test_subtable_a_on_first_page():
    """The Item/Qty sub-table lives on page 0 and carries all its data cells."""
    tree = parse(PDF)
    sub_a = next(
        (t for t in _tables(tree) if _table_header_texts(t) == ["Item", "Qty"]),
        None,
    )
    assert sub_a is not None, "sub-table A (Item/Qty) not found"
    assert _bbox_pages(sub_a) == {0}, (
        f"sub-table A must live entirely on page 0, got pages={_bbox_pages(sub_a)}"
    )
    texts = set(_all_texts(sub_a))
    for expected in ("Item", "Qty", "Widget A", "10", "Widget B", "5"):
        assert expected in texts, f"'{expected}' missing from sub-table A"


def test_subtable_b_on_second_page():
    """The Month/Sales sub-table lives on page 1 and carries all its data cells."""
    tree = parse(PDF)
    sub_b = next(
        (t for t in _tables(tree) if _table_header_texts(t) == ["Month", "Sales"]),
        None,
    )
    assert sub_b is not None, "sub-table B (Month/Sales) not found"
    assert _bbox_pages(sub_b) == {1}, (
        f"sub-table B must live entirely on page 1, got pages={_bbox_pages(sub_b)}"
    )
    texts = set(_all_texts(sub_b))
    for expected in ("Month", "Sales", "Jan", "$500", "Feb", "$700"):
        assert expected in texts, f"'{expected}' missing from sub-table B"


def test_between_text_bookends_preserved():
    """The NOTE: and END: bookends of the between-paragraphs both survive."""
    tree = parse(PDF)
    combined = " ".join(_all_texts(tree))
    assert "NOTE:" in combined, "'NOTE:' opener missing from output"
    assert "between the two sub-tables" in combined, (
        "core NOTE phrase missing from output"
    )
    assert "END:" in combined, "'END:' closer missing from output"


def test_between_text_spans_both_pages():
    """Numbered between-lines are partitioned cleanly across the page break.

    This is the real assertion the fixture exists for: the paragraph block
    must not be silently truncated where the page splits it.  Each
    Between-line N appears on exactly one page, and together the two pages
    cover the full 1..27 range without gaps.
    """
    tree = parse(PDF)
    pages = [c for c in tree.children if c.kind == "page"]
    assert len(pages) == 2

    def _line_numbers_on(page: DocNode) -> set[int]:
        nums: set[int] = set()
        for t in _all_texts(page):
            if "Between-line " in t:
                tok = t.split("Between-line ", 1)[1].split(":", 1)[0].strip()
                if tok.isdigit():
                    nums.add(int(tok))
        return nums

    page0_lines = _line_numbers_on(pages[0])
    page1_lines = _line_numbers_on(pages[1])

    assert page0_lines, "no Between-line N paragraphs on page 0"
    assert page1_lines, "no Between-line N paragraphs on page 1 (split lost)"

    overlap = page0_lines & page1_lines
    assert not overlap, f"between-lines duplicated across pages: {sorted(overlap)}"
    assert page0_lines | page1_lines == set(range(1, 28)), (
        f"between-lines lost at the page break: missing "
        f"{sorted(set(range(1, 28)) - (page0_lines | page1_lines))}"
    )


def test_only_inner_sub_tables_are_detected():
    """With the borderless outer frame, only the two inner sub-tables surface.

    Pins what we *do* recognise so a future change that starts inventing
    phantom outer tables (or loses the inner ones) is caught.
    """
    tree = parse(PDF)
    headers = sorted(tuple(_table_header_texts(t)) for t in _tables(tree))
    assert headers == [("Item", "Qty"), ("Month", "Sales")], (
        f"expected exactly the two inner sub-tables, got {headers}"
    )


# ---------------------------------------------------------------------------
# Known parser limitation exposed by this fixture (strict xfail so a
# future improvement flips it to xpass and forces a follow-up).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "The outer 'section frame' (continuous left/right side rails, "
        "horizontal lines only around the Header and Footer rows, and NO "
        "horizontal lines anywhere across the between-paragraphs) does not "
        "match pdfplumber's line-based table detection: with no internal "
        "horizontal grid lines, the frame is not recognised as a table. "
        "Consequence: the two inner sub-tables surface at the page top "
        "level instead of being nested inside an outer-table cell, and "
        "the between-paragraphs surface as page-level paragraphs. "
        "Recovering the original containment would require a 'borderless "
        "frame' heuristic that promotes a region bounded by vertical-only "
        "rails into a table-like container."
    ),
)
def test_outer_section_frame_is_detected_as_outer_table():
    tree = parse(PDF)
    page0_top = [n for n in tree.children[0].children if n.kind == "table"]
    page1_top = [n for n in tree.children[1].children if n.kind == "table"]
    # Ideal: one outer table per page (the page-1 and page-2 halves of the
    # section frame), each containing its sub-table nested as a cell child,
    # with the between-paragraphs also nested as cell children.
    assert len(page0_top) == 1, (
        f"expected one top-level outer table on page 0, got {len(page0_top)}"
    )
    assert len(page1_top) == 1, (
        f"expected one top-level outer table on page 1, got {len(page1_top)}"
    )
    # And the sub-tables should be reachable as cell descendants of the
    # outer tables, not as top-level siblings.
    sub_a_headers = {("Item", "Qty")}
    sub_b_headers = {("Month", "Sales")}
    page0_top_headers = {tuple(_table_header_texts(t)) for t in page0_top}
    page1_top_headers = {tuple(_table_header_texts(t)) for t in page1_top}
    assert page0_top_headers.isdisjoint(sub_a_headers), (
        "sub-table A should be nested, not a top-level sibling"
    )
    assert page1_top_headers.isdisjoint(sub_b_headers), (
        "sub-table B should be nested, not a top-level sibling"
    )
