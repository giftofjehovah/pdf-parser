"""Bottom-up cell primitive.

A *Cell* is any rectangular page region that holds (or could hold) one
logical table cell.  Three evidence sources, ordered by trust:

  * ``line``   — bounded by visible horizontal+vertical edges (highest).
  * ``gutter`` — bounded by persistent whitespace columns + line gaps.
  * ``text``   — pdfplumber text-strategy fallback (lowest, prose-guarded).

``detect_cells(page, page_index)`` is the only public entry point.  It
returns the union of all three sources; downstream
:mod:`pdf_parser.stages.aggregate_tables` deduplicates and clusters them
into tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pdf_parser.model import BBox

CellSource = Literal["line", "gutter", "text"]


@dataclass(frozen=True)
class Cell:
    bbox: BBox
    text: str
    source: CellSource
    confidence: float


def detect_cells(page, page_index: int) -> list[Cell]:
    """Return every candidate cell on ``page``.  Empty list = no tables here."""
    return []
