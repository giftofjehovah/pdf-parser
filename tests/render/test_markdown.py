from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.render.markdown import to_markdown

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")


def test_simple_markdown_has_heading_and_pipe_table():
    md = to_markdown(parse(SIMPLE))
    assert "# Simple Table Example" in md
    assert "| Name | Quantity | Price |" in md
    assert "| --- | --- | --- |" in md


def test_simple_markdown_has_no_inline_html():
    md = to_markdown(parse(SIMPLE))
    assert "<table>" not in md


def test_nested_table_falls_back_to_inline_html():
    md = to_markdown(parse(NESTED))
    # Nested → at least one cell rendered as inline HTML <table>
    assert "<table>" in md
