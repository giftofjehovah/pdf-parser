from pathlib import Path

from pdf_parser.pipeline import parse

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")
SPAN = Path("tests/golden/synthetic/03_page_spanning/source.pdf")


def test_parse_simple_returns_document():
    tree = parse(SIMPLE)
    assert tree.kind == "document"


def test_parse_nested_preserves_nesting():
    tree = parse(NESTED)
    tables = [n for n in _walk(tree) if n.kind == "table"]
    nested = [n for n in tables if any(
        c.kind == "table" for row in n.children for cell in row.children for c in cell.children
    )]
    assert len(nested) >= 1


def test_parse_span_produces_single_table():
    tree = parse(SPAN)
    tables = [n for n in _walk(tree) if n.kind == "table"]
    assert len(tables) == 1


def test_parse_deterministic_same_id():
    a = parse(SIMPLE)
    b = parse(SIMPLE)
    assert a.id == b.id
    assert [n.id for n in _walk(a)] == [n.id for n in _walk(b)]


def _walk(n):
    yield n
    for c in n.children:
        yield from _walk(c)
