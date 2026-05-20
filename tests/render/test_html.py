from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.render.html import to_html

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")


def test_html_is_full_document():
    html = to_html(parse(SIMPLE))
    assert html.startswith("<!DOCTYPE html>")
    assert "<style>" in html
    assert "</html>" in html


def test_simple_table_cells_present():
    html = to_html(parse(SIMPLE))
    # Header row cell and a data cell must appear
    assert "Name" in html
    assert "Apple" in html


def test_simple_table_has_grid_lines():
    html = to_html(parse(SIMPLE))
    # CSS must define cell border (grid lines)
    assert "border:" in html or "border :" in html


def test_nested_html_table_renders_inner_cells():
    html = to_html(parse(NESTED))
    # Both outer and inner cell text must be present
    assert "Outer-Col-1" in html
    assert "sub-A" in html


def test_page_div_is_sized():
    html = to_html(parse(SIMPLE))
    # Absolute-layout pages carry explicit pixel dimensions
    assert 'class="page"' in html
    assert "width:" in html and "height:" in html


def test_header_row_has_rH_class():
    html = to_html(parse(SIMPLE))
    assert "rH" in html
