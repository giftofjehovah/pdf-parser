"""use_bottom_up flag is accepted by parse() and defaults to True (bottom-up path).

This file is retained only for the duration of the Phase-10 cutover.  Once the
``use_bottom_up`` parameter is removed in Step 4 it goes with it.
"""
import inspect
from pathlib import Path
from pdf_parser.pipeline import parse


def test_parse_accepts_use_bottom_up_kwarg():
    sig = inspect.signature(parse)
    assert "use_bottom_up" in sig.parameters
    assert sig.parameters["use_bottom_up"].default is True


def test_parse_default_path_is_bottom_up():
    """With the flag at its default, output equals explicit bottom-up output (identity)."""
    pdf = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    default = parse(pdf)
    explicit = parse(pdf, use_bottom_up=True)
    assert default.id == explicit.id
