from pathlib import Path

from pdf_parser.stages.ingest import ingest

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"


def test_ingest_returns_one_page():
    pages = ingest(FIXTURE)
    assert len(pages) == 1


def test_ingest_extracts_heading_text():
    pages = ingest(FIXTURE)
    texts = [s.text for s in pages[0].spans]
    assert any("Simple Table Example" in t for t in texts)


def test_ingest_spans_have_bbox_and_font():
    pages = ingest(FIXTURE)
    span = pages[0].spans[0]
    assert span.bbox.x1 > span.bbox.x0
    assert span.bbox.y1 > span.bbox.y0
    assert span.font_size > 0
    assert isinstance(span.font_name, str) and span.font_name


def test_ingest_captures_page_size():
    pages = ingest(FIXTURE)
    p = pages[0]
    # US Letter is 612x792 points; pymupdf returns floats.
    assert round(p.width) == 612
    assert round(p.height) == 792
