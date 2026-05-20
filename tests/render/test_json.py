import json
from pathlib import Path

from pdf_parser.model import DocNode
from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_json_is_valid_json():
    tree = parse(SIMPLE)
    s = to_json(tree)
    json.loads(s)  # no error


def test_json_round_trip_preserves_ids():
    tree = parse(SIMPLE)
    s = to_json(tree)
    restored = DocNode.model_validate_json(s)
    assert restored.id == tree.id


def test_json_pretty_is_stable():
    tree = parse(SIMPLE)
    a = to_json(tree, indent=2)
    b = to_json(tree, indent=2)
    assert a == b
