from pathlib import Path

from pdf_parser.stages.detect_tables import detect_tables

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"
FIXTURE_14 = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "14_borderless_table" / "source.pdf"
FIXTURE_15 = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "15_multicolumn_text" / "source.pdf"


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


def test_borderless_table_detected_via_text_fallback():
    """Line strategy finds nothing; text fallback must detect the table."""
    regions = detect_tables(FIXTURE_14)
    assert len(regions) == 1, f"expected 1 table region, got {len(regions)}"
    assert regions[0].grid[0] == ["Name", "Score", "Grade"]
    assert len(regions[0].grid) == 4  # 1 header + 3 data rows


def test_borderless_table_has_correct_column_count():
    regions = detect_tables(FIXTURE_14)
    assert all(len(row) == 3 for row in regions[0].grid)


def test_multicolumn_paragraph_text_not_detected_as_table():
    """Text strategy must not misidentify multi-column paragraph text as a table."""
    regions = detect_tables(FIXTURE_15)
    assert regions == [], (
        f"False-positive: {len(regions)} table(s) detected in paragraph-text fixture. "
        "_MAX_CELL_TEXT_CHARS may need lowering."
    )


def test_business_style_headers_detected():
    """Borderless table with descriptive headers must be detected.

    Pins _MAX_CELL_TEXT_CHARS to a value that accepts headers like
    'Product Name' / 'Unit Price' / 'Quantity' (avg ~8 chars).
    """
    import tempfile

    # Build a temporary borderless table with longer headers.
    data_rows = [
        ["Product Name", "Unit Price", "Quantity"],
        ["Widget A",     "$25.99",    "12"],
        ["Gadget B",     "$8.50",     "100"],
    ]
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        tmp = Path(f.name)

    # Build a minimal borderless PDF with these headers.
    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
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
        regions = detect_tables(tmp)
        assert len(regions) == 1, (
            f"Business-header borderless table not detected. "
            f"_MAX_CELL_TEXT_CHARS may be too low."
        )
        assert regions[0].grid[0] == ["Product Name", "Unit Price", "Quantity"]
    finally:
        tmp.unlink(missing_ok=True)


def test_bordered_cell_with_bulleted_prose_is_not_shredded() -> None:
    """Regression for fixture ``23_bordered_cell_with_bulleted_prose``.

    A bordered 1×2 outer table whose right cell contains wrapped justified
    bulleted prose used to be shredded into a fake many-column nested table
    by the text-strategy fallback — pdfplumber detects vertical whitespace
    lanes through the wrapped text, producing mid-word cell splits like
    ``'Sec'|'tion'``, ``'Lorem ipsu'|'m d'|'ol'|'or sit a'``, with
    ``(cid:127)`` bullets stuck in column 1.

    See ``tests/golden/synthetic/23_bordered_cell_with_bulleted_prose/`` for
    the source PDF and committed golden tree.  The fix lives in
    :func:`pdf_parser.stages.detect_tables._is_text_strategy_table`: shred
    is identified by the dominant fraction of cells starting with a
    lowercase letter (real table values overwhelmingly start with uppercase,
    digits, or symbols).
    """
    from pdf_parser.pipeline import parse as parse_pdf
    from pdf_parser.model import DocNode

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
        f"Shapes: {[(t.attrs.get('n_rows'), t.attrs.get('n_cols')) for t in tables]}. "
        "Right cell was likely shredded by text-strategy fallback — see "
        "_MAX_LOWERCASE_START_RATIO in pdf_parser.stages.detect_tables."
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
