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

from dataclasses import dataclass

from pdf_parser.model import BBox
from pdf_parser.stages.detect_cells import Cell, CellSource


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


def aggregate(cells: list[Cell], page_height: float) -> list[CellTable]:
    """Group ``cells`` into tables. See module docstring."""
    if not cells:
        return []
    return []  # Tasks 3.x fill this in
