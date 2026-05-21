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
        attrs={"page": 0},
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

    mock_anthropic = MagicMock()
    mock_message = MagicMock()
    mock_message.content = [MagicMock(text=fake_response_json)]
    mock_anthropic.messages.create.return_value = mock_message

    region = BBox(page=0, x0=0, y0=0, x1=612, y1=792)

    with patch("pdf_parser.fallback.llm._render_page_png", return_value=b"\x89PNG"):
        client = AnthropicLLMClient.__new__(AnthropicLLMClient)
        client._anthropic = mock_anthropic
        client._model = "claude-3-5-sonnet-20241022"
        result = client.parse_region(SIMPLE, region)

    assert result.kind == "page"
    kinds = [c.kind for c in result.children]
    assert "heading" in kinds
    assert "paragraph" in kinds
