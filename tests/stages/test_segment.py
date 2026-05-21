from pathlib import Path

from pdf_parser.stages.ingest import ingest
from pdf_parser.stages.segment import segment

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"


def test_segment_produces_blocks():
    pages = ingest(FIXTURE)
    segs = segment(pages)
    assert len(segs) == 1
    assert len(segs[0].blocks) >= 2  # heading + body


def test_first_block_is_heading():
    pages = ingest(FIXTURE)
    segs = segment(pages)
    first = segs[0].blocks[0]
    assert first.kind_hint == "heading"
    assert "Simple Table Example" in first.text


def test_paragraph_block_detected():
    pages = ingest(FIXTURE)
    segs = segment(pages)
    paras = [b for b in segs[0].blocks if b.kind_hint == "paragraph"]
    assert any("three columns" in b.text for b in paras)


def test_blocks_in_reading_order():
    pages = ingest(FIXTURE)
    segs = segment(pages)
    # Blocks are in reading order when their row buckets (2pt y-bands) are
    # non-decreasing.  Using exact y0 is too strict: column-split produces
    # same-row blocks whose per-glyph tight bboxes differ by a fraction of a pt.
    buckets = [round(b.bbox.y0 / 2) for b in segs[0].blocks]
    assert buckets == sorted(buckets)
