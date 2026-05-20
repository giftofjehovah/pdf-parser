from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.validate.invariants import (
    check_cross_page_integrity, check_reading_order,
    check_table_shape, check_well_formedness,
)
from pdf_parser.validate.report import ValidationReport, validate

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")
SPAN = Path("tests/golden/synthetic/03_page_spanning/source.pdf")


def test_well_formedness_simple():
    tree = parse(SIMPLE)
    assert check_well_formedness(tree) == []


def test_table_shape_passes_for_uniform_table():
    tree = parse(SIMPLE)
    assert check_table_shape(tree) == []


def test_reading_order_monotonic():
    tree = parse(SIMPLE)
    assert check_reading_order(tree) == []


def test_cross_page_integrity_for_spanned_table():
    tree = parse(SPAN)
    assert check_cross_page_integrity(tree) == []


def test_validate_returns_report():
    tree = parse(NESTED)
    report: ValidationReport = validate(tree, NESTED)
    assert report.passed
    assert report.errors == []
