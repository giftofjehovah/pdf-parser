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

FIXTURE_14 = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "14_borderless_table" / "source.pdf"


def test_borderless_table_detected_via_text_fallback():
    """Line strategy finds nothing; text fallback must detect the table."""
    regions = detect_tables(FIXTURE_14)
    assert len(regions) == 1, f"expected 1 table region, got {len(regions)}"
    assert regions[0].grid[0] == ["Name", "Score", "Grade"]
    assert len(regions[0].grid) == 4  # 1 header + 3 data rows


def test_borderless_table_has_correct_column_count():
    regions = detect_tables(FIXTURE_14)
    assert all(len(row) == 3 for row in regions[0].grid)
