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
