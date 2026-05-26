"""CellTable dataclass shape + empty-input contract for aggregate()."""
from pdf_parser.model import BBox
from pdf_parser.stages.detect_cells import Cell
from pdf_parser.stages.aggregate_tables import CellTable, aggregate, _cells_inside, _dedupe_cells


def test_celltable_fields():
    bb = BBox(page=0, x0=0, y0=0, x1=10, y1=10)
    t = CellTable(
        page_index=0,
        bbox=bb,
        grid=[["a", "b"]],
        cell_bboxes=[[bb, bb]],
        covered=set(),
        header_signature=("a", "b"),
        page_height=792.0,
        nested=[],
        source="line",
    )
    assert t.grid == [["a", "b"]]
    assert t.covered == set()
    assert t.nested == []


def test_aggregate_empty_returns_empty():
    assert aggregate([], page_height=792.0) == []


def test_dedupe_prefers_line_over_gutter_over_text():
    bb = BBox(page=0, x0=10, y0=20, x1=30, y1=40)
    cells = [
        Cell(bbox=bb, text="gutter", source="gutter", confidence=0.7),
        Cell(bbox=bb, text="LINE",   source="line",   confidence=1.0),
        Cell(bbox=bb, text="text",   source="text",   confidence=0.4),
    ]
    out = _dedupe_cells(cells)
    assert len(out) == 1
    assert out[0].source == "line"
    assert out[0].text == "LINE"


def test_split_breaks_on_large_vertical_gap():
    """Two table candidates separated by > N×median row-gap split correctly."""
    def bb(y0, y1, x0=10, x1=30):
        return BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Table A: rows at y=100, 120
        Cell(bbox=bb(100, 110), text="a1", source="line", confidence=1.0),
        Cell(bbox=bb(100, 110, 40, 60), text="a2", source="line", confidence=1.0),
        Cell(bbox=bb(120, 130), text="a3", source="line", confidence=1.0),
        Cell(bbox=bb(120, 130, 40, 60), text="a4", source="line", confidence=1.0),
        # Big paragraph gap from y=130 to y=300
        # Table B: rows at y=300, 320
        Cell(bbox=bb(300, 310), text="b1", source="line", confidence=1.0),
        Cell(bbox=bb(300, 310, 40, 60), text="b2", source="line", confidence=1.0),
        Cell(bbox=bb(320, 330), text="b3", source="line", confidence=1.0),
        Cell(bbox=bb(320, 330, 40, 60), text="b4", source="line", confidence=1.0),
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 2


def test_cells_inside_filters_by_containment():
    outer = BBox(page=0, x0=0, y0=0, x1=100, y1=100)
    inside_bb = BBox(page=0, x0=10, y0=10, x1=30, y1=30)
    outside_bb = BBox(page=0, x0=200, y0=200, x1=220, y1=220)
    cells = [
        Cell(bbox=inside_bb, text="in", source="line", confidence=1.0),
        Cell(bbox=outside_bb, text="out", source="line", confidence=1.0),
    ]
    inside = _cells_inside(cells, outer)
    assert [c.text for c in inside] == ["in"]


def test_nested_table_detected_via_containment():
    """A 2-row × 2-col outer table whose top-right cell contains a 2×2 inner table.

    Outer cell layout (page 0):
      [ A:0,0,50,20 ]  [ B:50,0,100,20 ]
      [ C:0,20,50,60]  [ inner cell block 60-100 ]

    Inner table inside cell at (50,20,100,60): four cells at corners.
    """
    def bb(x0, y0, x1, y1):
        return BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    outer = [
        Cell(bbox=bb(0,  0, 50, 20), text="A", source="line", confidence=1.0),
        Cell(bbox=bb(50, 0,100, 20), text="B", source="line", confidence=1.0),
        Cell(bbox=bb(0, 20, 50, 60), text="C", source="line", confidence=1.0),
        Cell(bbox=bb(50,20,100, 60), text="",  source="line", confidence=1.0),
    ]
    inner = [
        Cell(bbox=bb(50, 20, 75, 40), text="i1", source="line", confidence=1.0),
        Cell(bbox=bb(75, 20,100, 40), text="i2", source="line", confidence=1.0),
        Cell(bbox=bb(50, 40, 75, 60), text="i3", source="line", confidence=1.0),
        Cell(bbox=bb(75, 40,100, 60), text="i4", source="line", confidence=1.0),
    ]
    tables = aggregate(outer + inner, page_height=792.0)
    assert len(tables) == 1, f"expected one outer; got {len(tables)}"
    outer_t = tables[0]
    assert outer_t.grid[0] == ["A", "B"]
    assert len(outer_t.nested) == 1
    inner_t = outer_t.nested[0]
    assert inner_t.grid == [["i1", "i2"], ["i3", "i4"]]

def test_header_columns_extend_below():
    """Mixed source (line-bounded header + gutter body) on a single page must
    cluster into one table with column structure inherited from the header.

    This is the Phase-5 ruled-header-open-body sentinel: three line cells
    on row 0 then four gutter rows at the same x-ranges below.  The
    ``_split_into_tables`` gap-merge logic must keep them in one table even
    when the header→first-body gap is tight (< 8pt).
    """
    def bb(x0, y0, x1, y1, src="line"):
        return Cell(
            bbox=BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1),
            text="H" if src == "line" else "x",
            source=src,
            confidence=1.0 if src == "line" else 0.7,
        )
    cells = [
        # Header (line-bounded): 3 cells at y=100..115
        bb(50, 100, 100, 115),
        bb(100, 100, 200, 115),
        bb(200, 100, 280, 115),
        # Body (gutter): 4 rows × 3 columns, same x-ranges, y=120..175
        *[bb(50, 120 + 15*i, 100, 130 + 15*i, "gutter") for i in range(4)],
        *[bb(100, 120 + 15*i, 200, 130 + 15*i, "gutter") for i in range(4)],
        *[bb(200, 120 + 15*i, 280, 130 + 15*i, "gutter") for i in range(4)],
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 1
    # 1 header row + 4 body rows = 5 rows in the merged table.
    assert len(tables[0].grid) == 5


def test_merged_cell_marks_covered():
    """Row 0 has 3 cells; row 1 has 2 cells where the left one spans cols 0-1.

    Logical layout:
      row 0: [a] [b] [c]                                 (3 cells)
      row 1: [d-spans-cols-0-and-1] [e]                  (2 cells)

    Expected: the wide cell in row 1 is placed in slot (1,0); slot (1,1) is
    an empty 'covered' cell pointing back to (1,0)'s bbox.
    """
    bb = lambda x0, x1, y0, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        Cell(bbox=bb(  0, 100,  0, 20), text="a", source="line", confidence=1.0),
        Cell(bbox=bb(100, 200,  0, 20), text="b", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300,  0, 20), text="c", source="line", confidence=1.0),
        Cell(bbox=bb(  0, 200, 20, 40), text="d", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300, 20, 40), text="e", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    assert (1, 1) in t.covered
    assert t.grid[1] == ["d", "", "e"]

def test_column_anchors_survive_colspan_heavy_row():
    """A row that is itself a partial colspan (e.g. section subheader)
    must not collapse the anchor set. Anchors come from union-clustering
    cell x0 positions across all rows."""
    bb = lambda x0, x1, y0, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Section header: 2 cells, each spanning 2 logical columns
        Cell(bbox=bb(  0, 200,  0, 20), text="Group A", source="line", confidence=1.0),
        Cell(bbox=bb(200, 400,  0, 20), text="Group B", source="line", confidence=1.0),
        # Data row 1: 4 narrow cells (drive the column set)
        Cell(bbox=bb(  0, 100, 20, 40), text="a", source="line", confidence=1.0),
        Cell(bbox=bb(100, 200, 20, 40), text="b", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300, 20, 40), text="c", source="line", confidence=1.0),
        Cell(bbox=bb(300, 400, 20, 40), text="d", source="line", confidence=1.0),
        # Data row 2: same 4-column structure
        Cell(bbox=bb(  0, 100, 40, 60), text="e", source="line", confidence=1.0),
        Cell(bbox=bb(100, 200, 40, 60), text="f", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300, 40, 60), text="g", source="line", confidence=1.0),
        Cell(bbox=bb(300, 400, 40, 60), text="h", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    assert t.grid[0] == ["Group A", "", "Group B", ""]
    assert (0, 1) in t.covered
    assert (0, 3) in t.covered
    assert t.grid[1] == ["a", "b", "c", "d"]
    assert t.grid[2] == ["e", "f", "g", "h"]

def test_column_anchors_union_cluster_recovers_missing_boundary():
    """No single row carries all 4 column boundaries: row 0 merges cols 0+1,
    row 1 merges cols 2+3.  Widest-row anchors (row 0 = 3 cells) yield only
    3 columns, mis-binning row 1's narrow cells.  Union-clustering x0
    positions across all rows recovers the 4-column truth.
    """
    bb = lambda x0, x1, y0, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Row 0: cols 0+1 merged, cols 2 and 3 separate.
        Cell(bbox=bb(  0, 200,  0, 20), text="ab", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300,  0, 20), text="c",  source="line", confidence=1.0),
        Cell(bbox=bb(300, 400,  0, 20), text="d",  source="line", confidence=1.0),
        # Row 1: cols 0 and 1 separate, cols 2+3 merged.
        Cell(bbox=bb(  0, 100, 20, 40), text="e",  source="line", confidence=1.0),
        Cell(bbox=bb(100, 200, 20, 40), text="f",  source="line", confidence=1.0),
        Cell(bbox=bb(200, 400, 20, 40), text="gh", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    # 4 logical columns total.
    assert t.grid[0] == ["ab", "", "c", "d"]
    assert t.grid[1] == ["e", "f", "gh", ""]
    assert (0, 1) in t.covered
    assert (1, 3) in t.covered


def test_covered_bbox_uses_spanning_cell_y_extent():
    """A covered slot's y bounds come from the SPANNING cell, not the row's
    min/max y across all cells.  Matches `_logical_grid_from_table` in the
    legacy extractor — required for id-set parity on fixtures 10/21.

    Row 0 here has a SHORT spanning cell ('header', y=10..30) plus a TALLER
    non-spanning cell ('x', y=4..36).  Row min/max y = (4, 36); spanning
    cell y = (10, 30).  The covered slot at (0, 1) must use (10, 30).
    """
    bb = lambda x0, x1, y0, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Row 0 (ymids 20 and 20 → cluster together).
        Cell(bbox=bb(  0, 200, 10, 30), text="header", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300,  4, 36), text="x",      source="line", confidence=1.0),
        # Row 1: three narrow cells (ymid 50 → new row).
        Cell(bbox=bb(  0, 100, 40, 60), text="a", source="line", confidence=1.0),
        Cell(bbox=bb(100, 200, 40, 60), text="b", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300, 40, 60), text="c", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    cov_bbox = t.cell_bboxes[0][1]
    assert (cov_bbox.x0, cov_bbox.x1) == (100, 200)
    assert (cov_bbox.y0, cov_bbox.y1) == (10, 30)


def test_flush_subtable_inside_parent_cell():
    """Inner 2x2 sub-table sharing edges with the parent cell on all sides.

    Phase-7 sentinel for the flush-edge case (mirrors fixtures 24/25): the
    inner cluster's union bbox equals the parent cell's bbox, and every inner
    cell shares at least one edge with the parent (i1.x0 == D.x0, i1.y0 == D.y0,
    i2.x1 == D.x1, i4.y1 == D.y1).  ``_cells_inside`` must still accept these
    as contained — strict-smaller is per-cell on at least one axis, which holds
    here (each inner cell is half-width AND half-height of the parent) but
    flush edges must not be rejected by the containment-tolerance check.

    The plan's original layout (outer 1-row x 2-col + a separate second outer
    row at y=60..80) cannot form an outer table at all: the inner cells'
    y-midpoints (15, 45) interleave the outer's (30), so ``_row_cluster``
    fragments the outer rows.  This corrected layout stacks the inner table
    INSIDE the bottom-right outer cell so the outer rows cluster cleanly.
    """
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Outer 2x2: row 0 (A, B), row 1 (C, D).  D contains the inner table.
        Cell(bbox=bb(  0,  0, 100, 30), text="A", source="line", confidence=1.0),
        Cell(bbox=bb(100,  0, 200, 30), text="B", source="line", confidence=1.0),
        Cell(bbox=bb(  0, 30, 100, 90), text="C", source="line", confidence=1.0),
        Cell(bbox=bb(100, 30, 200, 90), text="",  source="line", confidence=1.0),
        # Inner 2x2 flush with the parent on all four sides:
        # i1.x0 == D.x0, i1.y0 == D.y0, i2.x1 == D.x1, i3.x0 == D.x0,
        # i4.x1 == D.x1, i3.y1 == D.y1, i4.y1 == D.y1.
        Cell(bbox=bb(100, 30, 150, 60), text="i1", source="line", confidence=1.0),
        Cell(bbox=bb(150, 30, 200, 60), text="i2", source="line", confidence=1.0),
        Cell(bbox=bb(100, 60, 150, 90), text="i3", source="line", confidence=1.0),
        Cell(bbox=bb(150, 60, 200, 90), text="i4", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    assert t.grid[0] == ["A", "B"]
    assert t.grid[1][0] == "C"
    # Parent cell's text cleared because nested table takes over.
    assert t.grid[1][1] == ""
    assert len(t.nested) == 1
    sub = t.nested[0]
    assert sub.grid == [["i1", "i2"], ["i3", "i4"]]
    # Inner table's bbox equals the parent cell's bbox — the flush case.
    assert (sub.bbox.x0, sub.bbox.y0, sub.bbox.x1, sub.bbox.y1) == (100, 30, 200, 90)


def test_outer_frame_container_carves_nested_subtables():
    """1xN outer-frame wrapper: header text-cell + empty big-middle container
    + footer text-cell stack vertically, all sharing the outer x-extent.
    The container strictly contains the two sub-tables.  Mirrors fixtures
    16/17 + Annex C/D in 13_comprehensive: pdfplumber's line strategy
    emits an outer 3x1 wrapper alongside two inner sub-tables.

    Without the carve-out, the big container's y-range overlaps the
    sub-table rows, causing ``_row_cluster`` to place the container in
    its own row sandwiched between Header and the inner cells.  Then
    ``_split_into_tables`` splits at the x0=156 → x0=162 left-edge change,
    yielding a flat 6-row table from the fused sub-tables instead of a
    1xN outer wrapper hosting the sub-tables as nested.

    Expected behaviour: aggregate emits ONE top-level outer wrapper whose
    container cell hosts both sub-tables as nested children, plus 8 covered
    placeholder rows mirroring the inner sub-table row boundaries (the
    legacy ``_logical_grid_from_table`` convention -- see
    ``test_wrapper_expands_placeholder_rows_when_inner_h_lines_span_majority``).
    Header signature is the first row's text.
    """
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Outer 3x1 wrapper: header, container, footer (all share x0=156, x1=456).
        Cell(bbox=bb(156, 118, 456, 138), text="Section Header", source="line", confidence=1.0),
        Cell(bbox=bb(156, 138, 456, 284), text="",               source="line", confidence=1.0),
        Cell(bbox=bb(156, 284, 456, 304), text="Section Footer", source="line", confidence=1.0),
        # Sub-table A: 3x2 (header + 2 data rows) at y=142..196, x=162..342
        Cell(bbox=bb(162, 142, 252, 160), text="Item",     source="line", confidence=1.0),
        Cell(bbox=bb(252, 142, 342, 160), text="Qty",      source="line", confidence=1.0),
        Cell(bbox=bb(162, 160, 252, 178), text="Widget A", source="line", confidence=1.0),
        Cell(bbox=bb(252, 160, 342, 178), text="10",       source="line", confidence=1.0),
        Cell(bbox=bb(162, 178, 252, 196), text="Widget B", source="line", confidence=1.0),
        Cell(bbox=bb(252, 178, 342, 196), text="5",        source="line", confidence=1.0),
        # Sub-table B: 3x2 at y=226..280
        Cell(bbox=bb(162, 226, 252, 244), text="Month", source="line", confidence=1.0),
        Cell(bbox=bb(252, 226, 342, 244), text="Sales", source="line", confidence=1.0),
        Cell(bbox=bb(162, 244, 252, 262), text="Jan",   source="line", confidence=1.0),
        Cell(bbox=bb(252, 244, 342, 262), text="$500",  source="line", confidence=1.0),
        Cell(bbox=bb(162, 262, 252, 280), text="Feb",   source="line", confidence=1.0),
        Cell(bbox=bb(252, 262, 342, 280), text="$700",  source="line", confidence=1.0),
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 1, (
        f"expected ONE outer wrapper hosting both sub-tables, got {len(tables)}: "
        f"{[(t.header_signature, t.attrs if hasattr(t, 'attrs') else (len(t.grid), len(t.grid[0])) ) for t in tables]}"
    )
    outer = tables[0]
    assert outer.header_signature == ("Section Header",)
    # Header / container / 8 placeholders / footer.  Placeholder expansion
    # is covered separately in
    # ``test_wrapper_expands_placeholder_rows_when_inner_h_lines_span_majority``;
    # this test focuses on the carve-out + nested attachment, so we just
    # check the header / footer slots and the nested sub-tables.
    assert len(outer.grid) == 11
    assert all(len(r) == 1 for r in outer.grid)
    assert outer.grid[0] == ["Section Header"]
    assert outer.grid[10] == ["Section Footer"]
    # Both sub-tables attached as nested.
    nested_sigs = {t.header_signature for t in outer.nested}
    assert nested_sigs == {("Item", "Qty"), ("Month", "Sales")}, (
        f"expected both sub-tables nested, got {nested_sigs}"
    )

def test_rowspan_tall_cell_marks_following_row_covered():
    """A tall cell whose y-extent covers two visual rows below it must
    cluster into the FIRST overlapping row, and the column it occupies
    in the SECOND overlapping row must be emitted as ``covered`` with an
    anchor-x + sub-row-y bbox (the legacy ``_logical_grid_from_table``
    convention).

    Mirrors fixture 10 (Quarterly Report) and fixture 21 (Pacific
    Northwest Division): col 0 carries a single tall text cell whose
    y-extent equals the combined height of two short cells in cols 1+
    of the same band.  ``_row_cluster``'s ymid divergence would otherwise
    place the tall cell in its own narrow row (sandwiched between the two
    visual rows of its shorter neighbours), and ``_split_into_tables``
    would then split at the left-edge change between the tall cell's
    column and the shorter cells' column — yielding two unrelated tables
    instead of one with covered-cell rowspan semantics.
    """
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Header row (y=0..20)
        Cell(bbox=bb(  0,  0, 150, 20), text="Region", source="line", confidence=1.0),
        Cell(bbox=bb(150,  0, 220, 20), text="Q1",     source="line", confidence=1.0),
        Cell(bbox=bb(220,  0, 290, 20), text="Q2",     source="line", confidence=1.0),
        # Tall col-0 cell spans rows 1+2 (y=20..56)
        Cell(bbox=bb(  0, 20, 150, 56), text="North",  source="line", confidence=1.0),
        # Row 1 (y=20..38)
        Cell(bbox=bb(150, 20, 220, 38), text="100",    source="line", confidence=1.0),
        Cell(bbox=bb(220, 20, 290, 38), text="200",    source="line", confidence=1.0),
        # Row 2 (y=38..56) — short cells aligned with second half of "North"
        Cell(bbox=bb(150, 38, 220, 56), text="120",    source="line", confidence=1.0),
        Cell(bbox=bb(220, 38, 290, 56), text="180",    source="line", confidence=1.0),
        # Row 3 (y=56..74) — independent row beyond the merge
        Cell(bbox=bb(  0, 56, 150, 74), text="South",  source="line", confidence=1.0),
        Cell(bbox=bb(150, 56, 220, 74), text="300",    source="line", confidence=1.0),
        Cell(bbox=bb(220, 56, 290, 74), text="400",    source="line", confidence=1.0),
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 1, (
        f"expected ONE table with rowspan covered slots, got {len(tables)}: "
        f"sigs={[t.header_signature for t in tables]}"
    )
    t = tables[0]
    assert t.header_signature == ("Region", "Q1", "Q2")
    assert len(t.grid) == 4, f"expected 4 rows, got {len(t.grid)}"
    # Row 1: tall cell anchored here.
    assert t.grid[1] == ["North", "100", "200"]
    # Row 2: col 0 covered by the rowspan, cols 1-2 are independent.
    assert t.grid[2] == ["", "120", "180"]
    assert (2, 0) in t.covered, "row 2 col 0 must be marked covered by the rowspan"
    # Covered bbox at (2, 0) uses anchor x-range + sub-row y-range, matching
    # legacy's ``_logical_grid_from_table`` convention for both colspan and
    # rowspan covered slots (extract_tables.py lines 182-186).  The spanning
    # cell's own y-extent is intentionally NOT used here: it would split into
    # the rowspan's first visual band, but the slot lives in the SECOND band.
    cov_bbox = t.cell_bboxes[2][0]
    assert (cov_bbox.x0, cov_bbox.x1) == (0, 150)
    assert (cov_bbox.y0, cov_bbox.y1) == (38, 56), (
        f"covered slot must use sub-row y-extent, got {(cov_bbox.y0, cov_bbox.y1)}"
    )
    # Row 3: independent, no rowspan leakage.
    assert t.grid[3] == ["South", "300", "400"]
    assert (3, 0) not in t.covered, "row 3 must not be marked covered"


def test_single_row_multi_column_emits_celltable():
    """A 1-row N-col line-detected candidate forms a CellTable.

    Mirrors fixture 23 (bordered-cell-with-bulleted-prose): pdfplumber's
    line strategy emits a single horizontal band split into >=2 vertical
    cells by visible verticals.  Legacy's ``_logical_grid_from_table``
    accepts this as a 1xN logical grid; bottom-up must too, or the cells
    surface as loose paragraphs / lists instead of a table.

    The negative-direction guard (single 1-col cell) stays in place:
    ``all(len(r) < 2 for r in rows)`` still rejects 1-row 1-col candidates
    so lone bordered text blocks and split-off footers do not become
    spurious tables.
    """
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        Cell(bbox=bb(190, 118, 241, 594), text="Label",   source="line", confidence=1.0),
        Cell(bbox=bb(241, 118, 421, 594), text="Section", source="line", confidence=1.0),
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 1, f"expected 1 table, got {len(tables)}"
    t = tables[0]
    assert t.grid == [["Label", "Section"]]
    assert t.header_signature == ("Label", "Section")


def test_single_row_single_column_rejected():
    """A 1-row 1-col candidate must NOT become a table (lone bordered block)."""
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        Cell(bbox=bb(100, 100, 400, 200), text="Lone block", source="line", confidence=1.0),
    ]
    tables = aggregate(cells, page_height=792.0)
    assert tables == [], f"lone 1-col cell must not surface as a table, got {tables}"


def test_wrapper_expands_placeholder_rows_when_inner_h_lines_span_majority():
    """1xN wrapper with nested sub-tables whose width >= 50% of the wrapper's
    width gets placeholder rows mirroring the inner sub-tables' row boundaries.

    Mirrors fixture 16 (text-between-subtables): legacy's
    ``_logical_grid_from_table`` collects every H-line whose width spans
    at least 50% of the outer-table width as a row boundary; inner
    sub-table H-lines that pass the 50% gate become wrapper rows (with the
    container cell rowspan-extended over them, emitting them as
    ``covered=True`` slots at the wrapper's full width).
    """
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    wrapper_x0, wrapper_x1 = 156, 456  # width 300
    inner_x0, inner_x1 = 162, 342      # width 180; 180/300 = 0.60 >= 0.50
    cells = [
        Cell(bbox=bb(wrapper_x0, 118, wrapper_x1, 138), text="Section Header", source="line", confidence=1.0),
        Cell(bbox=bb(wrapper_x0, 138, wrapper_x1, 284), text="container",      source="line", confidence=1.0),
        Cell(bbox=bb(wrapper_x0, 284, wrapper_x1, 304), text="Section Footer", source="line", confidence=1.0),
        Cell(bbox=bb(inner_x0, 142, 252, 160), text="Item",     source="line", confidence=1.0),
        Cell(bbox=bb(252,      142, inner_x1, 160), text="Qty", source="line", confidence=1.0),
        Cell(bbox=bb(inner_x0, 160, 252, 178), text="Widget A", source="line", confidence=1.0),
        Cell(bbox=bb(252,      160, inner_x1, 178), text="10",  source="line", confidence=1.0),
        Cell(bbox=bb(inner_x0, 178, 252, 196), text="Widget B", source="line", confidence=1.0),
        Cell(bbox=bb(252,      178, inner_x1, 196), text="5",   source="line", confidence=1.0),
        Cell(bbox=bb(inner_x0, 226, 252, 244), text="Month",    source="line", confidence=1.0),
        Cell(bbox=bb(252,      226, inner_x1, 244), text="Sales",source="line", confidence=1.0),
        Cell(bbox=bb(inner_x0, 244, 252, 262), text="Jan",      source="line", confidence=1.0),
        Cell(bbox=bb(252,      244, inner_x1, 262), text="$500", source="line", confidence=1.0),
        Cell(bbox=bb(inner_x0, 262, 252, 280), text="Feb",      source="line", confidence=1.0),
        Cell(bbox=bb(252,      262, inner_x1, 280), text="$700", source="line", confidence=1.0),
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 1, f"expected 1 wrapper, got {len(tables)}"
    w = tables[0]
    assert w.header_signature == ("Section Header",)
    # 1 header + 1 container + 8 placeholders + 1 footer = 11.
    assert len(w.grid) == 11, f"expected 11 rows, got {len(w.grid)}"
    assert w.grid[0] == ["Section Header"]
    assert w.grid[10] == ["Section Footer"]
    # Container row keeps its full y-extent on its cell bbox.
    assert (w.cell_bboxes[1][0].y0, w.cell_bboxes[1][0].y1) == (138, 284)
    for r_idx in range(2, 10):
        assert (r_idx, 0) in w.covered, f"row {r_idx} must be covered"
        cb = w.cell_bboxes[r_idx][0]
        assert (cb.x0, cb.x1) == (wrapper_x0, wrapper_x1)
    expected_y_pairs = [
        (142, 160), (160, 178), (178, 196), (196, 226),
        (226, 244), (244, 262), (262, 280), (280, 284),
    ]
    actual = [(w.cell_bboxes[r][0].y0, w.cell_bboxes[r][0].y1) for r in range(2, 10)]
    assert actual == expected_y_pairs, (
        f"placeholder y-pairs mismatch:\n  expected: {expected_y_pairs}\n  actual:   {actual}"
    )


def test_wrapper_skips_placeholder_expansion_when_inner_h_lines_too_narrow():
    """Inner sub-tables whose width < 50% of wrapper width do NOT contribute
    placeholder rows.  Mirrors fixture 17 per-page wrapper: wrapper width
    400, inner sub-table width 180 (45%) -- below the 50% gate, so the
    per-page wrapper stays 2x1.
    """
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        Cell(bbox=bb(106, 118, 506, 138), text="Section Header", source="line", confidence=1.0),
        Cell(bbox=bb(106, 138, 506, 712), text="container",      source="line", confidence=1.0),
        Cell(bbox=bb(112, 142, 202, 160), text="Item",   source="line", confidence=1.0),
        Cell(bbox=bb(202, 142, 292, 160), text="Qty",    source="line", confidence=1.0),
        Cell(bbox=bb(112, 160, 202, 178), text="Widget A", source="line", confidence=1.0),
        Cell(bbox=bb(202, 160, 292, 178), text="10",     source="line", confidence=1.0),
        Cell(bbox=bb(112, 178, 202, 196), text="Widget B", source="line", confidence=1.0),
        Cell(bbox=bb(202, 178, 292, 196), text="5",      source="line", confidence=1.0),
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 1
    w = tables[0]
    assert len(w.grid) == 2, f"expected 2-row wrapper, got {len(w.grid)}"
