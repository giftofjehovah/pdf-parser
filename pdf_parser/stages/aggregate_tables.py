"""Cluster Cell records into tables.

Pipeline:
  1. Deduplicate cells by rounded bbox (line > gutter > text wins ties).
  2. Cluster cells by y-overlap into rows.
  3. Cluster rows by vertical adjacency into table candidates.
  4. Reject candidates < 2 rows or < 2 cols (single cells are not tables).
  5. Detect nesting via spatial containment: any cluster fully inside another
     cell's bbox becomes that cell's child ``CellTable``.
  6. Mark cells that are spanned over by a prior merged cell as covered.

Output: ``list[CellTable]`` per page.  The wiring layer
(:mod:`pdf_parser.stages.extract_tables_v2`) converts each ``CellTable`` to a
``DocNode(kind='table')`` with the contract documented in the plan header.
"""
from __future__ import annotations

import statistics


from dataclasses import dataclass, field

from pdf_parser.model import BBox
from pdf_parser.stages.detect_cells import Cell, CellBboxStyle, CellSource


@dataclass
class CellTable:
    page_index: int
    bbox: BBox
    grid: list[list[str]]
    cell_bboxes: list[list[BBox]]
    covered: set[tuple[int, int]]
    header_signature: tuple[str, ...]
    page_height: float
    nested: list[CellTable]
    source: CellSource
    # Bbox geometry — see ``pdf_parser.stages.detect_cells.CellBboxStyle``.
    # Propagated from the row's first cell; downstream
    # (``extract_tables_v2._celltable_to_docnode``) uses ``row_bboxes`` per row
    # when ``tight`` and ``bbox`` for every row when ``shared``.
    bbox_style: CellBboxStyle = "shared"
    row_bboxes: list[BBox] = field(default_factory=list)


_ROW_Y_TOL = 2.0           # pt; two cells share a row if y-midpoints within this
_TABLE_GAP_MULT = 2.5      # pt; row gap > N × median row height ends a table
# Cell-source precedence used by ``_dedupe_cells`` when multiple detectors
# fire on the same rounded bbox.  Higher rank wins.  Line edges are visible
# truth; gutter is a calibrated inference; text-strategy is a last resort.
_SOURCE_RANK = {"line": 3, "gutter": 2, "text": 1}


def _dedupe_cells(cells):
    """Drop cells that share a rounded bbox with a higher-ranked source.

    Mixed-source pages (e.g. ruled headers above an open body where both line
    and gutter cells fire on the header row) need one cell per rounded bbox;
    aggregation downstream assumes distinct cells per row/col slot.
    """
    best: dict[tuple[int, int, int, int, int], Cell] = {}
    for c in cells:
        key = c.bbox.rounded()
        cur = best.get(key)
        if cur is None or _SOURCE_RANK[c.source] > _SOURCE_RANK[cur.source]:
            best[key] = c
    return list(best.values())


# Slack on containment checks: cell bboxes drift by sub-pt amounts due to
# floating-point arithmetic on the underlying PDF coordinates, so a strict
# ``inner.x0 >= outer.x0`` rejects legitimate containment.  2pt clears every
# observed drift in the 27 golden fixtures.
_CONTAIN_TOL = 2.0


def _cells_inside(cells: list[Cell], outer: BBox) -> list[Cell]:
    """Return cells whose bbox lies inside ``outer`` (with ``_CONTAIN_TOL``
    slack).  Strict on size: at least one dimension must be smaller than the
    outer's by the same tolerance — otherwise an outer cell would 'contain'
    itself and we'd recurse forever.
    """
    return [
        c for c in cells
        if (c.bbox.page == outer.page
            and c.bbox.x0 >= outer.x0 - _CONTAIN_TOL
            and c.bbox.y0 >= outer.y0 - _CONTAIN_TOL
            and c.bbox.x1 <= outer.x1 + _CONTAIN_TOL
            and c.bbox.y1 <= outer.y1 + _CONTAIN_TOL
            and (c.bbox.x1 - c.bbox.x0 < outer.x1 - outer.x0 - _CONTAIN_TOL
                 or c.bbox.y1 - c.bbox.y0 < outer.y1 - outer.y0 - _CONTAIN_TOL))
    ]



def _row_cluster(cells: list[Cell]) -> list[list[Cell]]:
    """Bucket cells into rows by y-midpoint (page-aware)."""
    by_page: dict[int, list[Cell]] = {}
    for c in cells:
        by_page.setdefault(c.bbox.page, []).append(c)
    rows: list[list[Cell]] = []
    for page_cells in by_page.values():
        page_cells.sort(key=lambda c: (c.bbox.y0, c.bbox.x0))
        current: list[Cell] = []
        cur_y: float | None = None
        for c in page_cells:
            ymid = (c.bbox.y0 + c.bbox.y1) / 2.0
            if cur_y is None or abs(ymid - cur_y) <= _ROW_Y_TOL:
                current.append(c)
                cur_y = ymid if cur_y is None else (cur_y + ymid) / 2.0
            else:
                rows.append(sorted(current, key=lambda c: c.bbox.x0))
                current = [c]
                cur_y = ymid
        if current:
            rows.append(sorted(current, key=lambda c: c.bbox.x0))
    return rows


def _row_height(row: list[Cell]) -> float:
    return max(c.bbox.y1 for c in row) - min(c.bbox.y0 for c in row)


def _row_top(row: list[Cell]) -> float:
    return min(c.bbox.y0 for c in row)


# Rowspan-tall threshold: cells whose height exceeds ``_ROWSPAN_HEIGHT_MULT``
# times the median multi-cell row height are treated as vertical-merge
# (rowspan) cells.  1.5× discriminates a true rowspan (typically 2× or 3×
# the per-row height) from harmless cell-content padding while staying
# above the noise floor of pdfplumber's per-row y-bound rounding.
_ROWSPAN_HEIGHT_MULT = 1.5


def _apply_rowspan_merge(rows: list[list[Cell]]) -> list[list[Cell]]:
    """Move tall single-cell rows into the FIRST multi-cell row whose y-mid
    falls inside the tall cell's y-range.

    ``_row_cluster`` buckets cells by y-midpoint; a tall rowspan cell whose
    ymid diverges from its shorter neighbours' ends up alone in its own
    narrow row sandwiched between the visual rows of its neighbours.
    ``_split_into_tables`` then sees the left-edge change between the tall
    cell's column and the shorter cells' column and splits the table
    apart — yielding two unrelated CellTables instead of one with covered
    rowspan slots (fixtures 10 / 21 + Annex E in 13_comprehensive,
    + ``test_merged_cells_correct_structure``).

    This pre-split pass relocates each such tall cell into the first row
    it overlaps so the table stays whole; the rowspan-covered bookkeeping
    happens later in :func:`_rows_to_celltable`'s post-pass, which
    overwrites the covered slots' bboxes with the spanning cell's bbox.

    No-op when there are fewer than 3 rows (a rowspan needs at least one
    anchor row + one covered row beneath it) or no multi-cell row
    exposes a non-zero median height to compare against.
    """
    if len(rows) < 3:
        return rows
    multi_cell_heights = [_row_height(r) for r in rows if len(r) >= 2]
    if not multi_cell_heights:
        return rows
    median_h = statistics.median(multi_cell_heights)
    if median_h <= 0:
        return rows

    # Identify tall single-cell rows.
    tall_indices: list[int] = []
    for i, r in enumerate(rows):
        if len(r) != 1:
            continue
        if _row_height(r) > _ROWSPAN_HEIGHT_MULT * median_h:
            tall_indices.append(i)
    if not tall_indices:
        return rows

    rows_to_remove: set[int] = set()
    rows_to_merge: dict[int, list[Cell]] = {}
    for ti in tall_indices:
        tall_cell = rows[ti][0]
        tcy0, tcy1 = tall_cell.bbox.y0, tall_cell.bbox.y1
        first_overlap: int | None = None
        for j, r in enumerate(rows):
            if j == ti or len(r) < 2 or r[0].bbox.page != tall_cell.bbox.page:
                continue
            row_ymid = statistics.fmean(
                (c.bbox.y0 + c.bbox.y1) / 2.0 for c in r
            )
            if tcy0 - 2.0 <= row_ymid <= tcy1 + 2.0:
                if first_overlap is None or j < first_overlap:
                    first_overlap = j
        if first_overlap is None:
            continue
        rows_to_merge.setdefault(first_overlap, []).append(tall_cell)
        rows_to_remove.add(ti)

    if not rows_to_remove:
        return rows

    adjusted: list[list[Cell]] = []
    for i, r in enumerate(rows):
        if i in rows_to_remove:
            continue
        merged = list(r)
        if i in rows_to_merge:
            merged.extend(rows_to_merge[i])
            merged.sort(key=lambda c: c.bbox.x0)
        adjusted.append(merged)
    return adjusted


# Floor on the gap-split threshold: even if the median row height is small
# (single-line tables of ~3pt), inter-row gaps under this never break the
# table.  8pt is the median-typography-leading lower bound — below it the
# gap is part of the table; above it, MULT × median_h takes over once rows
# are tall enough.  Tightened from 12pt to 8pt in Phase 5 so single-line
# header rows do not artificially merge prose lines below into the table
# at the 8-12pt gap range.
_TABLE_GAP_FLOOR_PT = 8.0
# Tighter multiplier used by ``_aggregate_recursive`` when descending into
# the middle bay of a detected outer-frame container.  Inter-sub-table gaps
# inside a container (fixtures 16/17 + Annex C/D in 13_comprehensive) sit
# at ~1.5–2× the per-cell row height because the spacer between sub-tables
# is pure whitespace, not a detectable spacer cell.  1.2× catches them
# while still tolerating the small 0.5-row drift between a sub-table's
# header and body rows.
_NESTED_CONTAINER_GAP_MULT = 1.2


def _split_into_tables(
    rows: list[list[Cell]],
    *,
    gap_mult: float = _TABLE_GAP_MULT,
    gap_floor: float = _TABLE_GAP_FLOOR_PT,
) -> list[list[list[Cell]]]:
    """Adjacent rows with similar geometry AND small inter-row gap form one
    table candidate.

    Boundary checks: same page, matching left edges, and a
    gap-to-median-row-height ratio below ``gap_mult``.  Right-edge
    equality is NOT required: ``tight``-style cells (14b borderless long-text)
    carry per-cell content widths so the right edge varies by row by 20pt+.
    Column-count equality is NOT required either: a colspan-heavy row (e.g.
    a section subheader spanning the full width) carries fewer raw cells
    than its sibling data rows, and ``_rows_to_celltable`` reconciles the
    raggedness via column-anchor alignment.
    The gap floor (``gap_floor``) keeps single-line tables from
    breaking on any inter-row gap; the median × multiplier dominates once
    rows are tall enough.

    ``gap_mult`` and ``gap_floor`` default to module-level constants; the
    recursive aggregation passes tighter values when descending into a
    detected container frame whose middle cell hosts multiple sibling
    sub-tables separated only by inter-table whitespace (no detectable
    spacer cell), as in fixtures 16/17 / Annex C-D of 13_comprehensive —
    otherwise their 30pt inter-sub-table gap (~1.67× the 18pt row height)
    sits below the default 2.5× threshold and the sub-tables fuse.
    """
    if not rows:
        return []
    tables: list[list[list[Cell]]] = [[rows[0]]]
    for r in rows[1:]:
        prev_table = tables[-1]
        prev = prev_table[-1]
        same_page = prev[0].bbox.page == r[0].bbox.page
        # Compare against the PREV TABLE's leftmost cell (over all its rows)
        # rather than just the previous row's leftmost.  After rowspan
        # merging (:func:`_apply_rowspan_merge`), an intermediate row may
        # be MISSING its leftmost cell because a rowspan from above covers
        # that column — using prev's leftmost would then mis-split the
        # next row that DOES have a cell at the table's true left anchor.
        table_left = min(c.bbox.x0 for prow in prev_table for c in prow)
        same_left = abs(table_left - r[0].bbox.x0) <= 4.0
        # Rowspan tolerance: a row missing its leftmost cell because a
        # rowspan from an EARLIER row in the current table covers the
        # leftmost column still belongs to that table.  The covering cell
        # must (a) sit strictly left of this row's leftmost cell and (b)
        # y-cover this row's y-range.
        if not same_left and same_page:
            r_y0 = _row_top(r)
            r_y1 = max(c.bbox.y1 for c in r)
            r_left = r[0].bbox.x0
            for pr in prev_table:
                for c in pr:
                    if (c.bbox.x0 < r_left - 4.0
                            and c.bbox.y0 <= r_y0 + 2.0
                            and c.bbox.y1 >= r_y1 - 2.0):
                        same_left = True
                        break
                if same_left:
                    break
        if not (same_page and same_left):
            tables.append([r])
            continue
        gap = _row_top(r) - max(c.bbox.y1 for c in prev)
        heights = [_row_height(rr) for rr in prev_table]
        median_h = statistics.median(heights) if heights else 0.0
        if gap > max(gap_mult * median_h, gap_floor):
            tables.append([r])
        else:
            prev_table.append(r)
    return tables


# Column-anchor matching tolerance: cell x-positions drift sub-pt due to
# floating-point coordinate arithmetic; 4pt covers every observed drift
# in the 27 golden fixtures.
_ANCHOR_TOL = 4.0


def _column_anchors(rows: list[list[Cell]]) -> list[tuple[float, float]]:
    """Cluster cell ``x0`` positions across ALL rows to form the canonical column set.

    The previous "widest row" heuristic failed when no single row carried the
    full set of column boundaries — e.g. row A merges cols 0+1, row B merges
    cols 2+3, so neither row alone exposes all 4 boundaries.  Union-clustering
    x0 positions across every row recovers the full column set: each column
    boundary that appears in any row contributes one anchor.
    """
    tol = _ANCHOR_TOL
    positions = sorted({c.bbox.x0 for r in rows for c in r})
    if not positions:
        return []
    clusters: list[list[float]] = [[positions[0]]]
    for p in positions[1:]:
        if p - clusters[-1][-1] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    cluster_x0 = [statistics.fmean(c) for c in clusters]
    max_x1 = max(c.bbox.x1 for r in rows for c in r)
    anchors: list[tuple[float, float]] = []
    for i, x0 in enumerate(cluster_x0):
        x1 = cluster_x0[i + 1] if i + 1 < len(cluster_x0) else max_x1
        anchors.append((x0, x1))
    return anchors


def _assign_row_to_columns(
    row: list[Cell], anchors: list[tuple[float, float]], tol: float = _ANCHOR_TOL
) -> tuple[list[Cell | None], dict[int, BBox]]:
    """Place each cell into the column where its LEFT edge sits; mark every
    additional column whose left edge is to the left of the cell's right
    edge as covered by a horizontal merge.

    Left-edge anchoring (rather than first-overlapping-anchor) prevents a
    non-spanning cell from being misassigned to a slot covered by an earlier
    cell's colspan when the cell's left edge coincides with an anchor's
    right edge under ``tol``.

    Returns ``(slots, covered_bboxes)``.  ``covered_bboxes[i]`` is the bbox
    to record for slot ``i`` when that slot is covered by an earlier cell's
    colspan in this row; it uses the SPANNING cell's own y-extent (the
    legacy ``_logical_grid_from_table`` convention) so id-set parity holds
    against the legacy path for fixtures whose rows have non-uniform cell
    heights (10, 21).
    """
    slots: list[Cell | None] = [None] * len(anchors)
    covered_bboxes: dict[int, BBox] = {}
    for c in row:
        start_i: int | None = None
        for i, (ax0, ax1) in enumerate(anchors):
            # Cell's left edge falls inside this column's [ax0, ax1) range.
            # The strict upper bound prevents a cell starting exactly at the
            # next column's left edge from being placed in this column.
            if ax0 - tol <= c.bbox.x0 < ax1 - tol:
                start_i = i
                break
        if start_i is None:
            continue
        if slots[start_i] is None:
            slots[start_i] = c
        # Determine colspan: subsequent anchors whose left edge sits to the
        # left of the cell's right edge (with tol slack) are covered.  Use
        # the spanning cell's own y-extent for the synthesised covered bbox.
        for i in range(start_i + 1, len(anchors)):
            ax0, ax1 = anchors[i]
            if c.bbox.x1 > ax0 + tol:
                covered_bboxes[i] = BBox(
                    page=c.bbox.page,
                    x0=ax0, y0=c.bbox.y0, x1=ax1, y1=c.bbox.y1,
                )
            else:
                break
    return slots, covered_bboxes


def _rows_to_celltable(
    rows: list[list[Cell]], page_height: float
) -> CellTable | None:
    """Build a CellTable from raw rows by aligning each row's cells to the
    canonical column anchors.  Slots not filled by any cell are emitted as
    empty "covered" cells; cells covered by an earlier in-row colspan use
    the spanning cell's y-extent for legacy parity, sparse slots
    (no spanning cell in this row) fall back to anchor x + row y, and
    slots whose y-range falls inside an earlier row's TALL cell at the
    same column inherit the tall cell's bbox (rowspan covered)."""
    # Accept 1-row N-col candidates (fixture 23: a single bordered band
    # split into Label + bulleted-prose by visible verticals).  Reject
    # only when EVERY row is a single cell -- lone bordered blocks and
    # split-off footers must not surface as spurious tables.
    if all(len(r) < 2 for r in rows):
        return None
    anchors = _column_anchors(rows)
    n_cols = len(anchors)
    if n_cols < 2:
        return None
    grid: list[list[str]] = []
    cell_bboxes: list[list[BBox]] = []
    covered: set[tuple[int, int]] = set()
    for r_idx, row in enumerate(rows):
        slots, cov_bboxes = _assign_row_to_columns(row, anchors)
        row_top = min(c.bbox.y0 for c in row) if row else 0.0
        row_bot = max(c.bbox.y1 for c in row) if row else 0.0
        row_grid: list[str] = []
        row_bbs: list[BBox] = []
        for c_idx in range(n_cols):
            cell = slots[c_idx]
            if cell is not None:
                row_grid.append(cell.text)
                row_bbs.append(cell.bbox)
            else:
                row_grid.append("")
                if c_idx in cov_bboxes:
                    # Covered by a horizontal-merge in this row: spanning-cell y-extent.
                    row_bbs.append(cov_bboxes[c_idx])
                else:
                    # Sparse slot (no spanning cell in this row): anchor x + row y.
                    ax0, ax1 = anchors[c_idx]
                    row_bbs.append(BBox(
                        page=row[0].bbox.page,
                        x0=ax0, y0=row_top, x1=ax1, y1=row_bot,
                    ))
                covered.add((r_idx, c_idx))
        grid.append(row_grid)
        cell_bboxes.append(row_bbs)

    # Rowspan covered slots inherit their bboxes from the sparse-slot
    # branch above (anchor x-range + sub-row y-range) -- legacy's
    # ``_logical_grid_from_table`` uses exactly that shape for every
    # covered slot (colspan AND rowspan), so no rowspan-aware post-pass
    # is needed.  The earlier ``_assign_row_to_columns`` already marked
    # the slot ``covered`` via ``covered.add((sub_r, col_idx))`` in the
    # ``cell is None`` branch, which is the only structural fact the
    # downstream renderer needs.
    page = rows[0][0].bbox.page
    x0 = min(c.bbox.x0 for r in rows for c in r)
    y0 = min(c.bbox.y0 for r in rows for c in r)
    x1 = max(c.bbox.x1 for r in rows for c in r)
    y1 = max(c.bbox.y1 for r in rows for c in r)
    row_bboxes = [
        BBox(
            page=page,
            x0=min(c.bbox.x0 for c in r),
            y0=min(c.bbox.y0 for c in r),
            x1=max(c.bbox.x1 for c in r),
            y1=max(c.bbox.y1 for c in r),
        )
        for r in rows
    ]
    return CellTable(
        page_index=page,
        bbox=BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1),
        grid=grid,
        cell_bboxes=cell_bboxes,
        covered=covered,
        header_signature=tuple(grid[0]),
        page_height=page_height,
        nested=[],
        source=rows[0][0].source,
        bbox_style=rows[0][0].bbox_style,
        row_bboxes=row_bboxes,
    )




def aggregate(cells: list[Cell], page_height: float) -> list[CellTable]:
    """Group ``cells`` into tables, attaching nested sub-tables via spatial
    containment.  See module docstring.
    """
    if not cells:
        return []
    cells = _dedupe_cells(cells)
    return _aggregate_recursive(cells, page_height)


def _find_bracketing_text_cells(
    c: Cell, cells: list[Cell], tol: float = 2.0
) -> tuple[Cell | None, Cell | None]:
    """Return (L, R): closest text-bearing tall cells flanking ``c`` whose
    y-range strictly extends beyond ``c``'s on at least one side.

    Tall = same y-band as c (b.y0 ≤ cy0 AND b.y1 ≥ cy1) plus a strict
    extension on at least one side.  Text-bearing = non-empty stripped text,
    so empty lattice-fragment fillers around a sub-table do not qualify.
    """
    page = c.bbox.page
    cy0, cy1 = c.bbox.y0, c.bbox.y1
    cx0, cx1 = c.bbox.x0, c.bbox.x1
    L: Cell | None = None
    R: Cell | None = None
    for other in cells:
        if other is c or other.bbox.page != page:
            continue
        if not (other.text and other.text.strip()):
            continue
        b = other.bbox
        if not (b.y0 <= cy0 + tol and b.y1 >= cy1 - tol):
            continue
        if not (b.y0 < cy0 - tol or b.y1 > cy1 + tol):
            continue
        if b.x1 <= cx0 + tol:
            if L is None or b.x1 > L.bbox.x1:
                L = other
        elif b.x0 >= cx1 - tol:
            if R is None or b.x0 < R.bbox.x0:
                R = other
    return L, R


def _carve_subclusters(
    cells: list[Cell],
) -> tuple[list[Cell], list[Cell]]:
    """Carve nested sub-clusters from ``cells`` using tall-cell brackets.

    For each cell, identifies its closest text-bearing tall-cell brackets (L
    and R).  Cells sharing the same (L, R) pair belong to one synthetic
    parent region defined by (L.x1, max(L.y0, R.y0), R.x0, min(L.y1, R.y1)).
    Regions containing ≥4 cells are carved: a synthetic empty parent cell
    replaces all members in ``top_cells``, while sub-table members (those
    with y-extent strictly less than the parent's, i.e. NOT lattice fillers)
    are retained in ``nested_pool`` for the recursive nested attachment to
    find.

    Returns ``(top_cells, nested_pool)``:
      * ``top_cells``: input cells with carved members removed plus synthetic
        parents — feeds row-clustering and ``_rows_to_celltable``.
      * ``nested_pool``: input cells with lattice fillers (full-height empty
        cells inside a carved region) removed — feeds ``_cells_inside``
        during nested attachment so sub-table cells participate but
        non-content fillers do not pollute the recursive aggregation.

    No-op when no parent region reaches the ≥4 threshold (returns the input
    list as both outputs).
    """
    tol = 2.0
    parent_members: dict[tuple, list[Cell]] = {}
    for c in cells:
        L, R = _find_bracketing_text_cells(c, cells, tol)
        if L is None or R is None:
            continue
        py0 = max(L.bbox.y0, R.bbox.y0)
        py1 = min(L.bbox.y1, R.bbox.y1)
        if py1 <= py0:
            continue
        if not (c.bbox.y0 >= py0 - tol and c.bbox.y1 <= py1 + tol):
            continue
        key = (
            c.bbox.page,
            round(L.bbox.x1, 1),
            round(py0, 1),
            round(R.bbox.x0, 1),
            round(py1, 1),
        )
        parent_members.setdefault(key, []).append(c)

    valid = {k: v for k, v in parent_members.items() if len(v) >= 4}
    if not valid:
        return cells, cells

    carved_ids: set[int] = set()
    filler_ids: set[int] = set()
    synthetic: list[Cell] = []
    for key in valid:
        page, x0, y0_, x1, y1_ = key
        parent_h = y1_ - y0_
        bbox = BBox(page=page, x0=float(x0), y0=float(y0_),
                    x1=float(x1), y1=float(y1_))
        for c in cells:
            if c.bbox.page != page:
                continue
            b = c.bbox
            inside = (
                b.x0 >= x0 - tol and b.y0 >= y0_ - tol
                and b.x1 <= x1 + tol and b.y1 <= y1_ + tol
            )
            if not inside:
                continue
            carved_ids.add(id(c))
            # Cell with full parent height + empty text = lattice filler.
            cell_h = b.y1 - b.y0
            if cell_h >= parent_h - tol and not (c.text and c.text.strip()):
                filler_ids.add(id(c))
        synthetic.append(Cell(
            bbox=bbox,
            text="",
            source="line",
            confidence=1.0,
            bbox_style="shared",
        ))

    top_cells = [c for c in cells if id(c) not in carved_ids]
    top_cells.extend(synthetic)
    nested_pool = [c for c in cells if id(c) not in filler_ids]
    return top_cells, nested_pool


def _is_strictly_inside(inner: Cell, outer: Cell, tol: float = _CONTAIN_TOL) -> bool:
    """True when ``inner`` is a *different* cell that fits inside ``outer``'s
    bbox AND is strictly smaller on at least one axis.

    Mirrors :func:`_cells_inside` but operates on cell pairs and is safe to
    call across the full cell list (skips identity + cross-page comparisons).
    """
    if inner is outer or inner.bbox.page != outer.bbox.page:
        return False
    ib, ob = inner.bbox, outer.bbox
    if not (
        ib.x0 >= ob.x0 - tol and ib.y0 >= ob.y0 - tol
        and ib.x1 <= ob.x1 + tol and ib.y1 <= ob.y1 + tol
    ):
        return False
    return (
        ib.x1 - ib.x0 < ob.x1 - ob.x0 - tol
        or ib.y1 - ib.y0 < ob.y1 - ob.y0 - tol
    )


def _carve_container_frames(
    cells: list[Cell],
) -> tuple[list[Cell], list[Cell], set[int]]:
    """Isolate 1xN outer-frame container cells from the top-level row pool.

    Detects "container" cells — cells that strictly enclose ≥4 other cells
    on the same page.  In the bottom-up pipeline these come from
    pdfplumber's line strategy emitting an outer 3x1 wrapper (header /
    middle-container / footer) alongside the per-cell rows of the inner
    sub-tables, as in fixtures 16/17 and Annex C of 13_comprehensive.

    Without isolation, the container's y-range overlaps the inner rows,
    causing ``_row_cluster`` to place the container in its own row
    sandwiched between header and the inner cells; ``_split_into_tables``
    then splits on the left-edge change between the outer frame's x0 and
    the inner sub-tables' x0, fusing the inner sub-tables into one flat
    multi-row table and dropping the outer wrapper entirely (the residual
    fragments are rejected by ``_rows_to_celltable``'s n_cols ≥ 2 check).

    Returns ``(top_cells, nested_pool, container_ids)``:
      * ``top_cells``: input cells with the strictly-inside cells of every
        container removed.  Container cells themselves stay — they will
        cluster into a clean 1xN wrapper candidate without the inner cells
        bleeding into the same row pool.
      * ``nested_pool``: input cells with the container cells themselves
        removed — feeds ``_cells_inside`` for nested attachment so the
        inner sub-tables surface as nested descendants of the wrapper's
        middle cell (and the wrapper itself never attaches to itself).
      * ``container_ids``: ``id(...)`` of every detected container cell,
        used downstream by ``_aggregate_recursive`` to recognise rejected
        single-column candidates that contain a container — these escape
        the standard n_cols < 2 rejection via a 1xN wrapper builder.

    No-op (returns the input list as both outputs + empty set) when no
    cell strictly contains ≥4 others.
    """
    container_ids: set[int] = set()
    inner_ids: set[int] = set()
    for outer in cells:
        contained = [c for c in cells if _is_strictly_inside(c, outer)]
        if len(contained) < 4:
            continue
        container_ids.add(id(outer))
        for c in contained:
            inner_ids.add(id(c))
    if not container_ids:
        return cells, cells, container_ids
    top_cells = [c for c in cells if id(c) not in inner_ids]
    nested_pool = [c for c in cells if id(c) not in container_ids]
    return top_cells, nested_pool, container_ids


def _build_single_col_wrapper(
    rows: list[list[Cell]], page_height: float
) -> CellTable | None:
    """Build a 1xN wrapper CellTable from rows that each carry exactly one cell.

    Used by ``_aggregate_recursive`` to emit the outer frame of fixtures
    16/17/Annex C/D when ``_rows_to_celltable`` rejects the candidate for
    having no row with ≥2 cells.  The caller is responsible for verifying
    that at least one row's cell strictly contains other (nested-pool)
    cells — otherwise this would promote ordinary single-column rejects
    (e.g. lone bordered text blocks) into spurious tables.
    """
    if len(rows) < 2 or any(len(r) != 1 for r in rows):
        return None
    page = rows[0][0].bbox.page
    x0 = min(c.bbox.x0 for r in rows for c in r)
    y0 = min(c.bbox.y0 for r in rows for c in r)
    x1 = max(c.bbox.x1 for r in rows for c in r)
    y1 = max(c.bbox.y1 for r in rows for c in r)
    grid = [[r[0].text] for r in rows]
    cell_bboxes = [[r[0].bbox] for r in rows]
    row_bboxes = [r[0].bbox for r in rows]
    return CellTable(
        page_index=page,
        bbox=BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1),
        grid=grid,
        cell_bboxes=cell_bboxes,
        covered=set(),
        header_signature=tuple(grid[0]),
        page_height=page_height,
        nested=[],
        source=rows[0][0].source,
        bbox_style=rows[0][0].bbox_style,
        row_bboxes=row_bboxes,
    )


# Wrapper-placeholder gate: a nested sub-table whose width spans at least
# this fraction of the wrapper's width contributes its row-y boundaries as
# wrapper H-lines.  Matches legacy ``_outer_line_ys``'s 50% rule in
# ``extract_tables.py`` -- inner sub-table H-lines that pass the gate get
# treated as outer row boundaries, producing covered placeholder rows in
# the wrapper that mirror the inner sub-table row layout.
_WRAPPER_INNER_H_LINE_WIDTH_RATIO = 0.5
# Inset tolerance for "strictly inside the container row" -- inner H-lines
# whose y equals the container's y0 or y1 (e.g. the container's own top /
# bottom edge) are the wrapper's outer rows, not placeholders.
_WRAPPER_INNER_TOL = 1.0


def _expand_wrapper_with_placeholders(ct: CellTable) -> None:
    """Insert covered placeholder rows mirroring inner sub-table row
    boundaries that span at least :data:`_WRAPPER_INNER_H_LINE_WIDTH_RATIO`
    of the wrapper's width.

    Mirrors legacy ``_logical_grid_from_table`` (extract_tables.py
    lines 178-186 + 51-81): every H-line wider than 50% of the outer
    table's width is a row boundary; inner sub-table H-lines that pass
    the gate become wrapper rows.  The container cell rowspan-covers
    them so they emit as ``covered=True`` slots at the wrapper's full
    width.

    No-op when:
      * ``ct`` is not a 1xN wrapper (some row has >=2 cells).
      * No nested sub-table passes the width gate (fixture 17 per-page
        wrapper: inner sub-table 180pt, wrapper 400pt -> 45% < 50%).
      * No inner H-line position lies strictly inside the container's
        y-range (sub-tables flush to the container top/bottom only).

    Mutates ``ct`` in place: extends ``grid``, ``cell_bboxes``,
    ``row_bboxes`` with the new placeholders and shifts existing
    ``covered`` entries for rows after the container down by the
    number of inserted placeholders.
    """
    if not ct.nested:
        return
    if any(len(r) != 1 for r in ct.grid):
        return
    wrapper_width = ct.bbox.x1 - ct.bbox.x0
    if wrapper_width <= 0:
        return
    qualifying = [
        s for s in ct.nested
        if (s.bbox.x1 - s.bbox.x0) / wrapper_width >= _WRAPPER_INNER_H_LINE_WIDTH_RATIO
    ]
    if not qualifying:
        return

    container_r_idx: int | None = None
    for r_idx, row_bbs in enumerate(ct.cell_bboxes):
        cb = row_bbs[0]
        if any(
            s.bbox.y0 >= cb.y0 - _WRAPPER_INNER_TOL
            and s.bbox.y1 <= cb.y1 + _WRAPPER_INNER_TOL
            for s in qualifying
        ):
            container_r_idx = r_idx
            break
    if container_r_idx is None:
        return

    container_bb = ct.cell_bboxes[container_r_idx][0]
    h_positions: set[float] = set()
    for s in qualifying:
        for rb in s.row_bboxes:
            for y in (rb.y0, rb.y1):
                if (container_bb.y0 + _WRAPPER_INNER_TOL
                        < y
                        < container_bb.y1 - _WRAPPER_INNER_TOL):
                    h_positions.add(y)
    if not h_positions:
        return

    # Pairs of consecutive boundaries, final pair ending at container.y1.
    boundary_ys = sorted(h_positions) + [container_bb.y1]
    placeholder_pairs = [
        (boundary_ys[i], boundary_ys[i + 1])
        for i in range(len(boundary_ys) - 1)
    ]
    if not placeholder_pairs:
        return

    page = ct.bbox.page
    insertion_r = container_r_idx + 1
    shift_n = len(placeholder_pairs)

    shifted_covered: set[tuple[int, int]] = {
        (r + shift_n, c) if r >= insertion_r else (r, c)
        for (r, c) in ct.covered
    }

    new_grid = list(ct.grid[:insertion_r])
    new_cell_bboxes = list(ct.cell_bboxes[:insertion_r])
    new_row_bboxes = list(ct.row_bboxes[:insertion_r])
    for new_offset, (y0, y1) in enumerate(placeholder_pairs):
        new_r = insertion_r + new_offset
        new_grid.append([""])
        new_cell_bboxes.append([BBox(
            page=page, x0=ct.bbox.x0, y0=y0, x1=ct.bbox.x1, y1=y1,
        )])
        new_row_bboxes.append(BBox(
            page=page, x0=ct.bbox.x0, y0=y0, x1=ct.bbox.x1, y1=y1,
        ))
        shifted_covered.add((new_r, 0))
    new_grid.extend(ct.grid[insertion_r:])
    new_cell_bboxes.extend(ct.cell_bboxes[insertion_r:])
    new_row_bboxes.extend(ct.row_bboxes[insertion_r:])

    ct.grid = new_grid
    ct.cell_bboxes = new_cell_bboxes
    ct.row_bboxes = new_row_bboxes
    ct.covered = shifted_covered


def _aggregate_recursive(
    cells: list[Cell],
    page_height: float,
    *,
    gap_mult: float = _TABLE_GAP_MULT,
) -> list[CellTable]:
    """Produce top-level tables; attach contained sub-tables as ``nested``.

    Sub-cluster carve-out (see :func:`_carve_subclusters`): nested sub-table
    cells whose Inputs-style container is fragmented by the line detector
    are identified via tall-cell brackets and replaced in ``top_cells`` by a
    single synthetic parent cell.  Lattice-filler fragments (full-height
    empty cells around the sub-table) are discarded from ``nested_pool`` so
    they neither split the parent's row at the sub-table's ``x0`` nor
    pollute the recursive sub-table aggregation.

    Container-frame carve-out (see :func:`_carve_container_frames`): outer
    1xN wrapper cells (header / middle-container / footer stacks emitted
    by pdfplumber's line strategy alongside per-cell rows of inner
    sub-tables, as in fixtures 16/17 + Annex C of 13_comprehensive) have
    their strictly-inside cells stripped from ``top_cells`` so the wrapper
    candidate forms cleanly.  When ``_rows_to_celltable`` then rejects the
    candidate for having no row with ≥2 cells, the fallback
    :func:`_build_single_col_wrapper` emits it as a 1xN CellTable provided
    at least one of its rows references a carved container.

    Iteration is over every ``_split_into_tables`` output, so the same
    sub-cluster of cells can appear both as the parent's ``nested`` entry and
    as a stand-alone top-level table.  The post-loop prune removes any top
    candidate whose rounded bbox already appears anywhere in the attached
    nested tree — eliminating the double appearance without losing the
    structural link.
    """
    top_after_sub, nested_pool_sub = _carve_subclusters(cells)
    top_cells, _np_container, container_ids = _carve_container_frames(top_after_sub)
    # Final nested pool: filler-removed (sub-cluster carve) AND container-removed
    # (container carve).  Containers must not feed nested attachment because the
    # outer wrapper would otherwise self-attach during the recursive descent.
    nested_pool = [c for c in nested_pool_sub if id(c) not in container_ids]

    top: list[CellTable] = []
    for table_rows in _split_into_tables(
        _apply_rowspan_merge(_row_cluster(top_cells)), gap_mult=gap_mult,
    ):
        ct = _rows_to_celltable(table_rows, page_height)
        if ct is None:
            # Fallback: 1xN outer wrapper.  Only emit when at least one cell
            # in the candidate is a known container — otherwise ordinary
            # single-column rejects (lone bordered text blocks, footers
            # split off by a left-edge change) would surface as spurious
            # tables.
            has_container = any(
                id(c) in container_ids for r in table_rows for c in r
            )
            if not has_container:
                continue
            ct = _build_single_col_wrapper(table_rows, page_height)
            if ct is None:
                continue
        used = {c.bbox.rounded() for row in table_rows for c in row}
        remaining = [c for c in nested_pool if c.bbox.rounded() not in used]
        for r_idx, row in enumerate(table_rows):
            for c_idx, c in enumerate(row):
                inside = _cells_inside(remaining, c.bbox)
                if len(inside) < 4:
                    continue  # < 2×2 cannot form a table
                # When the parent cell is an explicit outer-frame container,
                # its middle bay typically hosts multiple sibling sub-tables
                # separated by inter-table whitespace only (no detectable
                # spacer cell).  Tighten the gap multiplier so the recursive
                # split breaks at gaps as small as ~1× the row height, instead
                # of the default 2.5× that would fuse the sub-tables.
                sub_gap_mult = (
                    _NESTED_CONTAINER_GAP_MULT
                    if id(c) in container_ids
                    else _TABLE_GAP_MULT
                )
                sub_tables = _aggregate_recursive(
                    inside, page_height, gap_mult=sub_gap_mult,
                )
                if not sub_tables:
                    continue
                ct.nested.extend(sub_tables)
                # Clear the parent cell's text — the nested table replaces it.
                ct.grid[r_idx][c_idx] = ""
        _expand_wrapper_with_placeholders(ct)
        top.append(ct)

    nested_bboxes: set = set()

    def _collect(t: CellTable) -> None:
        for sub in t.nested:
            nested_bboxes.add(sub.bbox.rounded())
            _collect(sub)

    for t in top:
        _collect(t)
    return [t for t in top if t.bbox.rounded() not in nested_bboxes]
