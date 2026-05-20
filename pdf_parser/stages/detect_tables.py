"""Stage 3: pdfplumber-based table detection. Returns TableRegion list with cell grid."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber

from pdf_parser.model import BBox

DEFAULT_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "min_words_vertical": 2,
    "min_words_horizontal": 1,
}

MIN_TABLE_AREA = 100.0  # sq points; rejects two-span false-positives


@dataclass
class TableRegion:
    page_index: int
    bbox: BBox
    grid: list[list[str]]          # row-major text
    cell_bboxes: list[list[BBox]]  # parallel to grid
    page_height: float = 0.0       # original page height in points (for stitch proximity check)


def _cell_text(cells: list[list]) -> list[list[str]]:
    return [[(c if c is not None else "") for c in row] for row in cells]


def _extract_region(plumber_page, table, page_index: int, page_height: float = 0.0) -> Optional[TableRegion]:
    rows = table.extract()
    if not rows or len(rows) < 1:
        return None
    grid = _cell_text(rows)

    # table.rows returns Row objects; each Row has .cells (list of bbox tuples or None)
    cell_bboxes: list[list[BBox]] = []
    for row in table.rows:
        cell_bboxes.append([
            BBox(page=page_index, x0=c[0], y0=c[1], x1=c[2], y1=c[3]) if c is not None else
            BBox(page=page_index, x0=0, y0=0, x1=0, y1=0)
            for c in row.cells
        ])

    x0, y0, x1, y1 = table.bbox
    area = (x1 - x0) * (y1 - y0)
    if area < MIN_TABLE_AREA:
        return None
    return TableRegion(
        page_index=page_index,
        bbox=BBox(page=page_index, x0=x0, y0=y0, x1=x1, y1=y1),
        grid=grid,
        cell_bboxes=cell_bboxes,
        page_height=page_height,
    )


def detect_tables(
    pdf_path: Path,
    region_bbox: Optional[BBox] = None,
    settings: Optional[dict] = None,
) -> list[TableRegion]:
    settings = {**DEFAULT_TABLE_SETTINGS, **(settings or {})}
    out: list[TableRegion] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages if region_bbox is None else [pdf.pages[region_bbox.page]]
        for page in pages:
            target = page
            if region_bbox is not None:
                target = page.crop((region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1))
            for t in target.find_tables(table_settings=settings):
                region = _extract_region(target, t, page.page_number - 1, float(page.height))
                if region is not None:
                    out.append(region)
    return out
