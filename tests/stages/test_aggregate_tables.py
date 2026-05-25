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
