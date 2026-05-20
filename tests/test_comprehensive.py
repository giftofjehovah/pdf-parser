"""End-to-end behavioral tests for the 13_comprehensive omnibus fixture.

Each test asserts a distinct structural property of the parsed tree, covering
every use-case exercised by the fixture in a single document:
  - Heading levels 1, 2, 3
  - Body paragraphs
  - Simple grid tables
  - Nested tables (table inside a cell)
  - Merged cells (covered-cell semantics)
  - Page-spanning table WITH header repeat  → header deduplicated to 1 row
  - Page-spanning table WITHOUT header repeat → all rows preserved, no dedup
  - Page-spanning table with nested sub-tables on both pages
  - Dense financial table
  - Three embedded figure nodes (raster images)
  - Parse determinism (stable node IDs across two runs)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.model import DocNode

PDF = Path("tests/golden/synthetic/13_comprehensive/source.pdf")


def _walk(node: DocNode):
    yield node
    for child in node.children:
        yield from _walk(child)


@pytest.fixture(scope="module")
def tree() -> DocNode:
    return parse(PDF)


# ---------------------------------------------------------------------------
# Document structure
# ---------------------------------------------------------------------------

def test_root_is_document(tree):
    assert tree.kind == "document"


def test_page_count(tree):
    pages = [n for n in _walk(tree) if n.kind == "page"]
    assert len(pages) == 12


def test_all_heading_levels_present(tree):
    levels = {n.attrs["level"] for n in _walk(tree) if n.kind == "heading"}
    assert {1, 2, 3} <= levels


def test_three_figure_nodes(tree):
    figures = [n for n in _walk(tree) if n.kind == "figure"]
    assert len(figures) == 3


# ---------------------------------------------------------------------------
# Tables: overall inventory
# ---------------------------------------------------------------------------

def test_has_exactly_four_spanning_tables(tree):
    spanning = [n for n in _walk(tree) if n.kind == "table" and isinstance(n.bbox, list)]
    assert len(spanning) == 4


# ---------------------------------------------------------------------------
# Page-spanning table WITH header repeat (Transaction Log)
# ---------------------------------------------------------------------------

def _tx_log(tree: DocNode) -> DocNode:
    for n in _walk(tree):
        if n.kind == "table" and n.attrs.get("header_signature") == ("ID", "Description", "Value"):
            return n
    raise AssertionError("Transaction Log table not found")


def test_tx_log_spans_two_pages(tree):
    t = _tx_log(tree)
    assert isinstance(t.bbox, list)
    assert len(t.bbox) == 2


def test_tx_log_header_deduplicated(tree):
    """repeatRows=1 causes the header to appear on every page slice; the stitcher must
    remove the duplicate so the merged table has exactly one header row."""
    t = _tx_log(tree)
    header_rows = [r for r in t.children if r.children[0].text == "ID"]
    assert len(header_rows) == 1, f"expected 1 header row, got {len(header_rows)}"


def test_tx_log_has_fifty_data_rows(tree):
    t = _tx_log(tree)
    data_rows = [r for r in t.children if r.children[0].text != "ID"]
    assert len(data_rows) == 50


def test_tx_log_rows_span_both_pages(tree):
    t = _tx_log(tree)
    pages_seen = {r.attrs["page"] for r in t.children}
    assert len(pages_seen) == 2


# ---------------------------------------------------------------------------
# Page-spanning table WITHOUT header repeat (Operations Register)
# ---------------------------------------------------------------------------

def _ops_register(tree: DocNode) -> DocNode:
    for n in _walk(tree):
        if n.kind == "table" and n.attrs.get("header_signature") == ("ID", "Operation", "Cost"):
            return n
    raise AssertionError("Operations Register table not found")


def test_ops_register_spans_two_pages(tree):
    t = _ops_register(tree)
    assert isinstance(t.bbox, list)
    assert len(t.bbox) == 2


def test_ops_register_has_fifty_data_rows(tree):
    """Without repeatRows the header is never duplicated, so all 50 data rows survive
    unmodified and no dedup is applied."""
    t = _ops_register(tree)
    data_rows = [r for r in t.children if r.children[0].text != "ID"]
    assert len(data_rows) == 50


def test_ops_register_rows_span_both_pages(tree):
    t = _ops_register(tree)
    pages_seen = {r.attrs["page"] for r in t.children}
    assert len(pages_seen) == 2


def test_ops_and_tx_log_are_separate_tables(tree):
    """Two independent tables with the same column widths must not be stitched together."""
    tx = _tx_log(tree)
    ops = _ops_register(tree)
    assert tx is not ops
    # Their page spans must not overlap
    tx_pages = set(tx.attrs.get("spans_pages", []))
    ops_pages = set(ops.attrs.get("spans_pages", []))
    assert tx_pages.isdisjoint(ops_pages)


# ---------------------------------------------------------------------------
# Page-spanning table with nested sub-tables on BOTH pages (Project Tracking)
# ---------------------------------------------------------------------------

def _project_table(tree: DocNode) -> DocNode:
    for n in _walk(tree):
        if n.kind == "table" and n.attrs.get("header_signature") == ("Step", "Inputs", "Notes"):
            return n
    raise AssertionError("Project Tracking table not found")


def _nested_step_numbers(table: DocNode) -> list[str]:
    """Return Step values for every row that has a nested table in its Inputs cell."""
    found = []
    for row in table.children:
        if len(row.children) < 2:
            continue
        inputs_cell = row.children[1]
        if any(c.kind == "table" for c in inputs_cell.children):
            found.append(row.children[0].text)
    return found


def test_project_table_spans_two_pages(tree):
    t = _project_table(tree)
    assert isinstance(t.bbox, list)
    assert len(t.bbox) == 2


def test_project_table_has_nested_on_both_pages(tree):
    t = _project_table(tree)
    steps = _nested_step_numbers(t)
    assert len(steps) == 2, f"expected 2 rows with nested tables, got {steps}"


def test_project_table_nested_are_on_different_pages(tree):
    t = _project_table(tree)
    pages_with_nested = set()
    for row in t.children:
        if len(row.children) >= 2:
            if any(c.kind == "table" for c in row.children[1].children):
                pages_with_nested.add(row.attrs["page"])
    assert len(pages_with_nested) == 2, "nested sub-tables must appear on both pages"


def test_project_table_nested_step_numbers(tree):
    t = _project_table(tree)
    steps = _nested_step_numbers(t)
    assert "5" in steps
    assert "45" in steps


def test_project_table_all_fifty_steps_present(tree):
    t = _project_table(tree)
    step_values = [r.children[0].text for r in t.children if r.children[0].text != "Step"]
    assert step_values == [str(i) for i in range(1, 51)]


# ---------------------------------------------------------------------------
# Nested table (Hardware Inventory)
# ---------------------------------------------------------------------------

def test_hardware_inventory_has_nested_tables(tree):
    """The hardware inventory outer table embeds sub-tables in CPU and Storage cells."""
    for n in _walk(tree):
        if n.kind == "table" and n.attrs.get("header_signature") == ("Component", "Specifications", "Notes"):
            nested = [c for row in n.children for cell in row.children for c in cell.children
                      if c.kind == "table"]
            assert len(nested) >= 2, f"expected ≥2 nested tables in inventory, found {len(nested)}"
            return
    pytest.fail("Hardware Inventory table not found")


# ---------------------------------------------------------------------------
# Merged cells (covered semantics)
# ---------------------------------------------------------------------------

def test_merged_cells_table_has_covered_cells(tree):
    """The Quarterly Performance table has a colspan row and a rowspan column."""
    covered = [n for n in _walk(tree) if n.kind == "cell" and n.attrs.get("covered")]
    assert len(covered) >= 2, f"expected ≥2 covered cells, got {len(covered)}"



def test_merged_cells_table_spans_pages(tree):
    """The Quarterly Performance table is large enough to split across a page break;
    the stitcher must reassemble it into one spanning table with all 5 rows."""
    mc = next(
        (n for n in _walk(tree)
         if n.kind == "table" and n.attrs.get("header_signature", ("",))[0] == "Quarterly Report"),
        None,
    )
    assert mc is not None, "Quarterly Performance table not found"
    assert isinstance(mc.bbox, list), "merged-cells table must span pages"
    assert len(mc.children) == 5, f"expected 5 rows after stitch, got {len(mc.children)}"


def test_merged_cells_correct_structure(tree):
    """Row 0 has a colspan (2 covered cells); row 3 has a rowspan continuation (1 covered cell)."""
    mc = next(
        (n for n in _walk(tree)
         if n.kind == "table" and n.attrs.get("header_signature", ("",))[0] == "Quarterly Report"),
        None,
    )
    assert mc is not None
    # Colspan: cells (0,1) and (0,2) are covered
    row0_covered = [c for c in mc.children[0].children if c.attrs.get("covered")]
    assert len(row0_covered) == 2, f"row 0 should have 2 covered cells (colspan), got {len(row0_covered)}"
    # Rowspan: cell (3,0) is covered
    row3_covered = [c for c in mc.children[3].children if c.attrs.get("covered")]
    assert len(row3_covered) == 1, f"row 3 should have 1 covered cell (rowspan), got {len(row3_covered)}"
    # Primary rowspan cell text is in row 2 col 0
    assert mc.children[2].children[0].text == "North"

# ---------------------------------------------------------------------------
# Dense financial table
# ---------------------------------------------------------------------------

def test_financial_table_row_count(tree):
    pl = next(
        (n for n in _walk(tree)
         if n.kind == "table" and n.attrs.get("header_signature", ("",))[0] == "Income Statement"),
        None,
    )
    assert pl is not None, "P&L table not found"
    assert len(pl.children) >= 25, "P&L table should have at least 25 rows"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_parse_is_deterministic():
    a = parse(PDF)
    b = parse(PDF)
    ids_a = [n.id for n in _walk(a)]
    ids_b = [n.id for n in _walk(b)]
    assert ids_a == ids_b
