"""Layer 2 regression for real-world (non-synthetic) golden fixtures.

Cases live in tests/golden/real_world/<name>/ alongside source.pdf and
expected_tree.json.  The corpus starts empty; cases are added via
scripts/add_real_world_fixture.py after human review.

The suite is skipped entirely when no cases exist, so CI is always green
before any real-world PDFs are committed.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json
from scripts.update_goldens import _strip_bbox_noise

CASES_DIR = Path("tests/golden/real_world")
CASES = (
    sorted(
        p.name
        for p in CASES_DIR.iterdir()
        if p.is_dir()
        and (p / "source.pdf").exists()
        and (p / "expected_tree.json").exists()
    )
    if CASES_DIR.exists()
    else []
)


@pytest.mark.skipif(not CASES, reason="no real-world fixtures added yet")
@pytest.mark.parametrize("case", CASES)
def test_real_world_tree_matches_expected(case: str) -> None:
    case_dir = CASES_DIR / case
    expected = json.loads((case_dir / "expected_tree.json").read_text())
    got = _strip_bbox_noise(json.loads(to_json(parse(case_dir / "source.pdf"))))
    assert got == expected, (
        f"Tree drift in real-world fixture '{case}'. "
        f"Run `python scripts/add_real_world_fixture.py --update {case}` "
        "and review the diff before committing."
    )
