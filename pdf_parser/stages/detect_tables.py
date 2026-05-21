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
_FALLBACK_TABLE_SETTINGS = {
    "vertical_strategy":    "text",
    "horizontal_strategy":  "text",
    "snap_tolerance":       3,
    "join_tolerance":       3,
    "edge_min_length":      3,
    "min_words_vertical":   2,
    "min_words_horizontal": 1,
}

# Average cell-text length (characters) above which a text-strategy table is
# treated as paragraph text misidentified as a table and discarded.
#
# Empirically:
#   real data cells   (Name / Score / Grade, Alice / 95 / A)         avg ~3 chars
#   business headers  (Product Name / Unit Price, Widget A / $25.99)  avg ~7 chars
#   paragraph text    (pdfplumber text-strategy on body paragraphs)   avg ~20-25 chars
#
# 7 accepts typical data-table cell text (including business headers) while
# rejecting paragraphs detected as 13-column pseudo-tables and multi-column
# body-text layouts.  If real borderless tables with longer headers appear,
# adjust upward; if false positives reappear, adjust downward.
_MAX_CELL_TEXT_CHARS = 7


def _is_text_strategy_table(table) -> bool:
    """Return True if this text-strategy result looks like a real data table.

    Paragraph text on multi-column pages can be misidentified as tables by the
    text strategy.  The key discriminator: real data-table cells contain short
    text (names, numbers, codes); paragraph-text 'cells' contain full sentences.
    """
    texts = table.extract()
    if not texts:
        return False
    all_cells = [cell for row in texts for cell in (row or []) if cell and cell.strip()]
    if not all_cells:
        return False
    avg_len = sum(len(c) for c in all_cells) / len(all_cells)
    return avg_len <= _MAX_CELL_TEXT_CHARS


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

    # Drop rows where every cell is blank.  Applies to both line-strategy and
    # text-strategy tables: the text strategy can emit empty spacer rows, and
    # line-strategy tables may have them as artefacts.  Intentional blank
    # separator rows are not common in practice; revisit if they appear.
    keep = [i for i, row in enumerate(grid) if any(cell.strip() for cell in row)]
    grid = [grid[i] for i in keep]
    if not grid:
        return None

    # Build cell bboxes for all rows first
    cell_bboxes_raw: list[list[BBox]] = []
    for row in table.rows:
        cell_bboxes_raw.append([
            BBox(page=page_index, x0=c[0], y0=c[1], x1=c[2], y1=c[3]) if c is not None else
            BBox(page=page_index, x0=0, y0=0, x1=0, y1=0)
            for c in row.cells
        ])
    # Keep only the rows we retained.
    cell_bboxes = [cell_bboxes_raw[i] for i in keep]

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
    primary = {**DEFAULT_TABLE_SETTINGS, **(settings or {})}
    out: list[TableRegion] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages if region_bbox is None else [pdf.pages[region_bbox.page]]
        for page in pages:
            target = page
            if region_bbox is not None:
                target = page.crop(
                    (region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1)
                )
            page_height = float(page.height)
            page_idx   = page.page_number - 1

            found = target.find_tables(table_settings=primary)
            # If the line strategy found nothing and the caller did not supply
            # custom settings, retry with the text strategy.  This catches
            # tables that rely on whitespace alignment rather than vector borders
            # (Word exports, many financial PDFs).
            if not found and settings is None:
                fallback = target.find_tables(table_settings=_FALLBACK_TABLE_SETTINGS)
                found = [t for t in fallback if _is_text_strategy_table(t)]

            for t in found:
                region = _extract_region(target, t, page_idx, page_height)
                if region is not None:
                    out.append(region)
    return out
