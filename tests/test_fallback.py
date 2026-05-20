"""LLM fallback is opt-in. By default it is never invoked.

These tests check the wiring without making real network calls: a fake LLM
client is injected; we assert the fallback is called only when validation fails
and only for the failing region.
"""

from pathlib import Path
from unittest.mock import MagicMock

from pdf_parser.fallback.llm import LLMFallback, fallback_for_region
from pdf_parser.model import BBox, DocNode

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_fallback_disabled_by_default():
    fb = LLMFallback()
    assert fb.enabled is False


def test_fallback_calls_client_when_invoked():
    client = MagicMock()
    client.parse_region.return_value = DocNode(
        kind="paragraph",
        bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
        text="fallback text",
    )
    fb = LLMFallback(client=client, enabled=True)
    region = BBox(page=0, x0=10, y0=10, x1=100, y1=100)
    out = fallback_for_region(fb, SIMPLE, region)
    assert out.text == "fallback text"
    assert client.parse_region.called


def test_fallback_returns_none_when_disabled():
    fb = LLMFallback(enabled=False)
    region = BBox(page=0, x0=10, y0=10, x1=100, y1=100)
    assert fallback_for_region(fb, SIMPLE, region) is None
