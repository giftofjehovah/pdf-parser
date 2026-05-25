"""Cells containing both a nested table AND a between-text paragraph keep both.

Targets ``16_text_between_subtables`` where a cell holds two sub-tables with a
paragraph between them.  The between-text plumbing lives in
``_celltable_to_docnode``; this test asserts the wiring runs end-to-end, not
that 16 itself reaches parity — full outer-frame reconstruction is a
follow-up.
"""
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse


@pytest.mark.xfail(
    reason="16 outer-frame reconstruction is a separate follow-up; this test "
           "locks the between-text plumbing once that lands.",
    strict=False,
)
def test_16_keeps_between_text() -> None:
    pdf = Path("tests/golden/synthetic/16_text_between_subtables/source.pdf")
    tree = parse(pdf, use_bottom_up=True)
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
