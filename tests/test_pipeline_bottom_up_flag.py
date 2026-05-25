"""use_bottom_up flag is accepted by parse() and defaults to False (legacy path)."""
import inspect
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse


def test_parse_accepts_use_bottom_up_kwarg():
    sig = inspect.signature(parse)
    assert "use_bottom_up" in sig.parameters
    assert sig.parameters["use_bottom_up"].default is False


def test_parse_default_path_unchanged():
    """With the flag off, output equals legacy output (identity)."""
    pdf = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    legacy = parse(pdf)
    flagged = parse(pdf, use_bottom_up=False)
    assert legacy.id == flagged.id
