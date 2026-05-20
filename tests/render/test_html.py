from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.render.html import to_html

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")


def test_html_has_article_root():
    html = to_html(parse(SIMPLE))
    assert html.startswith("<article")


def test_simple_table_renders_as_native_table():
    html = to_html(parse(SIMPLE))
    assert "<table>" in html
    assert "<th>Name</th>" in html or "<td>Name</td>" in html


def test_nested_html_table_is_native():
    html = to_html(parse(NESTED))
    # Find an inner <table> nested inside a <td>
    assert "<td><table>" in html.replace(" ", "")
