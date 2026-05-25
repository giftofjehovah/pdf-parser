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


def _split_into_tables(rows: list[list[Cell]]) -> list[list[list[Cell]]]:
    """Adjacent rows with similar geometry form one table candidate.

    Boundary checks: same page, same column count, and matching left edges.
    Right-edge equality is NOT required: ``tight``-style cells (14b borderless
    long-text) carry per-cell content widths, so the right edge varies by row
    by 20pt+.  Column-count + left-edge anchor is the stable invariant.
    """
    if not rows:
        return []
    tables: list[list[list[Cell]]] = [[rows[0]]]
    for r in rows[1:]:
        prev = tables[-1][-1]
        same_page = prev[0].bbox.page == r[0].bbox.page
        same_ncols = len(prev) == len(r)
        same_left = abs(prev[0].bbox.x0 - r[0].bbox.x0) <= 4.0
        if same_page and same_ncols and same_left:
            tables[-1].append(r)
        else:
            tables.append([r])
    return tables


def _rows_to_celltable(
    rows: list[list[Cell]], page_height: float
) -> CellTable | None:
    if len(rows) < 2 or any(len(r) < 2 for r in rows):
        return None
    n_cols = max(len(r) for r in rows)
    grid: list[list[str]] = [
        [c.text for c in r] + [""] * (n_cols - len(r))
        for r in rows
    ]
    cell_bboxes: list[list[BBox]] = [
        [c.bbox for c in r]
        + [r[-1].bbox] * (n_cols - len(r))     # padding bboxes for ragged rows
        for r in rows
    ]
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
        covered=set(),
        header_signature=tuple(grid[0]),
        page_height=page_height,
        nested=[],
        source=rows[0][0].source,
        bbox_style=rows[0][0].bbox_style,
        row_bboxes=row_bboxes,
    )


def aggregate(cells: list[Cell], page_height: float) -> list[CellTable]:
    """Group ``cells`` into tables. See module docstring."""
    if not cells:
        return []
    out: list[CellTable] = []
    for table_rows in _split_into_tables(_row_cluster(cells)):
        ct = _rows_to_celltable(table_rows, page_height)
        if ct is not None:
            out.append(ct)
    return out
