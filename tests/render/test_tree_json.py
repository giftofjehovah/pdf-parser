"""Tests for the simplified ``to_tree_json`` renderer.

Invariant: emits hierarchy + content only.  Drops bbox, provenance, id,
and every ``attrs`` field that does not describe content shape.  Drops
rowspan-placeholder rows and rowspan-covered cells.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_tree_json

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")
SPANNING = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
BETWEEN = Path("tests/golden/synthetic/16_text_between_subtables/source.pdf")


def _load(pdf: Path) -> dict:
    return json.loads(to_tree_json(parse(pdf)))


def _walk(node):
    yield node
    for c in node.get("children", []):
        yield from _walk(c)


# --------------------------------------------------------------------------- #
# universal: no positional / provenance / id metadata anywhere                #
# --------------------------------------------------------------------------- #


_FORBIDDEN_KEYS = {"bbox", "provenance", "id", "attrs"}


@pytest.mark.parametrize("pdf", [SIMPLE, NESTED, SPANNING, BETWEEN])
def test_no_forbidden_metadata(pdf):
    root = _load(pdf)
    for node in _walk(root):
        leaked = _FORBIDDEN_KEYS & set(node.keys())
        assert not leaked, f"node {node!r} leaks {leaked}"


@pytest.mark.parametrize("pdf", [SIMPLE, NESTED, SPANNING, BETWEEN])
def test_every_node_has_kind(pdf):
    root = _load(pdf)
    for node in _walk(root):
        assert "kind" in node
        assert isinstance(node["kind"], str)


# --------------------------------------------------------------------------- #
# per-kind shape attributes                                                   #
# --------------------------------------------------------------------------- #


def test_simple_table_has_heading_level_and_table_shape():
    root = _load(SIMPLE)
    [page] = root["children"]
    assert page["kind"] == "page" and page["index"] == 0
    heading = page["children"][0]
    assert heading == {
        "kind": "heading", "level": 1, "text": "Simple Table Example",
    }
    table = next(c for c in page["children"] if c["kind"] == "table")
    assert table["shape"] == "3x3"
    # No id / bbox / spans_pages leaked
    assert set(table.keys()) <= {"kind", "shape", "children"}


def test_spanning_table_carries_spans_pages():
    root = _load(SPANNING)
    table = next(n for n in _walk(root) if n["kind"] == "table")
    assert table["spans_pages"] == [0, 1]
    assert table["shape"] == "51x3"


def test_nested_table_appears_under_outer_cell():
    root = _load(NESTED)
    outer = next(n for n in _walk(root) if n["kind"] == "table")
    # Inner table sits inside an outer cell as a child.
    inners = [n for n in _walk(outer)
              if n["kind"] == "table" and n is not outer]
    assert len(inners) == 1
    inner = inners[0]
    assert inner["shape"] == "3x2"


# --------------------------------------------------------------------------- #
# parser-artifact filtering                                                   #
# --------------------------------------------------------------------------- #


def test_placeholder_rows_filtered_in_between_fixture():
    """Fixture 16's outer table has 8 all-covered rowspan-placeholder rows
    sandwiched between the content cell and the footer.  The simplified
    tree must hide them."""
    root = _load(BETWEEN)
    outer = next(n for n in _walk(root) if n["kind"] == "table")
    # Three logical rows survive: header, mixed-content body, footer.
    assert len(outer["children"]) == 3
    assert outer["shape"] == "3x1"


def test_covered_cells_filtered_when_any_present():
    """When a cell carries ``covered=True`` (rowspan continuation), the
    simplified tree must drop it from its row's children."""
    root = _load(BETWEEN)
    for row in (n for n in _walk(root) if n["kind"] == "row"):
        for cell in row.get("children", []):
            # Cells in the simplified tree never carry the covered flag,
            # and visible-cell shape attrs are positive integers only.
            assert "covered" not in cell
            for k in ("colspan", "rowspan"):
                if k in cell:
                    assert cell[k] > 1


# --------------------------------------------------------------------------- #
# output is valid JSON, stable insertion order                                #
# --------------------------------------------------------------------------- #


def test_output_is_valid_json_and_starts_with_kind():
    raw = to_tree_json(parse(SIMPLE), indent=2)
    parsed = json.loads(raw)
    assert parsed["kind"] == "document"
    # Insertion order puts "kind" before "children"
    first_key = raw.lstrip("{\n ").split('"')[1]
    assert first_key == "kind"


def test_indent_none_compresses():
    raw = to_tree_json(parse(SIMPLE), indent=None)
    assert "\n" not in raw
