"""Layer 2: parse each golden PDF, assert tree equals committed expected_tree.json."""

import json
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json
from scripts.update_goldens import _load_parser_config, _strip_bbox_noise

CASES_DIR = Path("tests/golden/synthetic")
CASES = sorted(p.name for p in CASES_DIR.iterdir() if (p / "source.pdf").exists())


@pytest.mark.parametrize("case", CASES)
def test_tree_matches_expected(case):
    case_dir = CASES_DIR / case
    pdf = case_dir / "source.pdf"
    expected = json.loads((case_dir / "expected_tree.json").read_text())
    got = _strip_bbox_noise(json.loads(to_json(parse(pdf, **_load_parser_config(case_dir)))))
    assert got == expected, (
        f"Tree drift in {case}. "
        f"Run `python scripts/update_goldens.py --case {case}` and review the diff."
    )
