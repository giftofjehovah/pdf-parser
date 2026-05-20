"""Layer 3: tree_skeleton(parse(pdf)) == expected_skeleton.json.

Drops bboxes, ids, attrs, provenance. Only kind/text/children remain.
This is the laser-focused check for 'tables-within-tables stayed nested.'
"""

import json
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json
from scripts.update_goldens import _skeleton

CASES_DIR = Path("tests/golden/synthetic")
CASES = sorted(p.name for p in CASES_DIR.iterdir() if (p / "source.pdf").exists())


def _tree_skeleton(tree):
    return _skeleton(json.loads(to_json(tree)))


@pytest.mark.parametrize("case", CASES)
def test_skeleton_matches_expected(case):
    case_dir = CASES_DIR / case
    pdf = case_dir / "source.pdf"
    expected = json.loads((case_dir / "expected_skeleton.json").read_text())
    got = _tree_skeleton(parse(pdf))
    assert got == expected, (
        f"Hierarchy drift in {case}. "
        f"Inspect tests/golden/synthetic/{case}/expected_skeleton.json vs current parse output."
    )


def test_nested_table_is_in_skeleton():
    """Sanity: 02_nested_table's skeleton actually has a table inside a cell."""
    sk = json.loads((CASES_DIR / "02_nested_table" / "expected_skeleton.json").read_text())

    def has_nested_table(node):
        if node.get("kind") == "cell":
            if any(c.get("kind") == "table" for c in node.get("children", [])):
                return True
        for c in node.get("children", []):
            if has_nested_table(c):
                return True
        return False

    assert has_nested_table(sk), "02_nested_table skeleton lost its nested table"
