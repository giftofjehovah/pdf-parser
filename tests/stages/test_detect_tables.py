"""Per-fixture detection-quality tests for the bottom-up extractor.

Asserts the public ``extract_tables_v2.extract_tables`` returns one
``DocNode(kind='table')`` per source table with the correct shape, headers,
and bbox — and that prose / shredded layouts do NOT surface as spurious
tables (the load-bearing negative assertions: fixture 15 multicolumn
paragraphs, fixture 23 bordered cell with bulleted prose).
"""
from pathlib import Path

from pdf_parser.model import DocNode
from pdf_parser.stages.extract_tables_v2 import extract_tables

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"
FIXTURE_14 = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "14_borderless_table" / "source.pdf"
FIXTURE_15 = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "15_multicolumn_text" / "source.pdf"


def _grid(table: DocNode) -> list[list[str]]:
    """row × col → cell text (None → '')."""
    return [
        [(cell.text or "") for cell in row.children if cell.kind == "cell"]
        for row in table.children if row.kind == "row"
    ]


def test_detects_one_table_on_first_page():
    tables = extract_tables(FIXTURE)
    assert len(tables) == 1
    assert tables[0].attrs["page"] == 0


def test_table_has_three_rows_three_columns():
    tables = extract_tables(FIXTURE)
    grid = _grid(tables[0])
    assert len(grid) == 3
    assert all(len(row) == 3 for row in grid)


def test_header_row_extracted():
    tables = extract_tables(FIXTURE)
    grid = _grid(tables[0])
    assert grid[0] == ["Name", "Quantity", "Price"]


def test_table_bbox_has_positive_area():
    tables = extract_tables(FIXTURE)
    b = tables[0].bbox
    assert b.x1 > b.x0 and b.y1 > b.y0


def test_borderless_table_detected_via_text_fallback():
    """Fixture 14 has no rendered lines.  The bottom-up extractor's
    gutter / text-strategy fallback (``detect_cells._gutter_cells`` and
    ``_text_cells``) must still find the table.
    """
    tables = extract_tables(FIXTURE_14)
    assert len(tables) == 1, f"expected 1 table, got {len(tables)}"
    grid = _grid(tables[0])
    assert grid[0] == ["Name", "Score", "Grade"]
    assert len(grid) == 4  # 1 header + 3 data rows


def test_borderless_table_has_correct_column_count():
    tables = extract_tables(FIXTURE_14)
    grid = _grid(tables[0])
    assert all(len(row) == 3 for row in grid)


def test_multicolumn_paragraph_text_not_detected_as_table():
    """Negative regression: multi-column paragraph layout (fixture 15)
    must NOT surface as a table.  The text-strategy fallback's
    prose-rejection guard (``detect_cells._is_gutter_table_shape``) is
    what keeps the false-positive count at zero here.
    """
    tables = extract_tables(FIXTURE_15)
    assert tables == [], (
        f"False-positive: {len(tables)} table(s) detected in paragraph-text fixture."
    )


def test_business_style_headers_detected():
    """Borderless table with descriptive headers (avg ~8 chars per cell)
    must still be detected.  Pins the gutter / text-strategy fallback's
    accept threshold high enough to take long-text headers like
    ``Product Name`` / ``Unit Price`` / ``Quantity``.
    """
    import tempfile

    data_rows = [
        ["Product Name", "Unit Price", "Quantity"],
        ["Widget A",     "$25.99",    "12"],
        ["Gadget B",     "$8.50",     "100"],
    ]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = Path(f.name)

    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    doc = SimpleDocTemplate(str(tmp), pagesize=LETTER)
    t = Table(data_rows, colWidths=[140, 80, 80])
    t.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    doc.build([t])

    try:
        tables = extract_tables(tmp)
        assert len(tables) == 1, (
            f"Business-header borderless table not detected; got {len(tables)} tables."
        )
        grid = _grid(tables[0])
        assert grid[0] == ["Product Name", "Unit Price", "Quantity"]
    finally:
        tmp.unlink(missing_ok=True)


def test_bordered_cell_with_bulleted_prose_is_not_shredded() -> None:
    """Regression for fixture ``23_bordered_cell_with_bulleted_prose``.

    A bordered 1×2 outer table whose right cell holds wrapped justified
    bulleted prose used to be shredded into a fake many-column nested table
    by the text-strategy fallback — pdfplumber detects vertical whitespace
    lanes through wrapped text, producing mid-word cell splits like
    ``'Sec'|'tion'``, ``'Lorem ipsu'|'m d'|'ol'|'or sit a'``, with
    ``(cid:127)`` bullets stuck in column 1.

    The fix lives in ``detect_cells``' lowercase-start-ratio prose-rejection
    guard: shred is identified by the dominant fraction of cells starting
    with a lowercase letter (real table values overwhelmingly start with
    uppercase, digits, or symbols).
    """
    from pdf_parser.pipeline import parse as parse_pdf

    pdf = Path(__file__).resolve().parents[1] / "golden" / "synthetic" \
        / "23_bordered_cell_with_bulleted_prose" / "source.pdf"
    assert pdf.exists(), (
        f"Fixture missing: {pdf}. "
        "Run `python -m tests.fixtures.build_pdfs` to regenerate."
    )

    tree = parse_pdf(pdf)

    def all_tables(n: DocNode) -> list[DocNode]:
        out = [n] if n.kind == "table" else []
        for c in n.children:
            out.extend(all_tables(c))
        return out

    tables = all_tables(tree)
    # Outer 1×2 wrapper is the only legitimate table.  A fake inner table
    # from text-strategy shredding would push this to 2+.
    assert len(tables) == 1, (
        f"Expected only the outer 1×2 wrapper table, got {len(tables)}. "
        f"Shapes: {[(t.attrs.get('n_rows'), t.attrs.get('n_cols')) for t in tables]}."
    )
    assert tables[0].attrs.get("n_rows") == 1
    assert tables[0].attrs.get("n_cols") == 2

    # Pin one cell's content so a regression that swaps real text for
    # shredded fragments fails loudly.  The right cell carries every
    # heading + intro + bullet as concatenated lines.
    right_cell = tables[0].children[0].children[1]
    assert right_cell.text is not None and "Lorem ipsum" in right_cell.text, (
        f"Right cell text was lost: {right_cell.text!r}"
    )
