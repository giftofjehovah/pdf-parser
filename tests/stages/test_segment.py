from pathlib import Path

from pdf_parser.stages.ingest import ingest
from pdf_parser.stages.segment import segment

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"
FIXTURE_22 = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "22_text_between_adjacent_tables" / "source.pdf"


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


def test_wrapped_paragraph_joins_into_one_block():
    """A multi-line paragraph should produce ONE paragraph block, not N.

    Fixture 22 has a plain paragraph that wraps to three visual lines starting
    with "The Other Producers and Other Media cap is at 15% primarily to
    support transition projects..." (ends with "...section8.").  All three
    lines must be merged into a single paragraph block.
    """
    pages = ingest(FIXTURE_22)
    segs = segment(pages)
    paras = [b for b in segs[0].blocks if b.kind_hint == "paragraph"]
    matches = [p for p in paras if p.text.startswith("The Other Producers")]
    assert len(matches) == 1, (
        f"Expected exactly one paragraph starting with 'The Other Producers'; "
        f"got {len(matches)}: {[m.text for m in matches]}"
    )
    assert "section8" in matches[0].text, (
        f"Paragraph continuation lost; text was: {matches[0].text!r}"
    )


def test_wrapped_list_item_absorbs_continuation_lines():
    """A multi-line list item should produce ONE list_item block, with the
    continuation lines absorbed.

    Fixture 22 has a dot bullet that wraps to three visual lines starting with
    "Given the continued strong digital lending..." and ending with
    "...lower Books sub-category exposures."
    """
    pages = ingest(FIXTURE_22)
    segs = segment(pages)
    items = [b for b in segs[0].blocks if b.kind_hint == "list_item"]
    matches = [i for i in items if "Given the continued strong" in i.text]
    assert len(matches) == 1
    assert "lower Books sub-category exposures" in matches[0].text, (
        f"List-item continuation lost; text was: {matches[0].text!r}"
    )
