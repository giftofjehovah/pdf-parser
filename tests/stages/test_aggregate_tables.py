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
