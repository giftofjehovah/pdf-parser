"""Cell dataclass shape: bbox, text, source, confidence."""
from pdf_parser.model import BBox
from pdf_parser.stages.detect_cells import Cell


def test_cell_holds_bbox_text_source_confidence():
    bb = BBox(page=0, x0=0, y0=0, x1=10, y1=10)
    c = Cell(bbox=bb, text="x", source="line", confidence=1.0)
    assert c.bbox == bb
    assert c.text == "x"
    assert c.source == "line"
    assert c.confidence == 1.0


def test_cell_source_is_constrained():
    bb = BBox(page=0, x0=0, y0=0, x1=10, y1=10)
    for src in ("line", "gutter", "text"):
        Cell(bbox=bb, text="", source=src, confidence=0.5)
