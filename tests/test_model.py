import pytest
from pdf_parser.model import DocNode, BBox, MAX_DEPTH


def _para(text="hello", page=0):
    return DocNode(
        kind="paragraph",
        bbox=BBox(page=page, x0=0, y0=0, x1=10, y1=10),
        text=text,
    )


def test_create_paragraph():
    n = _para()
    assert n.kind == "paragraph"
    assert n.text == "hello"
    assert n.children == []


def test_id_is_deterministic():
    a = _para("same")
    b = _para("same")
    assert a.id == b.id
    assert len(a.id) == 12


def test_id_changes_with_text():
    assert _para("a").id != _para("b").id


def test_id_rounds_bbox():
    a = DocNode(kind="paragraph", bbox=BBox(page=0, x0=0.001, y0=0, x1=10, y1=10), text="x")
    b = DocNode(kind="paragraph", bbox=BBox(page=0, x0=0.002, y0=0, x1=10, y1=10), text="x")
    assert a.id == b.id  # rounded to 1pt


def test_table_must_contain_rows():
    cell = DocNode(kind="cell", bbox=BBox(page=0, x0=0, y0=0, x1=5, y1=5), text="c")
    with pytest.raises(ValueError, match="table children must all be row"):
        DocNode(
            kind="table",
            bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
            children=[cell],
        )


def test_row_must_contain_cells():
    para = _para()
    with pytest.raises(ValueError, match="row children must all be cell"):
        DocNode(
            kind="row",
            bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
            children=[para],
        )


def test_cell_may_contain_nested_table():
    inner_cell = DocNode(kind="cell", bbox=BBox(page=0, x0=0, y0=0, x1=5, y1=5), text="x")
    inner_row = DocNode(kind="row", bbox=BBox(page=0, x0=0, y0=0, x1=5, y1=5), children=[inner_cell])
    inner_table = DocNode(kind="table", bbox=BBox(page=0, x0=0, y0=0, x1=5, y1=5), children=[inner_row])
    outer_cell = DocNode(
        kind="cell",
        bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
        children=[inner_table],
    )
    assert outer_cell.children[0].kind == "table"


def test_depth_cap_enforced():
    # Build a chain deeper than MAX_DEPTH and assert it fails.
    leaf = _para()
    node = leaf
    for _ in range(MAX_DEPTH + 2):
        node = DocNode(
            kind="section",
            bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
            children=[node],
        )
    with pytest.raises(ValueError, match="exceeds max depth"):
        # depth is checked on assembly via the helper
        node.assert_invariants()


def test_serialization_roundtrip():
    n = _para("hello")
    data = n.model_dump()
    restored = DocNode.model_validate(data)
    assert restored.id == n.id
    assert restored.text == n.text
