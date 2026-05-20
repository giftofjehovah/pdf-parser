"""Hypothesis property test: tree_skeleton(parse(SIMPLE)) is stable across re-parses."""

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")
SPAN = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
FIXTURES = [SIMPLE, NESTED, SPAN]


@given(st.integers(min_value=0, max_value=2))
@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_repeated_parses_produce_identical_json(idx):
    pdf = FIXTURES[idx]
    j1 = to_json(parse(pdf))
    j2 = to_json(parse(pdf))
    assert j1 == j2
