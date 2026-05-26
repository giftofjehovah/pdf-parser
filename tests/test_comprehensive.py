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
  - Annex A: ruled-header tables (open-body / framed-body / row-strips)
  - Annex B: fully borderless table (text-strategy fallback)
  - Annex C: outer table with text between two nested sub-tables
  - Annex D: same idiom as C, tall enough to span a page break
  - Annex E: vertically merged column drawn with white "invisible" rules
  - Multi-column body text must NOT be mis-detected as a table
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


@pytest.fixture(scope="module", params=[False, True], ids=["legacy", "bottom_up"])
def use_bottom_up(request) -> bool:
    return request.param


@pytest.fixture(scope="module")
def tree(use_bottom_up: bool) -> DocNode:
    return parse(PDF, use_bottom_up=use_bottom_up)


# ---------------------------------------------------------------------------
# Per-assertion xfail for the bottom_up variant.
#
# 13_comprehensive surfaces every residual the prior 27 fixtures cover
# individually.  Phase 9's sub-cluster carve-out in
# ``aggregate_tables._carve_subclusters`` recovers the page-spanning nested
# Project Tracking table + Hardware Inventory nesting (6 assertions) for
# bottom_up.  Phase 10 prep adds three more passes:
#   * ``_carve_container_frames`` + ``_build_single_col_wrapper`` +
#     ``_NESTED_CONTAINER_GAP_MULT`` recover Annex C's outer 1xN wrapper
#     + nested-sub-table separation (2 assertions).
#   * ``_apply_rowspan_merge`` + ``_rows_to_celltable``'s rowspan post-pass
#     + ``_split_into_tables``'s rowspan-tolerance recover the merged-cells
#     + Annex E vertical-merge assertions (6 assertions) and remove the
#     spurious-table false positive that drove the multicolumn check
#     (1 assertion).
#   * ``detect_cells._frame_cells`` (Residual D) recovers Annex D's outer
#     "Spanning Header" frame (2 assertions: Annex D + 5-spanning-tables).
#   * ``detect_cells._ruled_header_body_cells`` (Residual E) re-bins body
#     words into the line-detected header column template for ruled-header
#     tables -- recovers Annex A open-body / framed-body / row-strips
#     (3 assertions) and brings total table count from 22 to 23 (1 assertion).
#
# With Residual E landed every assertion in this file passes under bottom_up.
# ``_REASON_PHASE_7`` is retained as documentation for the deferred
# 1xN-outer-frame variants (fixtures 24/25 closed_rect / zero-height bands)
# that have no behavioural assertion in this omnibus -- left for Phase 10
# decision.  See ``docs/superpowers/plans/2026-05-25-bottom-up-cell-detection.md``.
# ---------------------------------------------------------------------------


_REASON_PHASE_7 = (
    "Phase-7+ residual (1xN outer-frame without line-detected container): "
    "Phase 10 prep's _carve_container_frames recovers fixtures 16 / Annex C "
    "where pdfplumber emits an outer wrapper cell.  Phase 10 prep Residual D "
    "(_frame_cells in detect_cells.py) further recovers fixtures 17 / Annex D "
    "via vector-rail + cap-band frame promotion.  Remaining variant of the "
    "residual: fixtures 24 / 25 (flush-edge sub-tables) where pdfplumber "
    "fuses the line strategy on 24 and 25 is a pure closed_rect with no "
    "cap-band evidence.  See tests/test_bottom_up_parity.py header."
)


# ---------------------------------------------------------------------------
# Document structure
# ---------------------------------------------------------------------------

def test_root_is_document(tree):
    assert tree.kind == "document"


def test_page_count(tree):
    pages = [n for n in _walk(tree) if n.kind == "page"]
    assert len(pages) == 18


def test_all_heading_levels_present(tree):
    levels = {n.attrs["level"] for n in _walk(tree) if n.kind == "heading"}
    assert {1, 2, 3} <= levels


def test_three_figure_nodes(tree):
    figures = [n for n in _walk(tree) if n.kind == "figure"]
    assert len(figures) == 3


# ---------------------------------------------------------------------------
# Tables: overall inventory
# ---------------------------------------------------------------------------

def test_has_exactly_five_spanning_tables(tree):
    """Quarterly Report, Transaction Log, Operations Register, Project
    Tracking, plus Annex D's outer 'Spanning Header' frame (promoted by the
    borderless-frame detector and stitched across pages 16-17)."""
    spanning = [n for n in _walk(tree) if n.kind == "table" and isinstance(n.bbox, list)]
    assert len(spanning) == 5


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
# Annex helpers
# ---------------------------------------------------------------------------

def _table_by_sig(tree: DocNode, signature: tuple[str, ...]) -> DocNode | None:
    """Locate the first table whose header_signature matches exactly."""
    for n in _walk(tree):
        if n.kind == "table" and tuple(n.attrs.get("header_signature", ())) == tuple(signature):
            return n
    return None


def _body_grid(table: DocNode) -> list[list[str]]:
    """Return the body rows (skipping the header) as a string grid."""
    return [[(c.text or "") for c in row.children] for row in table.children[1:]]


def _page_of(node: DocNode) -> int:
    """Return the first page index covered by ``node``'s bbox."""
    return node.bbox[0].page if isinstance(node.bbox, list) else node.bbox.page


# ---------------------------------------------------------------------------
# Annex A — ruled-header tables (fixtures 18 / 19 / 20)
# ---------------------------------------------------------------------------

def test_annex_a_open_body_table(tree):
    """Fixture 18 idiom: header has borders, body has none.  Parser must still
    surface a 5×3 grid with one atomic value per body cell."""
    t = _table_by_sig(tree, ("Name", "Score", "Grade"))
    assert t is not None, "Annex A open-body table missing"
    assert t.attrs["n_rows"] == 5 and t.attrs["n_cols"] == 3
    assert _body_grid(t) == [
        ["Alice", "95", "A"],
        ["Bob",   "82", "B-"],
        ["Carol", "91", "A-"],
        ["Dave",  "76", "C+"],
    ]


def test_annex_a_framed_body_table(tree):
    """Fixture 19 idiom: outer box + header column dividers; body words are
    redistributed across the five header columns."""
    t = _table_by_sig(tree, ("Region", "Q1", "Q2", "Q3", "Q4"))
    assert t is not None, "Annex A framed-body table missing"
    assert t.attrs["n_rows"] == 5 and t.attrs["n_cols"] == 5
    assert _body_grid(t) == [
        ["North", "120", "135", "150", "162"],
        ["South", "98",  "104", "111", "120"],
        ["East",  "87",  "92",  "101", "118"],
        ["West",  "143", "149", "156", "171"],
    ]


def test_annex_a_row_strips_table(tree):
    """Fixture 20 idiom: each body row has its own horizontal rule but no
    internal verticals.  Words must snap to header column bounds; '$' stays
    attached to the Price column."""
    t = _table_by_sig(tree, ("Item", "Qty", "Price"))
    assert t is not None, "Annex A row-strips table missing"
    assert t.attrs["n_rows"] == 5 and t.attrs["n_cols"] == 3
    assert _body_grid(t) == [
        ["Apple",  "3",  "$1.00"],
        ["Banana", "6",  "$0.50"],
        ["Cherry", "12", "$2.25"],
        ["Date",   "4",  "$3.10"],
    ]


# ---------------------------------------------------------------------------
# Annex B — fully borderless table (fixture 14)
# ---------------------------------------------------------------------------

def test_annex_b_borderless_table(tree):
    """Fixture 14 idiom: zero vector borders.  Only the text-strategy fallback
    can reconstruct the grid from word positions."""
    t = _table_by_sig(tree, ("Student", "Average", "Standing"))
    assert t is not None, "Annex B borderless table missing — text-fallback failed"
    assert t.attrs["n_rows"] == 4 and t.attrs["n_cols"] == 3
    assert _body_grid(t) == [
        ["Ellie", "94", "A"],
        ["Finn",  "81", "B"],
        ["Gwen",  "88", "B+"],
    ]


# ---------------------------------------------------------------------------
# Annex C — text between sub-tables (fixture 16)
# ---------------------------------------------------------------------------

def test_annex_c_outer_table_has_nested_subtables_and_note(tree):
    """Fixture 16 idiom: outer table whose middle cell holds two sub-tables
    with a paragraph between them.  Both sub-tables AND the paragraph must be
    children of the same content cell."""
    outer = _table_by_sig(tree, ("Annex C Header",))
    assert outer is not None, "Annex C outer table missing"

    content_cells = [
        cell
        for row in outer.children
        for cell in row.children
        if any(c.kind == "table" for c in cell.children)
    ]
    assert len(content_cells) == 1, (
        f"expected exactly 1 content cell with nested tables, got {len(content_cells)}"
    )
    cell = content_cells[0]

    nested_sigs = {
        tuple(c.attrs.get("header_signature", ()))
        for c in cell.children
        if c.kind == "table"
    }
    assert nested_sigs == {("Part", "Count"), ("Quarter", "Revenue")}

    note_paras = [
        c for c in cell.children
        if c.kind == "paragraph" and "NOTE:" in (c.text or "")
    ]
    assert note_paras, "NOTE paragraph missing from Annex C content cell"


def test_annex_c_subtables_are_not_siblings_of_outer(tree):
    """Sub-tables must NOT appear as top-level page children alongside the
    Annex C outer.  Regression for the _filter_outer_regions duplicate-table
    bug exposed by fixture 16."""
    annex_c_page = next(
        (
            page for page in tree.children
            if any(
                n.kind == "table"
                and tuple(n.attrs.get("header_signature", ())) == ("Annex C Header",)
                for n in page.children
            )
        ),
        None,
    )
    assert annex_c_page is not None, "Annex C page not found"
    top_tables = [n for n in annex_c_page.children if n.kind == "table"]
    assert len(top_tables) == 1, (
        f"expected 1 top-level table on Annex C page, got {len(top_tables)}"
    )


# ---------------------------------------------------------------------------
# Annex D — text between sub-tables, page-spanning (fixture 17)
# ---------------------------------------------------------------------------
#
# The outer 'Spanning Header' frame is recovered by the borderless-frame
# detector: vertical side-rails plus tiny header/footer caps are promoted
# to a single-column table, then ``stitch_pages`` joins the two per-page
# halves on matching column anchors.  Both inner sub-tables nest inside
# the outer's per-page Content cells and all between-paragraphs survive.

def test_annex_d_subtables_on_adjacent_pages(tree):
    """The two sub-tables of Annex D must land on consecutive pages — the
    between-paragraphs span the page break between them."""
    a = _table_by_sig(tree, ("Code", "Total"))
    b = _table_by_sig(tree, ("Phase", "Status"))
    assert a is not None and b is not None
    a_page = _page_of(a)
    b_page = _page_of(b)
    assert b_page == a_page + 1, (
        f"Annex D sub-tables on pages {a_page} and {b_page}; expected adjacent"
    )


def test_annex_d_between_text_bookends_preserved(tree):
    """The NOTE: / END: bookends of the between-paragraphs both survive the
    page break."""
    combined = " ".join(n.text for n in _walk(tree) if n.text)
    assert "NOTE: This paragraph sits between" in combined
    assert "END: This trailing sentence" in combined


def test_annex_d_between_text_spans_two_pages(tree):
    """The numbered Spanning-line paragraphs must be partitioned across two
    pages — neither side of the page break may swallow the entire block."""
    pages_with_lines: set[int] = set()
    for n in _walk(tree):
        if n.kind == "paragraph" and n.text and "Spanning-line" in n.text:
            if isinstance(n.bbox, list):
                pages_with_lines.update(b.page for b in n.bbox)
            else:
                pages_with_lines.add(n.bbox.page)
    assert len(pages_with_lines) >= 2, (
        f"between-paragraphs concentrated on pages {pages_with_lines}"
    )


def test_annex_d_all_spanning_lines_preserved(tree):
    """All 27 numbered Spanning-line paragraphs must survive parsing."""
    combined = " ".join(n.text for n in _walk(tree) if n.text)
    missing = [f"Spanning-line {i:02d}" for i in range(1, 28) if f"Spanning-line {i:02d}" not in combined]
    assert not missing, f"missing between-paragraphs: {missing}"


def test_annex_d_outer_frame_spans_two_pages(tree):
    """The promoted Annex D outer frame is one ``DocNode`` whose bbox is a
    2-element list covering pages 16 and 17, with both inner sub-tables
    reachable as nested descendants."""
    outer = _table_by_sig(tree, ("Spanning Header",))
    assert outer is not None, "Annex D outer 'Spanning Header' frame missing"
    assert isinstance(outer.bbox, list) and len(outer.bbox) == 2, (
        f"outer frame must span 2 pages, got bbox={outer.bbox!r}"
    )
    assert {b.page for b in outer.bbox} == {15, 16}, (
        f"outer frame must cover pages 15 and 16 (0-indexed), "
        f"got {[b.page for b in outer.bbox]}"
    )
    nested_sigs = {
        n.attrs.get("header_signature")
        for n in _walk(outer)
        if n.kind == "table" and n is not outer
    }
    assert ("Code", "Total") in nested_sigs, (
        f"Code/Total sub-table must nest inside the outer frame, got {nested_sigs}"
    )
    assert ("Phase", "Status") in nested_sigs, (
        f"Phase/Status sub-table must nest inside the outer frame, got {nested_sigs}"
    )


# ---------------------------------------------------------------------------
# Annex E — vertical merge with invisible row separators (fixture 21)
# ---------------------------------------------------------------------------

def test_annex_e_merged_column_is_one_cell_with_three_lines(tree):
    """Fixture 21 idiom: col-0 row separators at rows 1/2 and 2/3 are drawn
    in white.  A colour-aware parser must subtract those overdraws so the
    column collapses into one cell spanning rows 1..3 with newline-joined
    text — matching what a reader sees."""
    t = _table_by_sig(tree, ("Zone", "Jan", "Feb", "Mar"))
    assert t is not None, "Annex E vertical-merge table missing"
    assert t.attrs["n_rows"] == 5 and t.attrs["n_cols"] == 4
    assert t.children[1].children[0].text == "Tropical\nSubtropical\nTemperate"


def test_annex_e_continuation_rows_carry_covered_cells(tree):
    """Rows 2 and 3 of the merged column must be marked ``covered`` — they
    are continuations of the row-1 anchor cell."""
    t = _table_by_sig(tree, ("Zone", "Jan", "Feb", "Mar"))
    assert t is not None
    for r in (2, 3):
        assert t.children[r].children[0].attrs.get("covered") is True, (
            f"row {r} col 0 is not marked as covered"
        )


def test_annex_e_row_four_is_independent(tree):
    """The merge stops at row 3.  Row 4 (Polar) must remain its own row with
    all four columns intact — the merge must not leak past its boundary."""
    t = _table_by_sig(tree, ("Zone", "Jan", "Feb", "Mar"))
    assert t is not None
    assert [c.text for c in t.children[4].children] == ["Polar", "400", "410", "420"]


def test_annex_e_adjacent_columns_stay_on_their_own_row(tree):
    """Q1..Q3 values in rows 1..3 must remain in their original rows — they
    must not be swept up into the merged anchor cell."""
    t = _table_by_sig(tree, ("Zone", "Jan", "Feb", "Mar"))
    assert t is not None
    body = _body_grid(t)
    # The col-0 anchor in row 1 carries the merged text; col-0 in rows 2..3
    # is empty (covered).  Adjacent columns hold the per-row values.
    assert body[0][1:] == ["100", "110", "120"]
    assert body[1][1:] == ["200", "210", "220"]
    assert body[2][1:] == ["300", "310", "320"]


# ---------------------------------------------------------------------------
# Multi-column body text must NOT be mis-detected as a table (fixture 15)
# ---------------------------------------------------------------------------

_KNOWN_TABLE_SIGS: set[tuple[str, ...]] = {
    # Original sections 1-8.
    ("Section", "Page"),
    ("Region", "Revenue ($M)", "YoY Growth"),
    ("Component", "Specifications", "Notes"),
    ("cpu-X", "cpu-Y"),
    ("disk-X", "disk-Y"),
    ("Quarterly Report", "", ""),
    ("ID", "Description", "Value"),
    ("ID", "Operation", "Cost"),
    ("Step", "Inputs", "Notes"),
    ("p1-A", "p1-B"),
    ("p2-A", "p2-B"),
    ("Income Statement", "FY2021", "FY2022", "FY2023", "FY2024"),
    # Annex A.
    ("Name", "Score", "Grade"),
    ("Region", "Q1", "Q2", "Q3", "Q4"),
    ("Item", "Qty", "Price"),
    # Annex B.
    ("Student", "Average", "Standing"),
    # Annex C.
    ("Annex C Header",),
    ("Part", "Count"),
    ("Quarter", "Revenue"),
    # Annex D — outer 'Spanning Header' frame is now promoted by the
    # borderless-frame detector and stitched across pages 16-17.
    ("Spanning Header",),
    ("Code", "Total"),
    ("Phase", "Status"),
    # Annex E.
    ("Zone", "Jan", "Feb", "Mar"),
}


def test_multicolumn_text_not_misidentified_as_table(tree):
    """Section 1.2 uses BalancedColumns for two-column body text.  The text
    fallback must NOT surface this region as a spurious table.  We enforce
    that by enumerating every legitimate table signature; any extra entry
    indicates a multi-column false positive or some other regression."""
    sigs = {
        tuple(t.attrs.get("header_signature", ()))
        for t in _walk(tree)
        if t.kind == "table"
    }
    extras = sigs - _KNOWN_TABLE_SIGS
    assert not extras, f"unexpected table signatures detected: {extras}"


def test_total_table_count(tree):
    """The omnibus fixture contains exactly the tables we authored.  This
    locks down the global inventory so a regression that splits or
    duplicates any table is caught even when individual structural tests
    still pass."""
    tables = [n for n in _walk(tree) if n.kind == "table"]
    assert len(tables) == 23, f"expected 23 tables, got {len(tables)}"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------

def test_parse_is_deterministic():
    a = parse(PDF)
    b = parse(PDF)
    ids_a = [n.id for n in _walk(a)]
    ids_b = [n.id for n in _walk(b)]
    assert ids_a == ids_b
