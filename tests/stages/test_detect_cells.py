"""Cell dataclass shape: bbox, text, source, confidence."""
from pathlib import Path

import pdfplumber

from pdf_parser.model import BBox
from pdf_parser.stages.detect_cells import Cell, _line_cells, _group_words_into_lines, _find_column_gutters, _gutter_cells



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


def test_line_cells_on_01_simple_table():
    pdf_path = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _line_cells(page, page_index=0)
    # 01_simple_table = 3 rows × 3 cols = 9 line-bounded cells.
    assert len(cells) == 9
    assert all(c.source == "line" for c in cells)
    assert all(c.confidence == 1.0 for c in cells)
    # Header row contains "Name"/"Quantity"/"Price" (any order in detected set).
    texts = {c.text for c in cells}
    assert {"Name", "Quantity", "Price"} <= texts

def test_word_lines_y_bucketed():
    words = [
        {"x0": 10, "x1": 30, "top": 100, "bottom": 110, "text": "Hello"},
        {"x0": 40, "x1": 60, "top": 101, "bottom": 111, "text": "world"},
        {"x0": 10, "x1": 30, "top": 130, "bottom": 140, "text": "Next"},
    ]
    lines = _group_words_into_lines(words, tol=2.0)
    assert len(lines) == 2
    assert [w["text"] for w in lines[0]] == ["Hello", "world"]
    assert [w["text"] for w in lines[1]] == ["Next"]


def test_word_lines_keeps_third_line_intact_after_line_break():
    """After a line break, cur_y must reset so the next word in the new line
    is bucketed correctly. Regression for the running-average bug where the
    update fired in both branches and contaminated the new-line centroid."""
    words = [
        {"x0": 10, "x1": 30, "top": 100, "bottom": 100, "text": "A"},
        {"x0": 10, "x1": 30, "top": 130, "bottom": 130, "text": "B1"},
        {"x0": 40, "x1": 60, "top": 131, "bottom": 131, "text": "B2"},
    ]
    lines = _group_words_into_lines(words, tol=2.0)
    assert len(lines) == 2
    assert [w["text"] for w in lines[0]] == ["A"]
    assert [w["text"] for w in lines[1]] == ["B1", "B2"]


def test_word_lines_handles_identical_y_midpoints():
    """Two words sharing top/bottom must not raise TypeError from a dict
    comparison falling through the sort key. Regression for the
    (ymid, dict) sort key."""
    words = [
        {"x0": 40, "x1": 60, "top": 100, "bottom": 110, "text": "B"},
        {"x0": 10, "x1": 30, "top": 100, "bottom": 110, "text": "A"},
    ]
    lines = _group_words_into_lines(words, tol=2.0)
    assert len(lines) == 1
    assert [w["text"] for w in lines[0]] == ["A", "B"]

def test_gutters_three_columns():
    """Three text columns with consistent inter-column whitespace.

    Each row's words: [Name        Score   Grade] at fixed x-ranges.
    """
    line_words: list[list[dict]] = []
    for y in (100.0, 120.0, 140.0, 160.0):
        line_words.append([
            {"x0": 50, "x1": 90, "top": y, "bottom": y + 8, "text": "Alice"},
            {"x0": 150, "x1": 170, "top": y, "bottom": y + 8, "text": "95"},
            {"x0": 220, "x1": 230, "top": y, "bottom": y + 8, "text": "A"},
        ])
    gutters = _find_column_gutters(line_words, min_run=3, min_gap_pt=8.0)
    # Two inter-column gutters → 3 column ranges.
    assert len(gutters) == 2

def test_gutter_cells_on_14_borderless_table():
    pdf_path = Path("tests/golden/synthetic/14_borderless_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _gutter_cells(page, page_index=0)
    assert cells, "gutter detector must find cells on a borderless table"
    assert all(c.source == "gutter" for c in cells)
