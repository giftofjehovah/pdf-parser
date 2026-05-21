"""LLM fallback: unit tests covering the full wiring path."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pdf_parser.fallback.llm import (
    AnthropicLLMClient,
    LLMFallback,
    fallback_for_region,
)
from pdf_parser.model import BBox, DocNode
from pdf_parser.pipeline import parse

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


# ---- existing tests (keep) ----

def test_fallback_disabled_by_default():
    fb = LLMFallback()
    assert fb.enabled is False


def test_fallback_returns_none_when_disabled():
    fb = LLMFallback(enabled=False)
    region = BBox(page=0, x0=10, y0=10, x1=100, y1=100)
    assert fallback_for_region(fb, SIMPLE, region) is None


def test_fallback_calls_client_when_invoked():
    fake_node = DocNode(kind="paragraph", text="hello", bbox=BBox(page=0, x0=0, y0=0, x1=100, y1=100))
    client = MagicMock()
    client.parse_region.return_value = fake_node
    fb = LLMFallback(enabled=True, client=client)
    region = BBox(page=0, x0=0, y0=0, x1=612, y1=792)
    result = fallback_for_region(fb, SIMPLE, region)
    assert result is fake_node
    assert client.parse_region.called


# ---- new tests ----

def test_parse_passes_none_fallback_by_default():
    """pipeline.parse() must accept no llm_fallback kwarg."""
    tree = parse(SIMPLE)
    assert tree.kind == "document"


def test_parse_accepts_disabled_fallback():
    """Passing an explicitly disabled LLMFallback must be a no-op."""
    fb = LLMFallback(enabled=False)
    tree = parse(SIMPLE, llm_fallback=fb)
    assert tree.kind == "document"


def test_parse_invokes_fallback_for_empty_page():
    """Empty pages (no leaf text) trigger the fallback client."""
    fake_paragraph = DocNode(
        kind="paragraph",
        text="OCR recovered text",
        bbox=BBox(page=0, x0=72, y0=72, x1=540, y1=100),
    )
    client = MagicMock()
    client.parse_region.return_value = DocNode(
        kind="page",
        bbox=BBox(page=0, x0=0, y0=0, x1=612, y1=792),
        children=[fake_paragraph],
        attrs={"page": 0},
    )
    fb = LLMFallback(enabled=True, client=client)

    empty_page = DocNode(
        kind="page",
        bbox=BBox(page=0, x0=0, y0=0, x1=612, y1=792),
        attrs={"page_index": 0},  # what build_tree actually writes
    )
    empty_doc = DocNode(
        kind="document",
        bbox=BBox(page=0, x0=0, y0=0, x1=0, y1=0),
        children=[empty_page],
    )

    with patch("pdf_parser.pipeline.build_tree", return_value=empty_doc), \
         patch("pdf_parser.pipeline.ingest", return_value=[MagicMock(width=612.0, height=792.0)]), \
         patch("pdf_parser.pipeline.segment", return_value=[]), \
         patch("pdf_parser.pipeline.extract_tables", return_value=[]), \
         patch("pdf_parser.pipeline.stitch_tables", return_value=[]):
        tree = parse(SIMPLE, llm_fallback=fb)

    assert client.parse_region.called
    page_node = tree.children[0]
    assert any(n.text == "OCR recovered text" for n in page_node.children)


def test_anthropic_client_builds_docnode_from_response():
    """AnthropicLLMClient parses the LLM JSON response into a DocNode."""
    fake_response_json = json.dumps({
        "children": [
            {"kind": "heading", "level": 1, "text": "Title"},
            {"kind": "paragraph", "text": "Body text here."},
        ]
    })

    mock_client = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(type="text", text=fake_response_json)]
    mock_client.messages.create.return_value = mock_message

    region = BBox(page=0, x0=0, y0=0, x1=612, y1=792)

    with patch("pdf_parser.fallback.llm._render_page_png", return_value=b"\x89PNG"):
        client = AnthropicLLMClient(client=mock_client)
        result = client.parse_region(SIMPLE, region)

    assert result.kind == "page"
    kinds = [c.kind for c in result.children]
    assert "heading" in kinds
    assert "paragraph" in kinds


def test_parse_llm_response_no_duplicate_ids():
    """Repeated text in LLM output must not produce colliding node ids."""
    from pdf_parser.fallback.llm import _parse_llm_response
    from pdf_parser.validate.invariants import check_well_formedness

    region = BBox(page=0, x0=0, y0=0, x1=612, y1=792)
    response_json = json.dumps({
        "children": [
            {"kind": "paragraph", "text": "same text"},
            {"kind": "paragraph", "text": "same text"},
            {"kind": "paragraph", "text": ""},
            {"kind": "paragraph", "text": ""},
        ]
    })
    page_node = _parse_llm_response(response_json, region)
    ids = [c.id for c in page_node.children]
    assert len(ids) == len(set(ids)), f"Duplicate ids: {ids}"
    errors = check_well_formedness(page_node)
    assert not errors, f"Invariant violations: {errors}"


def test_anthropic_client_handles_table_response():
    """_parse_llm_response correctly builds a table DocNode from LLM output."""
    from pdf_parser.fallback.llm import _parse_llm_response

    region = BBox(page=0, x0=0, y0=0, x1=612, y1=792)
    response_json = json.dumps({
        "children": [{
            "kind": "table",
            "n_rows": 2,
            "n_cols": 3,
            "rows": [["H1", "H2", "H3"], ["a", "b", "c"]],
        }]
    })
    page_node = _parse_llm_response(response_json, region)
    assert len(page_node.children) == 1
    table = page_node.children[0]
    assert table.kind == "table"
    assert table.attrs["n_rows"] == 2
    assert table.attrs["n_cols"] == 3
    assert table.attrs["header_signature"] == ("H1", "H2", "H3")
    assert len(table.children) == 2  # 2 rows
    assert len(table.children[0].children) == 3  # 3 cells per row
    # all ids must be unique
    all_ids = [n.id for row in table.children for n in [row] + row.children]
    assert len(all_ids) == len(set(all_ids)), f"Duplicate row/cell ids: {all_ids}"
