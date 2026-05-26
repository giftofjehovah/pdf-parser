"""extract_tables_v2 emits one DocNode per detected line-bounded table."""
from pathlib import Path

from pdf_parser.stages.extract_tables_v2 import extract_tables


def test_v2_on_01_simple_table_returns_one_table():
    pdf = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    tables = extract_tables(pdf)
    assert len(tables) == 1
    t = tables[0]
    assert t.kind == "table"
    # Real fixture: 3-row, 3-col, Name/Quantity/Price. Plan said 4/Name/Score/Grade in error.
    assert t.attrs["n_rows"] == 3
    assert t.attrs["n_cols"] == 3
    assert t.attrs["header_signature"] == ("Name", "Quantity", "Price")
    assert t.provenance == {"extractor": "bottom_up", "stage": "extract_tables_v2"}
    # row → cell hierarchy
    assert all(r.kind == "row" for r in t.children)
    assert all(c.kind == "cell" for r in t.children for c in r.children)


def test_v2_emits_no_tables_on_text_only_pdf():
    """12_image_chart has no tables — extractor returns []."""
    pdf = Path("tests/golden/synthetic/12_image_chart/source.pdf")
    assert extract_tables(pdf) == []


def test_cell_align_right_when_text_hugs_right_edge():
    """``_cell_align`` returns 'right' when chars sit near the right wall.

    Threshold: right_gap < 30 % of left_gap AND right_gap < 6 pt absolute.
    Cell width 60 pt, chars span 50..58 → left_gap 50, right_gap 2 → right.
    """
    from pdf_parser.model import BBox
    from pdf_parser.stages.extract_tables_v2 import _cell_align

    cbox = BBox(page=0, x0=0, y0=0, x1=60, y1=10)
    chars = [
        {"x0": 50, "x1": 54, "top": 1, "bottom": 9, "text": "1"},
        {"x0": 54, "x1": 58, "top": 1, "bottom": 9, "text": "2"},
    ]
    assert _cell_align(chars, cbox) == "right"


def test_cell_align_left_when_text_hugs_left_edge():
    from pdf_parser.model import BBox
    from pdf_parser.stages.extract_tables_v2 import _cell_align

    cbox = BBox(page=0, x0=0, y0=0, x1=60, y1=10)
    chars = [
        {"x0": 2, "x1": 6, "top": 1, "bottom": 9, "text": "L"},
        {"x0": 6, "x1": 10, "top": 1, "bottom": 9, "text": "x"},
    ]
    assert _cell_align(chars, cbox) == "left"


def test_cell_align_left_when_text_centered():
    """Centered text has roughly equal gaps — not right-aligned."""
    from pdf_parser.model import BBox
    from pdf_parser.stages.extract_tables_v2 import _cell_align

    cbox = BBox(page=0, x0=0, y0=0, x1=60, y1=10)
    chars = [
        {"x0": 26, "x1": 30, "top": 1, "bottom": 9, "text": "F"},
        {"x0": 30, "x1": 34, "top": 1, "bottom": 9, "text": "Y"},
    ]
    assert _cell_align(chars, cbox) == "left"


def test_cell_align_left_when_empty():
    """Empty cells (no chars inside) default to left."""
    from pdf_parser.model import BBox
    from pdf_parser.stages.extract_tables_v2 import _cell_align

    cbox = BBox(page=0, x0=0, y0=0, x1=60, y1=10)
    assert _cell_align([], cbox) == "left"
    # Chars present but outside the cell — still left.
    chars = [{"x0": 100, "x1": 104, "top": 1, "bottom": 9, "text": "X"}]
    assert _cell_align(chars, cbox) == "left"
