from pathlib import Path

from pdf_parser.chunk import chunk_tree
from pdf_parser.pipeline import parse

SPAN = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_chunks_have_breadcrumb_and_page_range():
    chunks = chunk_tree(parse(SIMPLE), max_tokens=400)
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert isinstance(c.breadcrumb, list)
        assert c.page_range[0] <= c.page_range[1]
        assert c.source_ids


def test_paragraph_chunks_get_overlap():
    # Make a small max_tokens so a paragraph splits
    chunks = chunk_tree(parse(SIMPLE), max_tokens=8, overlap=2)
    para_chunks = [c for c in chunks if c.kind_summary == "paragraph"]
    if len(para_chunks) >= 2:
        # Tail of one is prefix of next (token overlap)
        a, b = para_chunks[0], para_chunks[1]
        a_tail = a.text.split()[-2:]
        b_head = b.text.split()[:2]
        assert a_tail == b_head


def test_table_chunk_summary_has_shape():
    chunks = chunk_tree(parse(SIMPLE), max_tokens=400)
    tbl = [c for c in chunks if c.kind_summary.startswith("table:")]
    assert tbl, "expected a table chunk"


def test_big_table_splits_with_repeated_header():
    chunks = chunk_tree(parse(SPAN), max_tokens=200)
    tbl = [c for c in chunks if c.kind_summary.startswith("table:")]
    assert len(tbl) >= 2, f"expected ≥2 table chunks for big spanned table, got {len(tbl)}"
    # Every table chunk should contain the header row's first cell label.
    for c in tbl:
        assert "ID" in c.text  # header was "ID | Description | Value"


def test_no_chunk_splits_a_row():
    chunks = chunk_tree(parse(SPAN), max_tokens=200)
    for c in chunks:
        # row delimiters in text are newlines; each row begins with "ID" header OR a digit
        for line in c.text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Either header line or a complete row (3 pipe-separated fields)
            if stripped.startswith("ID"):
                continue
            assert stripped.count("|") >= 2 or stripped == c.text.strip()
