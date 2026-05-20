"""Aggregator: runs all Layer 1 checks; produces a ValidationReport."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pdf_parser.model import DocNode
from pdf_parser.validate.coverage import coverage_diff
from pdf_parser.validate.invariants import (
    check_cross_page_integrity, check_reading_order,
    check_table_shape, check_well_formedness,
)


@dataclass
class ValidationReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
    coverage_missing: str = ""
    coverage_extra: str = ""


def _raw_text(pdf_path: Path) -> str:
    doc = pymupdf.open(str(pdf_path))
    try:
        return "".join(page.get_text() for page in doc)
    finally:
        doc.close()


def validate(tree: DocNode, pdf_path: Path | str) -> ValidationReport:
    errors: list[str] = []
    errors += check_well_formedness(tree)
    errors += check_table_shape(tree)
    errors += check_reading_order(tree)
    errors += check_cross_page_integrity(tree)

    diff = coverage_diff(tree, _raw_text(Path(pdf_path)))
    if diff.missing:
        errors.append(f"coverage missing: {diff.missing[:80]!r}")

    return ValidationReport(
        passed=not errors,
        errors=errors,
        coverage_missing=diff.missing,
        coverage_extra=diff.extra,
    )
