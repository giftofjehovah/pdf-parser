"""Stage 4: build DocNode subtree per TableRegion; recurse into cells for nested tables."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pdfplumber

from pdf_parser.model import BBox, MAX_DEPTH, DocNode
from pdf_parser.stages.detect_tables import TableRegion, detect_tables

_OVERLAP_TOL = 2.0  # points; guards against sub-pixel boundary mismatches


def _cell_align(page_chars: list[dict], cbox: BBox) -> str:
    """Return 'right' if cell text is right-aligned, 'left' otherwise.

    Compares the gap between the text's left edge and the cell's left edge
    against the gap between the text's right edge and the cell's right edge.
    A text block that sits much closer to the right wall is right-aligned.
    """
    chars = [
        c for c in page_chars
        if c.get("x0", 0) >= cbox.x0 - 1
        and c.get("x1", 0) <= cbox.x1 + 1
        and c.get("top", 0) >= cbox.y0 - 1
        and c.get("bottom", 0) <= cbox.y1 + 1
        and c.get("text", "").strip()
    ]
    if not chars:
        return "left"
    cell_w = cbox.x1 - cbox.x0
    if cell_w < 2:
        return "left"
    text_x0 = min(c["x0"] for c in chars)
    text_x1 = max(c["x1"] for c in chars)
    left_gap = text_x0 - cbox.x0
    right_gap = cbox.x1 - text_x1
    # Right-aligned: text sits markedly closer to the right wall.
    # Threshold: right gap < 30 % of the left gap AND < 6 pt absolute.
    if right_gap < left_gap * 0.30 and right_gap < 6.0:
        return "right"
    return "left"


def _page_y(page_height: float, pdf_y: float) -> float:
    """Convert PDF y (bottom-origin) to pdfplumber page y (top-origin)."""
    return page_height - pdf_y


def _outer_line_ys(page, table_bbox: tuple[float, float, float, float]) -> list[float]:
    """Return sorted page-space y-values of horizontal lines that mark row boundaries.

    Accepts any line that lies within the table's x-range AND y-range and spans
    at least 50 % of the table width.  The relaxed width threshold (vs. the
    original 100 %) handles row-spanning cells: a row-dividing line is suppressed
    only for the spanned column but still drawn across the remaining columns,
    producing a partial-width segment (≥ 50 % when at most half the columns are
    merged).

    The y-range filter is critical: without it, horizontal lines from unrelated
    tables elsewhere on the same page can be included as row boundaries, producing
    phantom extra rows whose cell bboxes extend into the unrelated table's region.
    """
    table_x0, table_y0, table_x1, table_y1 = table_bbox
    table_width = table_x1 - table_x0
    page_height = page.height
    ys: set[float] = set()
    for line in page.lines:
        if abs(line["y0"] - line["y1"]) < 1:  # horizontal
            # Must overlap with the table's y-range (top-origin pdfplumber coords).
            ln_top = min(line["top"], line["bottom"])
            if ln_top < table_y0 - _OVERLAP_TOL or ln_top > table_y1 + _OVERLAP_TOL:
                continue
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

    Lines are pre-filtered to those whose x lies within the table's x-range and
    whose y-span overlaps the table's y-range.  Without both filters,
    vertical lines from unrelated tables elsewhere on the same page can satisfy
    the height threshold (because the merged-cells table may be very short) and
    produce spurious columns.
    """
    table_x0, table_y0, table_x1, table_y1 = table_bbox
    table_height = table_y1 - table_y0
    if table_height <= 0:
        return []
    xs: set[float] = set()
    for ln in page.lines:
        if abs(ln["x0"] - ln["x1"]) < 1:  # vertical line
            ln_x = round(ln["x0"], 1)
            # Must lie within the table's x-range.
            if ln_x < table_x0 - _OVERLAP_TOL or ln_x > table_x1 + _OVERLAP_TOL:
                continue
            # Must overlap with the table's y-range (top-origin pdfplumber coords).
            ln_top = min(ln["top"], ln["bottom"])
            ln_bot = max(ln["top"], ln["bottom"])
            if ln_bot < table_y0 - _OVERLAP_TOL or ln_top > table_y1 + _OVERLAP_TOL:
                continue
            length = abs(ln["y1"] - ln["y0"])
            if length >= 0.70 * table_height:
                xs.add(ln_x)
    xs_sorted = sorted(xs)
    if len(xs_sorted) < 2:
        return []
    return list(zip(xs_sorted[:-1], xs_sorted[1:]))


def _logical_grid_from_table(
    page, t, page_index: int
) -> Optional[tuple[list[list[str]], list[list[BBox]], set[tuple[int, int]]]]:
    """
    Reconstruct the logical (merged-cell) grid for a pdfplumber table.

    Returns (logical_grid, logical_cell_bboxes, covered) where *covered* is the
    set of (row_idx, col_idx) positions that are spanned over by an earlier cell
    (colspan or rowspan).  Returns None if no outer-line structure is found.
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
    # A colspan row that spans the full table width suppresses all inner vertical
    # edges for that row.  When the fragment is short (e.g. the top portion of a
    # split merged-cells table), inner dividers only appear in the remaining rows
    # and may not reach the 70 % height threshold.  Fall back to the raw row with
    # the most non-None cells so we still recover the correct column structure.
    if len(col_xs) < 2:
        for row in raw_rows:
            cand = _outer_col_xs(row)
            if len(cand) > len(col_xs):
                col_xs = cand
    if not col_xs:
        return None

    row_boundaries = list(zip(outer_ys[:-1], outer_ys[1:]))
    logical_grid: list[list[str]] = []
    logical_cell_bboxes: list[list[BBox]] = []
    covered: set[tuple[int, int]] = set()

    for r_idx, (row_y0, row_y1) in enumerate(row_boundaries):
        row_texts: list[str] = []
        row_bboxes: list[BBox] = []
        for c_idx, (col_x0, col_x1) in enumerate(col_xs):
            if (r_idx, c_idx) in covered:
                row_texts.append("")
                row_bboxes.append(BBox(
                    page=page_index, x0=col_x0, y0=row_y0, x1=col_x1, y1=row_y1
                ))
                continue

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
            primary_raw: tuple[float, float, float, float] | None = None
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
                        if primary_raw is None:
                            primary_raw = (cx0, cy0, cx1, cy1)

            text = " ".join(cell_texts)

            # Detect colspan / rowspan from the primary raw cell's extent.
            actual_x1 = col_x1
            actual_y1 = row_y1
            if primary_raw is not None and text:
                _, _, raw_x1, raw_y1 = primary_raw
                # Colspan: raw cell extends beyond this column's right edge.
                if raw_x1 > col_x1 + _OVERLAP_TOL:
                    actual_x1 = raw_x1
                    for nc in range(c_idx + 1, len(col_xs)):
                        if col_xs[nc][0] < raw_x1 - _OVERLAP_TOL:
                            covered.add((r_idx, nc))
                # Rowspan: raw cell extends below this row's bottom edge.
                if raw_y1 > row_y1 + _OVERLAP_TOL:
                    actual_y1 = raw_y1
                    for nr in range(r_idx + 1, len(row_boundaries)):
                        if row_boundaries[nr][0] < raw_y1 - _OVERLAP_TOL:
                            covered.add((nr, c_idx))

            row_texts.append(text)
            row_bboxes.append(BBox(
                page=page_index, x0=col_x0, y0=row_y0, x1=actual_x1, y1=actual_y1
            ))
        logical_grid.append(row_texts)
        logical_cell_bboxes.append(row_bboxes)

    return logical_grid, logical_cell_bboxes, covered


def _build_cell(text: str, bbox: BBox, pdf_path: Path, depth: int,
                covered: bool = False, align: str = "left") -> DocNode:
    children: list[DocNode] = []
    if not covered and depth + 1 < MAX_DEPTH:
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
    attrs: dict = {"align": align}
    if covered:
        attrs["covered"] = True
    return DocNode(
        kind="cell",
        bbox=bbox,
        text=text if not children else None,
        children=children,
        attrs=attrs,
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def _build_table(
    region: TableRegion, pdf_path: Path, depth: int,
    page_chars: list[dict] | None = None,
) -> DocNode:
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
            align = _cell_align(page_chars, cbox) if page_chars else "left"
            cells.append(_build_cell(text, cbox, pdf_path, depth, align=align))
        rows.append(DocNode(
            kind="row",
            bbox=region.bbox,
            children=cells,
            attrs={"page": region.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=region.bbox,
        children=rows,
        attrs={
            "n_rows": len(region.grid),
            "n_cols": len(region.grid[0]) if region.grid else 0,
            "header_signature": tuple(region.grid[0]) if region.grid else (),
            "page": region.page_index,
            "page_height": region.page_height,
        },
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def _build_table_from_logical(
    logical_grid: list[list[str]],
    cell_bboxes: list[list[BBox]],
    region: TableRegion,
    pdf_path: Path,
    depth: int,
    covered: set[tuple[int, int]] | None = None,
    page_chars: list[dict] | None = None,
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
            is_covered = covered is not None and (r_idx, c_idx) in covered
            align = _cell_align(page_chars, cbox) if page_chars else "left"
            cells.append(_build_cell(text, cbox, pdf_path, depth, covered=is_covered, align=align))
        rows.append(DocNode(
            kind="row",
            bbox=region.bbox,
            children=cells,
            attrs={"page": region.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=region.bbox,
        children=rows,
        attrs={
            "n_rows": len(logical_grid),
            "n_cols": len(logical_grid[0]) if logical_grid else 0,
            "header_signature": tuple(logical_grid[0]) if logical_grid else (),
            "page": region.page_index,
            "page_height": region.page_height,
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
            page_chars = page.chars  # used for cell alignment detection
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
                grid, bboxes, cov = logical
                result.append(
                    _build_table_from_logical(
                        grid, bboxes, region, pdf_path, depth=0, covered=cov,
                        page_chars=page_chars,
                    )
                )
            else:
                result.append(_build_table(region, pdf_path, depth=0, page_chars=page_chars))

    return result
