"""Tests for fixture 16: text between nested sub-tables inside a single outer cell.

The fixture has an outer table (1 column) where the middle cell contains:
  [sub-table A]  →  [paragraph]  →  [sub-table B]

Before the fix in _build_cell, the paragraph was silently dropped because
``text = text if not children else None`` discarded the cell's text whenever
any nested tables were detected.  The fix extracts characters that fall
outside every nested table bbox from page_chars and adds them as paragraph
children in the correct vertical order.

Note: the outer table may have extra phantom rows (inner-table horizontal lines
counted as outer row boundaries); these are a known structural noise issue but
do not affect content preservation.  Tests only assert on content.
"""
from __future__ import annotations

from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.model import DocNode

PDF = Path("tests/golden/synthetic/16_text_between_subtables/source.pdf")

# Key phrases that must appear in the output.  The full sentence may be split
# across two visual-line paragraph nodes (word-wrap), so we check phrases.
BETWEEN_PHRASE_1 = "NOTE:"
BETWEEN_PHRASE_2 = "between the two sub-tables"
BETWEEN_PHRASE_3 = "preserved"


def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


def _all_texts(node: DocNode) -> list[str]:
    return [n.text for n in _walk(node) if n.text]


def _outer_table(tree: DocNode) -> DocNode:
    for pg in tree.children:
        for n in pg.children:
            if n.kind == "table":
                return n
    raise AssertionError("outer table not found")


def test_between_text_preserved():
    """All key phrases of the between-tables paragraph must appear in the output.

    The text may be split across multiple paragraph nodes (one per visual line),
    so we collect all text strings and check each phrase individually.
    """
    tree = parse(PDF)
    combined = " ".join(_all_texts(tree))
    assert BETWEEN_PHRASE_1 in combined, f"'{BETWEEN_PHRASE_1}' not found"
    assert BETWEEN_PHRASE_2 in combined, f"'{BETWEEN_PHRASE_2}' not found"
    assert BETWEEN_PHRASE_3 in combined, f"'{BETWEEN_PHRASE_3}' not found"


def test_no_duplicate_top_level_tables():
    """Sub-tables must not appear as top-level siblings of the outer table.

    Before the _filter_outer_regions fix, pdfplumber's line strategy returned
    the nested sub-tables as independent top-level regions.  They should now
    be present only as children of the outer table's cell.
    """
    tree = parse(PDF)
    page = tree.children[0]
    top_tables = [n for n in page.children if n.kind == "table"]
    assert len(top_tables) == 1, (
        f"expected 1 top-level table (outer), got {len(top_tables)}"
    )


def test_header_and_footer_text():
    """The plain-text header and footer cells are correctly captured."""
    tree = parse(PDF)
    texts = _all_texts(tree)
    assert any("Section Header" in t for t in texts), "header text missing"
    assert any("Section Footer" in t for t in texts), "footer text missing"


def test_nested_subtables_present():
    """Both sub-tables must be detected as children inside the content cell."""
    tree = parse(PDF)
    outer = _outer_table(tree)
    # The content cell is the one whose children include table nodes.
    content_cells = [
        cell
        for row in outer.children
        for cell in row.children
        if any(c.kind == "table" for c in cell.children)
    ]
    assert len(content_cells) >= 1, "no cell with nested tables found"
    content_cell = content_cells[0]
    nested = [c for c in content_cell.children if c.kind == "table"]
    assert len(nested) == 2, (
        f"expected 2 nested tables in content cell, got {len(nested)}"
    )


def test_subtable_a_data():
    """Sub-table A (Item/Qty) data cells are preserved."""
    tree = parse(PDF)
    texts = set(_all_texts(tree))
    for expected in ("Item", "Qty", "Widget A", "10", "Widget B", "5"):
        assert expected in texts, f"'{expected}' missing from sub-table A"


def test_subtable_b_data():
    """Sub-table B (Month/Sales) data cells are preserved."""
    tree = parse(PDF)
    texts = set(_all_texts(tree))
    for expected in ("Month", "Sales", "Jan", "$500", "Feb", "$700"):
        assert expected in texts, f"'{expected}' missing from sub-table B"


def test_between_text_is_paragraph_not_heading():
    """The recovered between-table text must appear in paragraph nodes, not headings.

    Checks that at least one paragraph node carries the key NOTE phrase.
    """
    tree = parse(PDF)
    para_texts = [n.text for n in _walk(tree) if n.kind == "paragraph" and n.text]
    assert any(BETWEEN_PHRASE_1 in t for t in para_texts), (
        f"No paragraph node found containing '{BETWEEN_PHRASE_1}'. "
        f"paragraph texts: {para_texts}"
    )


def test_between_text_in_content_cell():
    """The between-table paragraph must be a child of the content cell (not free-floating)."""
    tree = parse(PDF)
    outer = _outer_table(tree)
    # Collect all children of all cells in the outer table.
    for row in outer.children:
        for cell in row.children:
            cell_texts = [c.text for c in cell.children if c.text]
            if any(BETWEEN_PHRASE_1 in t for t in cell_texts):
                return  # found it as a cell child
    raise AssertionError(
        f"'{BETWEEN_PHRASE_1}' not found as a child of any outer table cell"
    )
