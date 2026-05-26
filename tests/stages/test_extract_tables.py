from pathlib import Path

from pdf_parser.stages.extract_tables_v2 import extract_tables

SIMPLE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"
NESTED = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "02_nested_table" / "source.pdf"


def test_simple_table_no_nesting():
    tables = extract_tables(SIMPLE)
    assert len(tables) == 1
    t = tables[0]
    # 3 rows × 3 cells, no nested tables
    assert all(cell.children == [] or all(c.kind != "table" for c in cell.children)
               for row in t.children for cell in row.children)


def test_nested_table_detected_inside_cell():
    tables = extract_tables(NESTED)
    # Outer table should be present
    assert len(tables) >= 1
    outer = tables[0]
    nested_tables = [
        c for row in outer.children for cell in row.children for c in cell.children if c.kind == "table"
    ]
    assert len(nested_tables) == 1, f"expected 1 nested table, got {len(nested_tables)}"
    inner = nested_tables[0]
    # Inner table is 3×2
    assert len(inner.children) == 3
    assert all(len(row.children) == 2 for row in inner.children)


def test_nested_table_text_preserved():
    tables = extract_tables(NESTED)
    outer = tables[0]
    nested = [
        c for row in outer.children for cell in row.children for c in cell.children if c.kind == "table"
    ][0]
    cells_text = [cell.text for row in nested.children for cell in row.children]
    assert "sub-A" in cells_text
    assert "sub-B" in cells_text


MERGED = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "10_merged_cells" / "source.pdf"


def test_merged_cells_shape():
    """Table is detected with the correct number of rows and columns."""
    tables = extract_tables(MERGED)
    assert len(tables) == 1
    t = tables[0]
    # 5 logical rows: colspan header + sub-header + 2 rowspan rows + tail
    assert len(t.children) == 5
    # 3 columns throughout
    assert all(len(row.children) == 3 for row in t.children)


def test_merged_cells_colspan_text_not_duplicated():
    """'Quarterly Report' (colspan over all 3 cols) appears exactly once."""
    tables = extract_tables(MERGED)
    t = tables[0]
    all_texts = [cell.text for row in t.children for cell in row.children]
    matches = [text for text in all_texts if text and "Quarterly Report" in text]
    assert len(matches) == 1, f"expected 1 occurrence, got {len(matches)}: {matches}"


def test_merged_cells_colspan_in_first_column():
    """Colspan text lands in column 0 of its row (upper-left corner of span)."""
    tables = extract_tables(MERGED)
    t = tables[0]
    header_row = t.children[0]
    assert header_row.children[0].text == "Quarterly Report"
    assert not header_row.children[1].text  # spanned cols are empty
    assert not header_row.children[2].text


def test_merged_cells_rowspan_text_not_duplicated():
    """'North' (rowspan over 2 rows) appears exactly once."""
    tables = extract_tables(MERGED)
    t = tables[0]
    all_texts = [cell.text for row in t.children for cell in row.children]
    matches = [text for text in all_texts if text and "North" in text]
    assert len(matches) == 1, f"expected 1 occurrence, got {len(matches)}: {matches}"


def test_merged_cells_rowspan_in_first_row():
    """Rowspan text lands in the first row of the span; continuation row col 0 is empty."""
    tables = extract_tables(MERGED)
    t = tables[0]
    # Row 2 (index 2) carries 'North'; row 3 (index 3) col-0 is the continuation
    assert t.children[2].children[0].text == "North"
    assert not t.children[3].children[0].text


def test_merged_cells_data_values_preserved():
    """All data cell values survive extraction without being dropped or mixed up."""
    tables = extract_tables(MERGED)
    t = tables[0]
    all_texts = {cell.text for row in t.children for cell in row.children if cell.text}
    for expected in ("Region", "Q1", "Q2", "North", "100", "200", "120", "180", "South", "300", "400"):
        assert expected in all_texts, f"'{expected}' missing from extracted cells"
