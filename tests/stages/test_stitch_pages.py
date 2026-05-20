from pathlib import Path

from pdf_parser.model import BBox, DocNode
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


def _mk_table(page: int, n_data_rows: int, *, x0: float = 50, x1: float = 550) -> DocNode:
    """Build a synthetic single-page table with a header row plus n_data_rows."""
    header_cells = [
        DocNode(kind="cell", bbox=BBox(page=page, x0=x0, y0=0, x1=(x0 + x1) / 2, y1=10), text="H1"),
        DocNode(kind="cell", bbox=BBox(page=page, x0=(x0 + x1) / 2, y0=0, x1=x1, y1=10), text="H2"),
    ]
    rows = [DocNode(kind="row", bbox=BBox(page=page, x0=x0, y0=0, x1=x1, y1=10),
                    children=header_cells, attrs={"page": page, "row_index": 0})]
    for i in range(n_data_rows):
        y = 10 + i * 10
        data_cells = [
            DocNode(kind="cell", bbox=BBox(page=page, x0=x0, y0=y, x1=(x0 + x1) / 2, y1=y + 10), text=f"a{i}"),
            DocNode(kind="cell", bbox=BBox(page=page, x0=(x0 + x1) / 2, y0=y, x1=x1, y1=y + 10), text=f"b{i}"),
        ]
        rows.append(DocNode(kind="row", bbox=BBox(page=page, x0=x0, y0=y, x1=x1, y1=y + 10),
                            children=data_cells, attrs={"page": page, "row_index": i + 1}))
    return DocNode(
        kind="table",
        bbox=BBox(page=page, x0=x0, y0=0, x1=x1, y1=10 + n_data_rows * 10),
        children=rows,
        attrs={"n_rows": 1 + n_data_rows, "n_cols": 2, "header_signature": ("H1", "H2"), "page": page},
    )


def test_three_page_table_merges_to_one():
    pre = [_mk_table(0, 3), _mk_table(1, 3), _mk_table(2, 3)]
    merged = stitch_tables(pre)
    assert len(merged) == 1, f"expected 1 merged table across 3 pages, got {len(merged)}"
    assert isinstance(merged[0].bbox, list)
    assert len(merged[0].bbox) == 3
    # 3 pages × (1 header + 3 data) = 12 rows, minus 2 duplicate headers = 10
    assert len(merged[0].children) == 10
    assert merged[0].attrs["spans_pages"] == [0, 1, 2]


def test_non_adjacent_pages_do_not_merge():
    pre = [_mk_table(0, 2), _mk_table(2, 2)]  # skips page 1
    merged = stitch_tables(pre)
    assert len(merged) == 2
