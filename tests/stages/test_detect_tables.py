from pathlib import Path

from pdf_parser.stages.detect_tables import detect_tables

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"


def test_detects_one_table_on_first_page():
    regions = detect_tables(FIXTURE)
    assert len(regions) == 1
    region = regions[0]
    assert region.page_index == 0


def test_table_has_three_rows_three_columns():
    regions = detect_tables(FIXTURE)
    grid = regions[0].grid
    assert len(grid) == 3
    assert all(len(row) == 3 for row in grid)


def test_header_row_extracted():
    regions = detect_tables(FIXTURE)
    grid = regions[0].grid
    assert grid[0] == ["Name", "Quantity", "Price"]


def test_table_bbox_has_positive_area():
    regions = detect_tables(FIXTURE)
    b = regions[0].bbox
    assert b.x1 > b.x0 and b.y1 > b.y0
