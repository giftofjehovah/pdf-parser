from pathlib import Path

from pdf_parser.stages.extract_tables import extract_tables
from pdf_parser.stages.stitch_pages import stitch_tables

SPAN = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "03_page_spanning" / "source.pdf"


def test_per_page_tables_get_merged_to_one():
    pre = extract_tables(SPAN)
    assert len(pre) >= 2, f"expected ≥2 per-page tables before stitching, got {len(pre)}"
    merged = stitch_tables(pre)
    assert len(merged) == 1, f"expected 1 merged table, got {len(merged)}"


def test_merged_table_row_count_is_sum_minus_duplicate_headers():
    pre = extract_tables(SPAN)
    merged = stitch_tables(pre)
    pre_rows = sum(len(t.children) for t in pre)
    merged_rows = len(merged[0].children)
    # Header repeats on each page after the first should drop.
    assert merged_rows == pre_rows - (len(pre) - 1)


def test_merged_bbox_is_list_per_page():
    pre = extract_tables(SPAN)
    merged = stitch_tables(pre)
    assert isinstance(merged[0].bbox, list)
    assert len(merged[0].bbox) == len(pre)


def test_rows_retain_source_page():
    pre = extract_tables(SPAN)
    merged = stitch_tables(pre)
    pages = {row.attrs.get("page") for row in merged[0].children}
    assert pages == {t.attrs["page"] for t in pre}
