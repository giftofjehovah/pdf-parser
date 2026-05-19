from pathlib import Path

from pdf_parser.stages.extract_tables import extract_tables

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
