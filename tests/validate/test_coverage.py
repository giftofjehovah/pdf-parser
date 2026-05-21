from pathlib import Path

import pypdfium2 as pdfium

from pdf_parser.pipeline import parse
from pdf_parser.validate.coverage import coverage_diff, coverage_ok

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def _raw_text(pdf_path: Path) -> str:
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        return "".join(page.get_textpage().get_text_bounded() for page in doc)
    finally:
        doc.close()


def test_coverage_passes_on_simple_table():
    tree = parse(SIMPLE)
    raw = _raw_text(SIMPLE)
    assert coverage_ok(tree, raw)


def test_coverage_returns_no_missing_chars():
    tree = parse(SIMPLE)
    raw = _raw_text(SIMPLE)
    diff = coverage_diff(tree, raw)
    # missing should be empty (or only contain whitelist tokens). extra may be empty.
    assert diff.missing == "", f"missing leaf text: {diff.missing!r}"
