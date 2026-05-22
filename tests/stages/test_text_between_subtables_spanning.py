"""Tests for fixture 17: text between two sub-tables, spanning a page break.

The fixture extends fixture 16 (text between two nested sub-tables) to a
page-spanning layout.  See ``build_pdfs.build_17_text_between_subtables_spanning``
for the rendering choices.  Two visual properties matter to the parse:

  * The outer "section" has continuous left/right side rails and no
    horizontal closing borders around the between-paragraphs, so it reads
    as a single open box that bridges both pages.
  * Between-paragraphs sit in their own outer rows so each side of the
    page break retains the cell's TOPPADDING / BOTTOMPADDING.

The borderless-frame detector in :mod:`pdf_parser.stages.detect_tables`
promotes the section frame to a single-column outer table (Header /
Content per page / Footer) and the page-stitcher joins the per-page
halves on matching column anchors.  The two inner sub-tables and the
between-paragraphs are reachable as descendants of the outer table's
Content cells.
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
    """The 'Section Header' label is captured on page 0.

    After the borderless-frame detector promotes the outer section to a
    spanning table anchored on page 0, the Header cell lives inside that
    table.  We assert by the cell's bbox-page rather than by walking only
    one page node's descendants.
    """
    tree = parse(PDF)
    hits = [
        n for n in _walk(tree)
        if n.text and "Section Header" in n.text and _bbox_pages(n) == {0}
    ]
    assert hits, "'Section Header' missing from page 0"


def test_section_footer_on_second_page():
    """The 'Section Footer' label is captured on page 1.

    The Footer cell is a descendant of the spanning outer table (anchored
    on page 0), so walking ``tree.children[1]`` alone misses it.  Assert
    via the cell's bbox-page instead.
    """
    tree = parse(PDF)
    hits = [
        n for n in _walk(tree)
        if n.text and "Section Footer" in n.text and _bbox_pages(n) == {1}
    ]
    assert hits, "'Section Footer' missing from page 1"


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

    The real assertion the fixture exists for: the paragraph block must
    not be silently truncated where the page splits it.  Each Between-line
    N appears on exactly one page (asserted via the paragraph's bbox-page,
    since the paragraphs now live inside the spanning outer table's two
    Content cells), and together the two pages cover the full 1..27 range
    without gaps.
    """
    tree = parse(PDF)
    pages: dict[int, set[int]] = {0: set(), 1: set()}
    for n in _walk(tree):
        if n.kind != "paragraph" or not n.text or "Between-line " not in n.text:
            continue
        tok = n.text.split("Between-line ", 1)[1].split(":", 1)[0].strip()
        if not tok.isdigit():
            continue
        for pg in _bbox_pages(n):
            pages.setdefault(pg, set()).add(int(tok))

    assert pages[0], "no Between-line N paragraphs on page 0"
    assert pages[1], "no Between-line N paragraphs on page 1 (split lost)"

    overlap = pages[0] & pages[1]
    assert not overlap, f"between-lines duplicated across pages: {sorted(overlap)}"
    union = pages[0] | pages[1]
    assert union == set(range(1, 28)), (
        f"between-lines lost at the page break: missing "
        f"{sorted(set(range(1, 28)) - union)}"
    )


def test_all_three_tables_present():
    """Borderless-frame detection promotes the section frame to a table.

    The full table inventory is now:
      * the spanning outer 'Section Header' frame, and
      * the two inner sub-tables nested inside its Content cells.
    """
    tree = parse(PDF)
    headers = sorted(tuple(_table_header_texts(t)) for t in _tables(tree))
    assert headers == [
        ("Item", "Qty"),
        ("Month", "Sales"),
        ("Section Header",),
    ], f"expected outer frame + two inner sub-tables, got {headers}"


# ---------------------------------------------------------------------------
# Borderless-frame containment (the parser feature this fixture exercises).
# ---------------------------------------------------------------------------


def test_outer_section_frame_is_detected_as_spanning_table():
    """The outer frame becomes a single spanning table.

    Post-fix shape: ``stitch_pages`` joins the per-page frame halves into
    one ``DocNode`` anchored on page 0 whose bbox is a 2-element list
    covering both pages.  Page 0's top level contains exactly one table
    (the outer frame); page 1's top level contains none (the frame is
    anchored on page 0).  Both inner sub-tables are reachable as
    descendants of the outer frame, not as page-level siblings.
    """
    tree = parse(PDF)
    page0_top = [n for n in tree.children[0].children if n.kind == "table"]
    page1_top = [n for n in tree.children[1].children if n.kind == "table"]
    assert len(page0_top) == 1, (
        f"expected one top-level outer table on page 0, got {len(page0_top)}"
    )
    assert len(page1_top) == 0, (
        f"expected no top-level tables on page 1 (frame anchored on page 0), "
        f"got {len(page1_top)}"
    )

    outer = page0_top[0]
    assert isinstance(outer.bbox, list) and len(outer.bbox) == 2, (
        f"outer frame must span 2 pages, got bbox={outer.bbox!r}"
    )
    assert {b.page for b in outer.bbox} == {0, 1}, (
        f"outer frame must cover pages 0 and 1, got {[b.page for b in outer.bbox]}"
    )
    assert _table_header_texts(outer) == ["Section Header"], (
        f"outer frame header row should read 'Section Header', got "
        f"{_table_header_texts(outer)!r}"
    )

    # Both inner sub-tables nest inside the outer's descendants.
    nested_headers = {
        tuple(_table_header_texts(t))
        for t in _tables(outer)
        if t is not outer
    }
    assert ("Item", "Qty") in nested_headers, (
        f"sub-table A must nest inside the outer frame, got {nested_headers}"
    )
    assert ("Month", "Sales") in nested_headers, (
        f"sub-table B must nest inside the outer frame, got {nested_headers}"
    )
