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


# Floor on the gap-split threshold: even if the median row height is small
# (single-line tables of ~3pt), inter-row gaps under this never break the
# table.  8pt is the median-typography-leading lower bound — below it the
# gap is part of the table; above it, MULT × median_h takes over once rows
# are tall enough.  Tightened from 12pt to 8pt in Phase 5 so single-line
# header rows do not artificially merge prose lines below into the table
# at the 8-12pt gap range.
_TABLE_GAP_FLOOR_PT = 8.0


def _split_into_tables(rows: list[list[Cell]]) -> list[list[list[Cell]]]:
    """Adjacent rows with similar geometry AND small inter-row gap form one
    table candidate.

    Boundary checks: same page, matching left edges, and a
    gap-to-median-row-height ratio below ``_TABLE_GAP_MULT``.  Right-edge
    equality is NOT required: ``tight``-style cells (14b borderless long-text)
    carry per-cell content widths so the right edge varies by row by 20pt+.
    Column-count equality is NOT required either: a colspan-heavy row (e.g.
    a section subheader spanning the full width) carries fewer raw cells
    than its sibling data rows, and ``_rows_to_celltable`` reconciles the
    raggedness via column-anchor alignment.
    The gap floor (``_TABLE_GAP_FLOOR_PT``) keeps single-line tables from
    breaking on any inter-row gap; the median × multiplier dominates once
    rows are tall enough.
    """
    if not rows:
        return []
    tables: list[list[list[Cell]]] = [[rows[0]]]
    for r in rows[1:]:
        prev_table = tables[-1]
        prev = prev_table[-1]
        same_page = prev[0].bbox.page == r[0].bbox.page
        same_left = abs(prev[0].bbox.x0 - r[0].bbox.x0) <= 4.0
        if not (same_page and same_left):
            tables.append([r])
            continue
        gap = _row_top(r) - max(c.bbox.y1 for c in prev)
        heights = [_row_height(rr) for rr in prev_table]
        median_h = statistics.median(heights) if heights else 0.0
        if gap > max(_TABLE_GAP_MULT * median_h, _TABLE_GAP_FLOOR_PT):
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
) -> tuple[list[Cell | None], set[int]]:
    """Place each cell into the column where its LEFT edge sits; mark every
    additional column whose left edge is to the left of the cell's right
    edge as covered by a horizontal merge.

    Left-edge anchoring (rather than first-overlapping-anchor) prevents a
    non-spanning cell from being misassigned to a slot covered by an earlier
    cell's colspan when the cell's left edge coincides with an anchor's
    right edge under ``tol``.
    """
    slots: list[Cell | None] = [None] * len(anchors)
    covered_idx: set[int] = set()
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
        # left of the cell's right edge (with tol slack) are covered.
        for i in range(start_i + 1, len(anchors)):
            ax0, _ = anchors[i]
            if c.bbox.x1 > ax0 + tol:
                covered_idx.add(i)
            else:
                break
    return slots, covered_idx


def _rows_to_celltable(
    rows: list[list[Cell]], page_height: float
) -> CellTable | None:
    """Build a CellTable from raw rows by aligning each row's cells to the
    canonical column anchors.  Slots not filled by any cell are emitted as
    empty "covered" cells with bboxes synthesised from anchor x + row y."""
    if len(rows) < 2 or all(len(r) < 2 for r in rows):
        return None
    anchors = _column_anchors(rows)
    n_cols = len(anchors)
    if n_cols < 2:
        return None
    grid: list[list[str]] = []
    cell_bboxes: list[list[BBox]] = []
    covered: set[tuple[int, int]] = set()
    for r_idx, row in enumerate(rows):
        slots, cov = _assign_row_to_columns(row, anchors)
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
                ax0, ax1 = anchors[c_idx]
                row_grid.append("")
                row_bbs.append(BBox(
                    page=row[0].bbox.page,
                    x0=ax0, y0=row_top, x1=ax1, y1=row_bot,
                ))
                covered.add((r_idx, c_idx))
        for c_idx in cov:
            covered.add((r_idx, c_idx))
        grid.append(row_grid)
        cell_bboxes.append(row_bbs)
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


def _aggregate_recursive(cells: list[Cell], page_height: float) -> list[CellTable]:
    """Produce top-level tables; attach contained sub-tables as ``nested``.

    Iteration is over every ``_split_into_tables`` output, so the same
    sub-cluster of cells can appear both as the parent's ``nested`` entry and
    as a stand-alone top-level table.  The post-loop prune removes any top
    candidate whose rounded bbox already appears anywhere in the attached
    nested tree — eliminating the double appearance without losing the
    structural link.
    """
    top: list[CellTable] = []
    for table_rows in _split_into_tables(_row_cluster(cells)):
        ct = _rows_to_celltable(table_rows, page_height)
        if ct is None:
            continue
        used = {c.bbox.rounded() for row in table_rows for c in row}
        remaining = [c for c in cells if c.bbox.rounded() not in used]
        for r_idx, row in enumerate(table_rows):
            for c_idx, c in enumerate(row):
                inside = _cells_inside(remaining, c.bbox)
                if len(inside) < 4:
                    continue  # < 2×2 cannot form a table
                sub_tables = _aggregate_recursive(inside, page_height)
                if not sub_tables:
                    continue
                ct.nested.extend(sub_tables)
                # Clear the parent cell's text — the nested table replaces it.
                ct.grid[r_idx][c_idx] = ""
        top.append(ct)

    nested_bboxes: set = set()

    def _collect(t: CellTable) -> None:
        for sub in t.nested:
            nested_bboxes.add(sub.bbox.rounded())
            _collect(sub)

    for t in top:
        _collect(t)
    return [t for t in top if t.bbox.rounded() not in nested_bboxes]
