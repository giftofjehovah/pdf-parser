from pathlib import Path

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.extract_tables import extract_tables
from pdf_parser.stages.stitch_pages import stitch_tables

SPAN = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "03_page_spanning" / "source.pdf"
SPAN_NO_REPEAT = (
    Path(__file__).resolve().parents[1]
    / "golden"
    / "synthetic"
    / "06_page_spanning_no_header_repeat"
    / "source.pdf"
)
SPAN_NESTED = (
    Path(__file__).resolve().parents[1]
    / "golden"
    / "synthetic"
    / "07_page_spanning_with_nested"
    / "source.pdf"
)
SPAN_SUB_SPLIT = (
    Path(__file__).resolve().parents[1]
    / "golden"
    / "synthetic"
    / "08_page_spanning_subtable_split"
    / "source.pdf"
)


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


# --- continuation page without a repeated header ---------------------------
#
# 03_page_spanning sets repeatRows=1 so every page starts with the header row.
# 06_page_spanning_no_header_repeat omits repeatRows: page 2 begins directly
# with a data row, so the stitcher must merge by column-anchor alone and keep
# every continuation row (no dedup), while still mapping page-2 cells onto the
# page-1 column structure.


def test_no_repeat_header_still_merges_to_one():
    pre = extract_tables(SPAN_NO_REPEAT)
    assert len(pre) >= 2, f"expected ≥2 per-page tables, got {len(pre)}"
    # The continuation table must NOT carry the real header signature, otherwise
    # the fixture has accidentally repeated it and we are not exercising the
    # no-repeat code path.
    assert pre[1].attrs["header_signature"] != pre[0].attrs["header_signature"]
    merged = stitch_tables(pre)
    assert len(merged) == 1, f"expected 1 merged table, got {len(merged)}"


def test_no_repeat_header_preserves_every_row():
    pre = extract_tables(SPAN_NO_REPEAT)
    merged = stitch_tables(pre)
    pre_rows = sum(len(t.children) for t in pre)
    # No header was repeated, so nothing should be dropped during stitching.
    assert len(merged[0].children) == pre_rows
    # And the page-1 header survives as the merged table's signature.
    assert merged[0].attrs["header_signature"] == ("ID", "Description", "Value")


def test_no_repeat_header_column_mapping_across_page_break():
    """Page-2 cells must land in the same columns as page-1 cells.

    The fixture is ID/Description/Value with IDs 1..50 in order. After
    stitching, the data rows (everything after the page-1 header) must
    form a contiguous 1..50 sequence in column 0, with column 1 always
    starting with "Item number" and column 2 always starting with "$".
    A mis-mapped continuation page would shuffle these.
    """
    merged = stitch_tables(extract_tables(SPAN_NO_REPEAT))[0]
    data_rows = merged.children[1:]  # skip header
    assert len(data_rows) == 50
    ids = [r.children[0].text for r in data_rows]
    assert ids == [str(i) for i in range(1, 51)], "column 0 mis-mapped across page break"
    assert all(r.children[1].text.startswith("Item number ") for r in data_rows), \
        "column 1 mis-mapped across page break"
    assert all(r.children[2].text.startswith("$") for r in data_rows), \
        "column 2 mis-mapped across page break"
    # Sanity: the page-2 data rows must actually report page=1 in their attrs,
    # otherwise we'd be silently asserting on a single-page table.
    pages_seen = {r.attrs.get("page") for r in data_rows}
    assert pages_seen == {0, 1}, f"expected rows from pages {{0, 1}}, got {pages_seen}"


# --- continuation page without a header AND containing nested sub-tables ----
#
# 07_page_spanning_with_nested: 50-row outer table (Step/Inputs/Notes) split
# across two pages with no repeated header, plus a 2x2 nested sub-table in the
# "Inputs" cell of row 5 (page 1) and row 45 (page 2). This exercises the
# combination: cross-page merge + no dedup + nested-table preservation, and
# pins that the page-2 nested table is detected as a sub-table of the merged
# outer row (not surfaced as a top-level peer table).


def _nested_tables(table: DocNode) -> list[tuple[int, int, DocNode]]:
    """Return (row_index, col_index, nested_table_node) for every nested table."""
    found: list[tuple[int, int, DocNode]] = []
    for r_idx, row in enumerate(table.children):
        for c_idx, cell in enumerate(row.children):
            for child in cell.children:
                if child.kind == "table":
                    found.append((r_idx, c_idx, child))
    return found


def test_nested_page_spanning_pre_stitch_shape():
    """Pre-stitch: exactly 2 per-page outer tables, one nested sub-table each.

    Guards the fixture itself — if reportlab ever re-paginates and the row-45
    nested table drifts onto page 1, the later cross-page assertions would
    silently degrade into a single-page test.
    """
    pre = extract_tables(SPAN_NESTED)
    assert len(pre) == 2, f"expected 2 per-page outer tables, got {len(pre)}"
    assert pre[0].attrs["header_signature"] == ("Step", "Inputs", "Notes")
    # Page 2 must NOT carry the real header (no repeatRows in the fixture).
    assert pre[1].attrs["header_signature"] != pre[0].attrs["header_signature"]
    # One nested table per page, both in the "Inputs" column (col index 1).
    n0 = _nested_tables(pre[0])
    n1 = _nested_tables(pre[1])
    assert len(n0) == 1 and n0[0][1] == 1, f"page-1 nested shape wrong: {n0}"
    assert len(n1) == 1 and n1[0][1] == 1, f"page-2 nested shape wrong: {n1}"
    assert n0[0][2].attrs["header_signature"] == ("p1-A", "p1-B")
    assert n1[0][2].attrs["header_signature"] == ("p2-A", "p2-B")


def test_nested_page_spanning_merges_and_preserves_all_rows():
    pre = extract_tables(SPAN_NESTED)
    pre_rows = sum(len(t.children) for t in pre)
    merged = stitch_tables(pre)
    assert len(merged) == 1, f"expected 1 merged table, got {len(merged)}"
    assert len(merged[0].children) == pre_rows  # no dedup, header not repeated
    assert merged[0].attrs["header_signature"] == ("Step", "Inputs", "Notes")
    assert merged[0].attrs["spans_pages"] == [0, 1]


def test_nested_subtables_survive_stitching_on_both_pages():
    """Both sub-tables must remain attached to their owning cells after stitching.

    A regression where the stitcher rebuilt rows without preserving cell
    `children` would surface here: the p2 nested table would vanish or be
    re-parented incorrectly.
    """
    merged = stitch_tables(extract_tables(SPAN_NESTED))[0]
    nested = _nested_tables(merged)
    assert len(nested) == 2, f"expected 2 nested sub-tables after stitch, got {len(nested)}"

    # Index nested by their header signature so the test does not care about order.
    by_sig = {n[2].attrs["header_signature"]: n for n in nested}
    assert set(by_sig) == {("p1-A", "p1-B"), ("p2-A", "p2-B")}

    p1_row, p1_col, p1_tbl = by_sig[("p1-A", "p1-B")]
    p2_row, p2_col, p2_tbl = by_sig[("p2-A", "p2-B")]

    # Both nested tables sit in the "Inputs" column (col 1) of their row.
    assert p1_col == 1 and p2_col == 1
    # Owning cells must be non-leaf: text is None, child is the nested table.
    p1_cell = merged.children[p1_row].children[p1_col]
    p2_cell = merged.children[p2_row].children[p2_col]
    assert p1_cell.text is None and p1_cell.children == [p1_tbl]
    assert p2_cell.text is None and p2_cell.children == [p2_tbl]
    # And the outer-row "Step" cell still identifies the original row numbers.
    assert merged.children[p1_row].children[0].text == "5"
    assert merged.children[p2_row].children[0].text == "45"


def test_nested_subtable_on_continuation_page_reports_correct_page():
    """The row carrying the page-2 nested table must report page=1; the nested
    table's own bbox must also live on page 1. Catches a bug where stitching
    would inherit page metadata from the merged parent."""
    merged = stitch_tables(extract_tables(SPAN_NESTED))[0]
    nested = {n[2].attrs["header_signature"]: n for n in _nested_tables(merged)}
    p2_row_idx, _, p2_tbl = nested[("p2-A", "p2-B")]
    assert merged.children[p2_row_idx].attrs["page"] == 1
    p2_bbox = p2_tbl.bbox if isinstance(p2_tbl.bbox, BBox) else p2_tbl.bbox[0]
    assert p2_bbox.page == 1, f"expected page-2 nested bbox.page=1, got {p2_bbox.page}"
    # And the page-1 nested is still anchored to page 0.
    p1_row_idx, _, p1_tbl = nested[("p1-A", "p1-B")]
    assert merged.children[p1_row_idx].attrs["page"] == 0
    p1_bbox = p1_tbl.bbox if isinstance(p1_tbl.bbox, BBox) else p1_tbl.bbox[0]
    assert p1_bbox.page == 0


def test_nested_page_spanning_outer_column_mapping_unaffected_by_nesting():
    """Even with nested tables interleaved, the outer Step column must still
    enumerate 1..50 contiguously after stitching."""
    merged = stitch_tables(extract_tables(SPAN_NESTED))[0]
    data_rows = merged.children[1:]
    assert len(data_rows) == 50
    steps = [r.children[0].text for r in data_rows]
    assert steps == [str(i) for i in range(1, 51)], "outer Step column mis-mapped"


# --- sub-table itself spanning across pages --------------------------------
#
# 08_page_spanning_subtable_split: outer table with no header repeat. A nested
# sub-table is laid out as two halves placed in adjacent outer rows tuned so
# that the first half (header + 3 data) is the last data row on page 1 and the
# second half (3 data, no header) is the first data row on page 2. Both halves
# share identical inner column widths, so their column anchors match.
#
# This is the test the regression fix exists for: without line-based outer
# column detection, the page-2 outer column anchors would be derived from the
# first raw row, which on page 2 contains the inner sub-table — pdfplumber
# would then split the outer row into 6 spurious columns and the page-2 sub-
# table would be lost entirely.


def test_subtable_spans_outer_columns_correctly_on_continuation_page():
    """Page-2 outer must report exactly 3 columns even though its first row
    embeds a 2-column sub-table. A regression here would mean the outer column
    anchors got contaminated by inner-table vertical edges."""
    pre = extract_tables(SPAN_SUB_SPLIT)
    assert len(pre) == 2, f"expected 2 per-page outer tables, got {len(pre)}"
    assert pre[0].attrs["n_cols"] == 3
    assert pre[1].attrs["n_cols"] == 3, (
        f"page-2 outer mis-columned: got {pre[1].attrs['n_cols']} cols "
        "(inner sub-table likely polluted outer column detection)"
    )
    # Outer column anchors must be identical across pages.
    p0_anchors = [(c.bbox.x0, c.bbox.x1) for c in pre[0].children[0].children]
    p1_anchors = [(c.bbox.x0, c.bbox.x1) for c in pre[1].children[0].children]
    assert p0_anchors == p1_anchors, \
        f"outer column anchors drifted across page break: {p0_anchors} vs {p1_anchors}"


def test_spanning_subtable_halves_are_detected_on_both_pages():
    """Both halves of the spanning sub-table must surface as nested DocNodes."""
    pre = extract_tables(SPAN_SUB_SPLIT)
    n0 = _nested_tables(pre[0])
    n1 = _nested_tables(pre[1])
    assert len(n0) == 1, f"page-1 should have exactly 1 nested sub-table, got {len(n0)}"
    assert len(n1) == 1, f"page-2 should have exactly 1 nested sub-table, got {len(n1)}"
    # Page-1 half carries the real sub-header; page-2 half is a header-less continuation.
    assert n0[0][2].attrs["header_signature"] == ("sub-H1", "sub-H2")
    assert n1[0][2].attrs["header_signature"] != ("sub-H1", "sub-H2"), \
        "page-2 half must NOT have a repeated sub-header (otherwise the test is moot)"
    # Both halves sit in the outer 'Detail' column (col index 1).
    assert n0[0][1] == 1 and n1[0][1] == 1


def test_spanning_subtable_halves_share_column_anchors():
    """The two halves must agree on inner column anchors; this is the signal a
    downstream pass would use to recognise them as one logically-continuing
    sub-table."""
    pre = extract_tables(SPAN_SUB_SPLIT)
    sub0 = _nested_tables(pre[0])[0][2]
    sub1 = _nested_tables(pre[1])[0][2]
    anchors0 = [(c.bbox.x0, c.bbox.x1) for c in sub0.children[0].children]
    anchors1 = [(c.bbox.x0, c.bbox.x1) for c in sub1.children[0].children]
    assert anchors0 == anchors1, \
        f"spanning sub-table column anchors drifted: {anchors0} vs {anchors1}"


def test_spanning_subtable_halves_land_in_adjacent_merged_rows():
    """After stitching the outer, the two halves must sit in adjacent rows,
    on adjacent pages — i.e. they straddle exactly one page break with no
    intervening outer content. A future stitch pass for nested tables would
    use this adjacency to merge them."""
    merged = stitch_tables(extract_tables(SPAN_SUB_SPLIT))[0]
    subs = _nested_tables(merged)
    assert len(subs) == 2
    r0, c0, _ = subs[0]
    r1, c1, _ = subs[1]
    assert c0 == c1 == 1, "both halves must remain in the outer Detail column"
    assert r1 == r0 + 1, f"halves must be in adjacent merged rows, got {r0} and {r1}"
    assert merged.children[r0].attrs["page"] == 0
    assert merged.children[r1].attrs["page"] == 1
    # And the surrounding outer cells still carry their own data.
    assert merged.children[r0].children[0].text == "28"  # Step
    assert merged.children[r0].children[2].text == "ends pg1"  # Notes
    assert merged.children[r1].children[0].text == "29"
    assert merged.children[r1].children[2].text == "starts pg2"


def test_spanning_subtable_data_is_partitioned_correctly_across_pages():
    """The logical sub-table data is ('a','b','c','d','e','f') in column 0.
    Page 1 must own a/b/c (after the header) and page 2 must own d/e/f.
    A mis-mapping that lost the page-2 half (the original bug) or shuffled
    cells across the page boundary would fail here."""
    merged = stitch_tables(extract_tables(SPAN_SUB_SPLIT))[0]
    subs = _nested_tables(merged)
    (_, _, sub_p1), (_, _, sub_p2) = subs
    # sub_p1: header + 3 data rows
    p1_col0 = [r.children[0].text for r in sub_p1.children]
    p1_col1 = [r.children[1].text for r in sub_p1.children]
    assert p1_col0 == ["sub-H1", "a", "b", "c"]
    assert p1_col1 == ["sub-H2", "1", "2", "3"]
    # sub_p2: 3 data rows, no header
    p2_col0 = [r.children[0].text for r in sub_p2.children]
    p2_col1 = [r.children[1].text for r in sub_p2.children]
    assert p2_col0 == ["d", "e", "f"]
    assert p2_col1 == ["4", "5", "6"]
    # Combined, the spanning sub-table covers a..f / 1..6 in order.
    assert p1_col0[1:] + p2_col0 == ["a", "b", "c", "d", "e", "f"]
    assert p1_col1[1:] + p2_col1 == ["1", "2", "3", "4", "5", "6"]
