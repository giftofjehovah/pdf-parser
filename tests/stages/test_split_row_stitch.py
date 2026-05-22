"""Tests for cross-page split-row stitching.

The _merge_split_rows_in_table / _is_split_row_pair / _merge_split_cells
functions handle a structural artefact produced by pdfplumber when a single
outer-table row is so tall that it straddles a page break: pdfplumber yields
two separate rows (top half on page P, bottom half on page P+1) instead of
one row.  This is common in Word/InDesign exports; reportlab-generated PDFs do
not produce the pattern, so tests use synthetic DocNode objects rather than
real PDFs.
"""

from __future__ import annotations

import pytest

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.stitch_pages import (
    SPLIT_ROW_EDGE_FRAC,
    _is_split_row_pair,
    _merge_split_cells,
    _merge_split_rows_in_table,
    stitch_tables,
)

PAGE_H = 792.0   # LETTER page height used throughout


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bbox(page: int, y0: float, y1: float,
          x0: float = 50.0, x1: float = 550.0) -> BBox:
    return BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1)


def _cell(page: int, y0: float, y1: float,
          text: str | None = None,
          children: list[DocNode] | None = None) -> DocNode:
    return DocNode(
        kind="cell",
        bbox=_bbox(page, y0, y1),
        text=text,
        children=children or [],
        attrs={},
    )


def _row(page: int, y0: float, y1: float,
         cells: list[DocNode] | None = None,
         n_cols: int = 2) -> DocNode:
    if cells is None:
        cells = [_cell(page, y0, y1, text=f"c{i}") for i in range(n_cols)]
    return DocNode(
        kind="row",
        bbox=_bbox(page, y0, y1),
        children=cells,
        attrs={"page": page, "row_index": 0},
    )


def _table(rows: list[DocNode], page_height: float = PAGE_H,
           page: int = 0) -> DocNode:
    n_cols = len(rows[0].children) if rows else 0
    return DocNode(
        kind="table",
        bbox=_bbox(page, 0, page_height),
        children=rows,
        attrs={
            "n_rows": len(rows),
            "n_cols": n_cols,
            "page_height": page_height,
            "page": page,
            "header_signature": (),
        },
    )


def _subtable(page: int, y0: float, y1: float,
              n_rows: int = 3, x0: float = 55.0, x1: float = 355.0,
              x_sep: float = 205.0) -> DocNode:
    """Minimal sub-table with 2 columns."""
    rows = []
    row_h = (y1 - y0) / max(n_rows, 1)
    for i in range(n_rows):
        ry0 = y0 + i * row_h
        ry1 = ry0 + row_h
        c0 = DocNode(kind="cell", bbox=BBox(page=page, x0=x0,    y0=ry0, x1=x_sep, y1=ry1), text=f"r{i}c0")
        c1 = DocNode(kind="cell", bbox=BBox(page=page, x0=x_sep, y0=ry0, x1=x1,   y1=ry1), text=f"r{i}c1")
        rows.append(DocNode(
            kind="row",
            bbox=BBox(page=page, x0=x0, y0=ry0, x1=x1, y1=ry1),
            children=[c0, c1],
            attrs={"page": page, "row_index": i},
        ))
    return DocNode(
        kind="table",
        bbox=BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1),
        children=rows,
        attrs={
            "n_rows": n_rows, "n_cols": 2,
            "page_height": PAGE_H,
            "page": page,
            "header_signature": ("r0c0", "r0c1"),
        },
    )


# ---------------------------------------------------------------------------
# _is_split_row_pair
# ---------------------------------------------------------------------------

class TestIsSplitRowPair:

    def test_physical_edge_triggers(self):
        """Cells reaching the physical page edge are detected as a split pair."""
        # top row: cells y1 = page_height (physical bottom in pdfplumber coords)
        row_top = _row(0, y0=100.0, y1=PAGE_H)
        # bot row: cells y0 = 0.0 (physical top)
        row_bot = _row(1, y0=0.0,   y1=200.0)
        assert _is_split_row_pair(row_top, row_bot, PAGE_H)

    def test_within_edge_fraction_triggers(self):
        """Cells within SPLIT_ROW_EDGE_FRAC of the edge still qualify."""
        edge = PAGE_H * SPLIT_ROW_EDGE_FRAC   # ~23.8 pt
        row_top = _row(0, y0=100.0, y1=PAGE_H - edge + 1)
        row_bot = _row(1, y0=edge - 1,   y1=300.0)
        assert _is_split_row_pair(row_top, row_bot, PAGE_H)

    def test_normal_row_boundary_rejected(self):
        """Normal multi-page table row boundaries (y1≈712, y0≈78) are NOT split pairs."""
        # Mirrors fix03 geometry: last row on page 0 ends at y1=712,
        # first row on page 1 starts at y0=96.
        row_top = _row(0, y0=694.0, y1=712.0)
        row_bot = _row(1, y0=96.0,  y1=114.0)
        assert not _is_split_row_pair(row_top, row_bot, PAGE_H)

    def test_non_consecutive_pages_rejected(self):
        row_top = _row(0, y0=100.0, y1=PAGE_H)
        row_bot = _row(2, y0=0.0,   y1=200.0)   # page 2, not 1
        assert not _is_split_row_pair(row_top, row_bot, PAGE_H)

    def test_mismatched_column_counts_rejected(self):
        row_top = _row(0, y0=100.0, y1=PAGE_H, n_cols=2)
        row_bot = _row(1, y0=0.0,   y1=200.0,  n_cols=3)
        assert not _is_split_row_pair(row_top, row_bot, PAGE_H)

    def test_top_not_reaching_bottom_rejected(self):
        """Row whose cells end well above the page bottom is not a split top."""
        row_top = _row(0, y0=100.0, y1=600.0)  # ends at 600, well above threshold
        row_bot = _row(1, y0=0.0,   y1=200.0)
        assert not _is_split_row_pair(row_top, row_bot, PAGE_H)

    def test_bot_not_starting_at_top_rejected(self):
        """Row whose cells start well below the page top is not a split bottom."""
        row_top = _row(0, y0=100.0, y1=PAGE_H)
        row_bot = _row(1, y0=200.0, y1=400.0)  # starts at 200, well below threshold
        assert not _is_split_row_pair(row_top, row_bot, PAGE_H)

    def test_no_page_height_in_table_skips_merge(self):
        """Tables without page_height stored skip split-row detection."""
        rows = [
            _row(0, y0=100.0, y1=PAGE_H),
            _row(1, y0=0.0,   y1=200.0),
        ]
        table = _table(rows, page_height=0.0)   # page_height=0 → skip
        result = _merge_split_rows_in_table(table)
        assert result is table                  # returned unchanged


# ---------------------------------------------------------------------------
# _merge_split_cells
# ---------------------------------------------------------------------------

class TestMergeSplitCells:

    def test_leaf_text_combined(self):
        """Two leaf cells (text only) have their text joined."""
        c_top = _cell(0, 0.0, PAGE_H, text="first half")
        c_bot = _cell(1, 0.0, 200.0, text="second half")
        merged = _merge_split_cells(c_top, c_bot)
        assert merged.kind == "cell"
        assert merged.text == "first half second half"
        assert merged.children == []

    def test_leaf_strips_empty_text(self):
        c_top = _cell(0, 0.0, PAGE_H, text="content")
        c_bot = _cell(1, 0.0, 200.0, text="")        # empty continuation
        merged = _merge_split_cells(c_top, c_bot)
        assert merged.text == "content"

    def test_container_cells_children_combined(self):
        """Children from both halves appear in the merged cell."""
        sub_p0 = _subtable(0, y0=100.0, y1=PAGE_H)
        sub_p1 = _subtable(1, y0=0.0,   y1=150.0)

        c_top = _cell(0, 0.0, PAGE_H, children=[sub_p0])
        c_bot = _cell(1, 0.0, 200.0, children=[sub_p1])
        merged = _merge_split_cells(c_top, c_bot)

        assert merged.text is None
        assert any(ch.kind == "table" for ch in merged.children)

    def test_sub_tables_stitched_when_anchors_match(self):
        """Sub-table fragments from consecutive pages are stitched into one table."""
        # sub_p0 ends at physical page bottom → qualifies for stitching
        sub_p0 = _subtable(0, y0=200.0, y1=PAGE_H)
        # sub_p1 starts on next page
        sub_p1 = _subtable(1, y0=0.0,   y1=200.0)

        c_top = _cell(0, 0.0, PAGE_H, children=[sub_p0])
        c_bot = _cell(1, 0.0, 300.0, children=[sub_p1])
        merged = _merge_split_cells(c_top, c_bot)

        tables = [ch for ch in merged.children if ch.kind == "table"]
        assert len(tables) == 1, "sub-table fragments should be stitched into one"
        stitched = tables[0]
        assert stitched.attrs.get("spans_pages") == [0, 1]

    def test_independent_subtables_not_stitched(self):
        """Sub-tables A and B with different anchors stay separate after merge."""
        # sub_a ends in the middle of the page → NOT a spanning fragment
        sub_a = _subtable(0, y0=100.0, y1=500.0, x0=55.0, x1=255.0, x_sep=155.0)
        # sub_b is on the same page but different column positions
        sub_b = _subtable(0, y0=520.0, y1=700.0, x0=255.0, x1=455.0, x_sep=355.0)

        c_top = _cell(0, 0.0, PAGE_H, children=[sub_a])
        c_bot = _cell(1, 0.0, 300.0, children=[sub_b])
        merged = _merge_split_cells(c_top, c_bot)

        tables = [ch for ch in merged.children if ch.kind == "table"]
        assert len(tables) == 2, "non-matching sub-tables must remain separate"

    def test_paragraph_preserved_across_split(self):
        """Paragraph nodes from each half survive and are sorted in document order."""
        para_p0 = DocNode(kind="paragraph", bbox=_bbox(0, 600.0, 620.0), text="start")
        para_p1 = DocNode(kind="paragraph", bbox=_bbox(1,  10.0,  30.0), text="end")

        c_top = _cell(0, 0.0, PAGE_H, children=[para_p0])
        c_bot = _cell(1, 0.0, 200.0, children=[para_p1])
        merged = _merge_split_cells(c_top, c_bot)

        texts = [ch.text for ch in merged.children]
        assert "start" in texts and "end" in texts
        # "start" is on page 0, "end" on page 1 → start must come first
        assert texts.index("start") < texts.index("end")

    def test_provenance_marked(self):
        c_top = _cell(0, 0.0, PAGE_H, text="a")
        c_bot = _cell(1, 0.0, 100.0, text="b")
        merged = _merge_split_cells(c_top, c_bot)
        assert merged.provenance.get("split_cell_merged") is True


# ---------------------------------------------------------------------------
# _merge_split_rows_in_table — integration
# ---------------------------------------------------------------------------

class TestMergeSplitRowsInTable:

    def _make_split_table(self, n_data_rows_p0: int = 5, n_data_rows_p1: int = 3,
                          include_header: bool = True) -> DocNode:
        """Build a synthetic merged table with a split row pair."""
        rows: list[DocNode] = []
        if include_header:
            rows.append(_row(0, y0=50.0, y1=80.0))

        # Last row on page 0: cells extend to physical bottom
        split_top_cells = [
            _cell(0, y0=80.0, y1=PAGE_H, text="cell-top"),
        ]
        rows.append(DocNode(
            kind="row",
            bbox=_bbox(0, 80.0, PAGE_H),
            children=split_top_cells,
            attrs={"page": 0, "row_index": len(rows)},
        ))

        # First row on page 1: cells start at physical top
        split_bot_cells = [
            _cell(1, y0=0.0, y1=200.0, text="cell-bot"),
        ]
        rows.append(DocNode(
            kind="row",
            bbox=_bbox(1, 0.0, 200.0),
            children=split_bot_cells,
            attrs={"page": 1, "row_index": len(rows)},
        ))

        # Remaining page-1 rows (ordinary)
        for i in range(n_data_rows_p1):
            rows.append(_row(1, y0=200.0 + i * 20, y1=220.0 + i * 20, n_cols=1))

        t = _table(rows, page_height=PAGE_H)
        return t

    def test_split_row_pair_collapsed(self):
        """The split-row pair (top + bottom) is collapsed into one row."""
        table = self._make_split_table()
        result = _merge_split_rows_in_table(table)
        # Before: header + split_top + split_bot + 3 page-1 rows = 6
        # After:  header + merged_row + 3 page-1 rows = 5
        assert result.attrs["n_rows"] == len(table.children) - 1

    def test_merged_row_attr_set(self):
        table = self._make_split_table()
        result = _merge_split_rows_in_table(table)
        merged_row = result.children[1]  # index 1 = after header
        assert merged_row.attrs.get("split_row_merged") is True

    def test_merged_cell_contains_both_texts(self):
        table = self._make_split_table()
        result = _merge_split_rows_in_table(table)
        merged_cell = result.children[1].children[0]
        assert merged_cell.text == "cell-top cell-bot"

    def test_row_indices_resequenced(self):
        table = self._make_split_table()
        result = _merge_split_rows_in_table(table)
        indices = [r.attrs["row_index"] for r in result.children]
        assert indices == list(range(len(result.children)))

    def test_no_split_rows_unchanged(self):
        """Tables with no split-row pairs are returned unchanged."""
        rows = [_row(0, y0=50 + i * 20, y1=70 + i * 20) for i in range(5)]
        table = _table(rows)
        result = _merge_split_rows_in_table(table)
        assert result is table  # same object; no rebuild

    def test_ordinary_rows_preserved_after_merge(self):
        table = self._make_split_table(n_data_rows_p1=3)
        result = _merge_split_rows_in_table(table)
        # merged_row inherits page=0 from row_top; the 3 ordinary rows are page=1
        pages = [r.attrs.get("page") for r in result.children]
        assert pages.count(1) == 3          # exactly 3 ordinary page-1 rows survive
        assert result.attrs["n_rows"] == 5  # header + merged + 3 ordinary


# ---------------------------------------------------------------------------
# Regression: existing page-spanning fixtures must NOT be affected
# ---------------------------------------------------------------------------

class TestNoRegressionOnNormalSpanning:
    """Verify that fixture 03 (normal multi-page table) is not mis-detected
    as having a split row and is not incorrectly modified."""

    @pytest.fixture(scope="class")
    def fix03_post(self):
        from pathlib import Path
        from pdf_parser.stages.extract_tables import extract_tables
        pdf = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
        pre = extract_tables(pdf)
        return stitch_tables(pre)

    def test_fix03_still_one_table(self, fix03_post):
        assert len(fix03_post) == 1

    def test_fix03_row_count_unchanged(self, fix03_post):
        """The split-row pass must not alter fix03's row count.

        We compare against the actual stitched count from extract_tables (which
        does NOT go through _merge_split_rows_in_table) to confirm the pass is
        a no-op on this fixture.  The actual count is 51 (1 header + 50 data
        rows across 2 pages with repeated header deduped).
        """
        from pathlib import Path
        from pdf_parser.stages.extract_tables import extract_tables
        from pdf_parser.stages.stitch_pages import _merge_split_rows_in_table
        pdf = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
        pre_stitch = extract_tables(pdf)
        # Manually stitch without the split-row pass to get the baseline count.
        from pdf_parser.stages.stitch_pages import _merge_two, _can_merge
        baseline = [pre_stitch[0]]
        for t in pre_stitch[1:]:
            if _can_merge(baseline[-1], t):
                baseline[-1] = _merge_two(baseline[-1], t)
            else:
                baseline.append(t)
        baseline_count = baseline[0].attrs["n_rows"]
        # post-stitch (with split-row pass) must equal the baseline.
        merged_table = fix03_post[0]
        assert merged_table.attrs["n_rows"] == baseline_count

    def test_fix03_no_split_row_merged_flags(self, fix03_post):
        """No rows in fixture 03's stitched table should have split_row_merged=True."""
        merged_table = fix03_post[0]
        assert all(
            not r.attrs.get("split_row_merged")
            for r in merged_table.children
        )
