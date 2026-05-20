"""Stage 4: build DocNode subtree per TableRegion; recurse into cells for nested tables."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pdfplumber

from pdf_parser.model import BBox, MAX_DEPTH, DocNode
from pdf_parser.stages.detect_tables import TableRegion, detect_tables

_OVERLAP_TOL = 2.0  # points; guards against sub-pixel boundary mismatches


def _page_y(page_height: float, pdf_y: float) -> float:
    """Convert PDF y (bottom-origin) to pdfplumber page y (top-origin)."""
    return page_height - pdf_y


def _outer_line_ys(page, table_bbox: tuple[float, float, float, float]) -> list[float]:
    """Return sorted page-space y-values of horizontal lines that mark row boundaries.

    Accepts any line that lies within the table's x range and spans at least
    50 % of the table width.  The relaxed threshold (vs. the original 100 %)
    handles row-spanning cells: a row-dividing line is suppressed only for the
    spanned column but still drawn across the remaining columns, producing a
    partial-width segment (≥ 50 % when at most half the columns are merged).
    """
    table_x0, _, table_x1, _ = table_bbox
    table_width = table_x1 - table_x0
    page_height = page.height
    ys: set[float] = set()
    for line in page.lines:
        if abs(line["y0"] - line["y1"]) < 1:  # horizontal
            line_width = line["x1"] - line["x0"]
            in_x = (line["x0"] >= table_x0 - _OVERLAP_TOL
                    and line["x1"] <= table_x1 + _OVERLAP_TOL)
            if in_x and line_width >= 0.50 * table_width:
                ys.add(round(_page_y(page_height, line["y0"]), 1))
    return sorted(ys)


def _outer_col_xs(raw_header_row: list) -> list[tuple[float, float]]:
    """Return (x0, x1) for each logical outer column, derived from non-None header cells.

    Used as a fallback when the table lacks full-height vertical borders.
    Vulnerable to nested-table interference if the header row itself is split
    by inner-table vertical edges; prefer :func:`_outer_col_xs_from_lines`.
    """
    return [(cell[0], cell[2]) for cell in raw_header_row if cell is not None]


def _outer_col_xs_from_lines(
    page, table_bbox: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """Return (x0, x1) for each outer column, derived from tall vertical lines.

    Nested sub-tables draw vertical edges confined to a single cell, so they
    span only a fraction of the table height.  The ``≥ 70 %`` threshold keeps
    those out while still capturing outer column boundaries that are suppressed
    in a colspan header row (typically 1 out of N rows, leaving (N-1)/N ≥ 80 %
    for N ≥ 5; the threshold was originally 95 % which broke on any colspan).
    """
    _, table_y0, _, table_y1 = table_bbox
    table_height = table_y1 - table_y0
    if table_height <= 0:
        return []
    xs: set[float] = set()
    for ln in page.lines:
        if abs(ln["x0"] - ln["x1"]) < 1:  # vertical
            length = abs(ln["y1"] - ln["y0"])
            if length >= 0.70 * table_height:
                xs.add(round(ln["x0"], 1))
    xs_sorted = sorted(xs)
    if len(xs_sorted) < 2:
        return []
    return list(zip(xs_sorted[:-1], xs_sorted[1:]))


def _logical_grid_from_table(
    page, t, page_index: int
) -> Optional[tuple[list[list[str]], list[list[BBox]]]]:
    """
    Reconstruct the logical (merged-cell) grid for a pdfplumber table.

    Returns (logical_grid, logical_cell_bboxes), or None if no outer-line
    structure is found (caller should fall back to the raw TableRegion).
    """
    raw_rows = [r.cells for r in t.rows]
    texts = t.extract()
    if not texts:
        return None

    outer_ys = _outer_line_ys(page, t.bbox)
    if len(outer_ys) < 2:
        return None

    # Prefer full-height vertical lines: robust when the first/last row contains
    # a nested sub-table that would otherwise pollute the header-cell positions.
    col_xs = _outer_col_xs_from_lines(page, t.bbox)
    if not col_xs:
        col_xs = _outer_col_xs(raw_rows[0])
    if not col_xs:
        return None

    row_boundaries = list(zip(outer_ys[:-1], outer_ys[1:]))
    logical_grid: list[list[str]] = []
    logical_cell_bboxes: list[list[BBox]] = []

    for row_y0, row_y1 in row_boundaries:
        row_texts: list[str] = []
        row_bboxes: list[BBox] = []
        for col_x0, col_x1 in col_xs:
            logical_bbox = BBox(
                page=page_index, x0=col_x0, y0=row_y0, x1=col_x1, y1=row_y1
            )
            # Collect text from all raw sub-cells that START in this logical cell.
            # Using the top-left corner (cx0, cy0) rather than the centre avoids
            # merged cells being assigned to multiple logical cells: a colspan/rowspan
            # cell's centre lands exactly on a boundary, but its top-left corner is
            # unambiguously inside the first logical cell of the span.
            #
            # Symmetric tolerance: [boundary - tol, boundary + tol] for the lower
            # edge (handles sub-pixel rounding where cy0 is slightly below row_y0)
            # and [boundary - tol, next_boundary - tol] for the upper edge (prevents
            # a cell whose start drifted to within tol of the next row from matching
            # two rows simultaneously).  In practice pdfplumber coordinates are exact
            # to within 1-2 pt, so tol=2.0 is sufficient.
            cell_texts: list[str] = []
            for ri, rrow in enumerate(raw_rows):
                for ci, cell in enumerate(rrow):
                    if cell is None:
                        continue
                    cx0, cy0, cx1, cy1 = cell
                    in_x = col_x0 - _OVERLAP_TOL <= cx0 <= col_x1 - _OVERLAP_TOL
                    in_y = row_y0 - _OVERLAP_TOL <= cy0 <= row_y1 - _OVERLAP_TOL
                    if in_x and in_y:
                        t_val = texts[ri][ci]
                        if t_val:
                            cell_texts.append(t_val)
            row_texts.append(" ".join(cell_texts))
            row_bboxes.append(logical_bbox)
        logical_grid.append(row_texts)
        logical_cell_bboxes.append(row_bboxes)

    return logical_grid, logical_cell_bboxes


def _build_cell(text: str, bbox: BBox, pdf_path: Path, depth: int) -> DocNode:
    children: list[DocNode] = []
    if depth + 1 < MAX_DEPTH:
        shrunk = BBox(
            page=bbox.page,
            x0=bbox.x0 + 1,
            y0=bbox.y0 + 1,
            x1=bbox.x1 - 1,
            y1=bbox.y1 - 1,
        )
        # Guard against degenerate (zero-area) cells after shrinking.
        if shrunk.x1 > shrunk.x0 and shrunk.y1 > shrunk.y0:
            nested = detect_tables(pdf_path, region_bbox=shrunk)
            for region in nested:
                # Skip if the detected region is as wide as the cell itself (echoed parent).
                if abs(region.bbox.x1 - region.bbox.x0) >= abs(bbox.x1 - bbox.x0) - 1:
                    continue
                children.append(_build_table(region, pdf_path, depth + 1))
    return DocNode(
        kind="cell",
        bbox=bbox,
        text=text if not children else None,
        children=children,
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def _build_table(region: TableRegion, pdf_path: Path, depth: int) -> DocNode:
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(region.grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (
                region.cell_bboxes[r_idx][c_idx]
                if r_idx < len(region.cell_bboxes)
                and c_idx < len(region.cell_bboxes[r_idx])
                else region.bbox
            )
            cells.append(_build_cell(text, cbox, pdf_path, depth))
        rows.append(
            DocNode(
                kind="row",
                bbox=region.bbox,
                children=cells,
                attrs={"page": region.page_index, "row_index": r_idx},
            )
        )
    return DocNode(
        kind="table",
        bbox=region.bbox,
        children=rows,
        attrs={
            "n_rows": len(region.grid),
            "n_cols": len(region.grid[0]) if region.grid else 0,
            "header_signature": tuple(region.grid[0]) if region.grid else (),
            "page": region.page_index,
        },
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def _build_table_from_logical(
    logical_grid: list[list[str]],
    cell_bboxes: list[list[BBox]],
    region: TableRegion,
    pdf_path: Path,
    depth: int,
) -> DocNode:
    """Build a DocNode table from a reconstructed logical (merged-cell) grid."""
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(logical_grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (
                cell_bboxes[r_idx][c_idx]
                if r_idx < len(cell_bboxes) and c_idx < len(cell_bboxes[r_idx])
                else region.bbox
            )
            cells.append(_build_cell(text, cbox, pdf_path, depth))
        rows.append(
            DocNode(
                kind="row",
                bbox=region.bbox,
                children=cells,
                attrs={"page": region.page_index, "row_index": r_idx},
            )
        )
    return DocNode(
        kind="table",
        bbox=region.bbox,
        children=rows,
        attrs={
            "n_rows": len(logical_grid),
            "n_cols": len(logical_grid[0]) if logical_grid else 0,
            "header_signature": tuple(logical_grid[0]) if logical_grid else (),
            "page": region.page_index,
        },
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def extract_tables(pdf_path: Path) -> list[DocNode]:
    """
    Build a DocNode subtree for each top-level table in *pdf_path*.

    For each top-level table we first attempt to reconstruct the logical
    (merged-cell) outer grid using full-width horizontal lines.  This lets us
    correctly identify cells that contain nested tables even when pdfplumber's
    default detection "flattens" inner grids into the parent.  Cells whose bbox
    contains an inner table are given ``text=None`` and a table child; leaf
    cells carry ``text`` and ``children=[]``.
    """
    result: list[DocNode] = []
    regions = detect_tables(pdf_path)

    with pdfplumber.open(str(pdf_path)) as pdf:
        for region in regions:
            page = pdf.pages[region.page_index]
            raw_rows = [r.cells for r in page.find_tables()[0].rows]
            # find_tables() may return multiple tables on the page; match by bbox
            page_tables = page.find_tables()
            matched_pt = None
            for pt in page_tables:
                if abs(pt.bbox[0] - region.bbox.x0) < 2 and abs(pt.bbox[1] - region.bbox.y0) < 2:
                    matched_pt = pt
                    break

            logical = None
            if matched_pt is not None:
                logical = _logical_grid_from_table(page, matched_pt, region.page_index)

            if logical is not None:
                grid, bboxes = logical
                result.append(
                    _build_table_from_logical(grid, bboxes, region, pdf_path, depth=0)
                )
            else:
                result.append(_build_table(region, pdf_path, depth=0))

    return result
