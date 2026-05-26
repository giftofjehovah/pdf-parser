"""Cells containing both a nested table AND a between-text paragraph keep both.

Targets ``16_text_between_subtables`` where a cell holds two sub-tables with a
paragraph between them.  The between-text plumbing lives in
``_celltable_to_docnode``; this test locks the wiring end-to-end now that the
outer-frame reconstruction (``aggregate_tables._carve_container_frames`` +
``_build_single_col_wrapper`` in the Phase 10 prep step) emits the outer 1xN
wrapper with the sub-tables and paragraph nested inside its content cell.
"""
from pathlib import Path

from pdf_parser.pipeline import parse

def test_16_keeps_between_text() -> None:
    pdf = Path("tests/golden/synthetic/16_text_between_subtables/source.pdf")
    tree = parse(pdf)
    has_mixed = False

    def walk(n) -> None:
        nonlocal has_mixed
        if n.kind == "cell":
            kinds = {c.kind for c in n.children}
            if "table" in kinds and ("paragraph" in kinds or "list_item" in kinds):
                has_mixed = True
        for c in n.children:
            walk(c)

    walk(tree)
    assert has_mixed, "Expected a cell with both a nested table and between-text"
