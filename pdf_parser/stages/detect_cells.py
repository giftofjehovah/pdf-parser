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
    cells: list[Cell] = []
    cells.extend(_line_cells(page, page_index))
    return cells


# ---------------------------------------------------------------------------
# Line-bounded cells: pdfplumber's line strategy + visible-edge overdraw
# filtering (background-coloured strokes subtracted).  The overdraw helper is
# currently a thin wrapper around ``detect_tables._visible_edges``; Phase 10
# inlines a port here so the bottom-up path stands alone.
#
# ``_LINE_AXIS_TOL``, ``_LINE_SNAP_TOL`` and ``_is_background_color`` are
# reserved for that Phase-10 inline (the inlined ``_visible_edges`` port uses
# them); kept here so the constant/helper set is stable across the cutover.
# ---------------------------------------------------------------------------

_LINE_AXIS_TOL  = 0.5
_LINE_SNAP_TOL  = 1.0
_BG_COLOR_TOL   = 0.95
_MIN_CELL_AREA  = 1.0

_DEFAULT_TABLE_SETTINGS = {
    "vertical_strategy":    "lines",
    "horizontal_strategy":  "lines",
    "snap_tolerance":       3,
    "join_tolerance":       3,
    "edge_min_length":      3,
    "min_words_vertical":   1,
    "min_words_horizontal": 1,
}


def _is_background_color(c) -> bool:
    """True if ``c`` is at or near the page background (default: near-white)."""
    if c is None:
        return False
    try:
        seq = tuple(c) if not isinstance(c, (int, float)) else (float(c),)
    except TypeError:
        return False
    if len(seq) in (1, 3):
        return all(v >= _BG_COLOR_TOL for v in seq)
    if len(seq) == 4:  # CMYK
        return all(v <= (1.0 - _BG_COLOR_TOL) for v in seq)
    return False


def _visible_edges_local(page):
    """Return ``(h_lines, v_lines)`` with background-coloured overdraws removed.

    Thin wrapper around ``detect_tables._visible_edges``.  Phase 10 inlines
    a port here so the bottom-up path stands alone; see that module for the
    design notes in the meantime.
    """
    from pdf_parser.stages.detect_tables import _visible_edges  # Phase 10 inlines this
    h, v, _ = _visible_edges(page)
    return h, v


def _line_cells(page, page_index: int) -> list[Cell]:
    settings = dict(_DEFAULT_TABLE_SETTINGS)
    h_vis, v_vis = _visible_edges_local(page)
    if len(h_vis) >= 2 and len(v_vis) >= 2:
        settings.update(
            vertical_strategy="explicit",
            horizontal_strategy="explicit",
            explicit_vertical_lines=v_vis,
            explicit_horizontal_lines=h_vis,
        )
    tables = page.find_tables(table_settings=settings)
    out: list[Cell] = []
    for t in tables:
        rows = t.extract()
        for r_idx, row in enumerate(t.rows):
            for c_idx, cbox in enumerate(row.cells):
                if cbox is None:
                    continue
                x0, y0, x1, y1 = cbox
                if (x1 - x0) * (y1 - y0) < _MIN_CELL_AREA:
                    continue
                text = (rows[r_idx][c_idx] if r_idx < len(rows)
                                              and c_idx < len(rows[r_idx])
                                              and rows[r_idx][c_idx] is not None
                                              else "")
                out.append(Cell(
                    bbox=BBox(page=page_index, x0=x0, y0=y0, x1=x1, y1=y1),
                    text=text.strip(),
                    source="line",
                    confidence=1.0,
                ))
    return out



def _word_ymid(w: dict) -> float:
    return (w["top"] + w["bottom"]) / 2.0


def _group_words_into_lines(words: list[dict], tol: float = 2.0) -> list[list[dict]]:
    """y-bucket pdfplumber word dicts into visual text lines.

    Bucketing rule: a word joins the current line when its y-midpoint is
    within ``tol`` of the line's running-average y-midpoint.  When it falls
    outside, a new line opens and the running average resets to that word's
    y-midpoint (NOT the average of the new word and the previous line — that
    would silently mis-bucket the next word in the new line if it sits within
    ``tol`` of the new line but outside the contaminated midpoint).

    Sort key uses (ymid, x0) so ties on y-midpoint (very common — same-line
    words usually share top/bottom) compare on x0 rather than falling through
    to a dict comparison (which raises ``TypeError``).
    """
    if not words:
        return []
    by_y = sorted(words, key=lambda w: (_word_ymid(w), w["x0"]))
    lines: list[list[dict]] = [[by_y[0]]]
    cur_y = _word_ymid(by_y[0])
    for w in by_y[1:]:
        ymid = _word_ymid(w)
        if abs(ymid - cur_y) <= tol:
            lines[-1].append(w)
            cur_y = (cur_y + ymid) / 2.0
        else:
            lines.append([w])
            cur_y = ymid
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
    return lines

# ---------------------------------------------------------------------------
# Whitespace-gutter cell detection.
#
# Vertical gutters that persist across ≥ ``min_run`` consecutive text lines
# define column boundaries.  Algorithm:
#
#   1. For every line, compute the list of horizontal gaps between adjacent
#      words wider than ``min_gap_pt``.
#   2. Project each gap as an x-interval.  Intersect intervals across lines.
#   3. Persistent (≥ min_run) intersections define columns.
#
# Spiritual replacement for ``detect_tables_anchor._column_anchor_detector``;
# outputs cells, not whole tables.
# ---------------------------------------------------------------------------

_GUTTER_MIN_RUN_LINES = 3
_GUTTER_MIN_GAP_PT    = 8.0


def _line_gaps(words: list[dict], min_gap_pt: float) -> list[tuple[float, float]]:
    """Inter-word gaps wider than ``min_gap_pt`` as ``(x0, x1)`` intervals."""
    gaps: list[tuple[float, float]] = []
    for prev, cur in zip(words, words[1:]):
        if cur["x0"] - prev["x1"] >= min_gap_pt:
            gaps.append((prev["x1"], cur["x0"]))
    return gaps


def _find_column_gutters(
    lines: list[list[dict]],
    min_run: int = _GUTTER_MIN_RUN_LINES,
    min_gap_pt: float = _GUTTER_MIN_GAP_PT,
) -> list[tuple[float, float]]:
    """Return (x0, x1) gutter intervals that persist across ≥ ``min_run`` lines."""
    if len(lines) < min_run:
        return []
    # Per-line gap interval lists; we accumulate "support counts" per x-bucket.
    line_gaps = [_line_gaps(ln, min_gap_pt) for ln in lines]
    # Walk overlapping intervals: for each gap on the first line, see how many
    # consecutive following lines also have an overlapping gap.
    out: list[tuple[float, float]] = []
    seen: list[tuple[float, float]] = []
    for i, gaps_i in enumerate(line_gaps):
        for g in gaps_i:
            if any(_intervals_overlap(g, s) for s in seen):
                continue
            run = 1
            for gaps_j in line_gaps[i + 1:]:
                if any(_intervals_overlap(g, gj) for gj in gaps_j):
                    run += 1
                else:
                    break
            if run >= min_run:
                out.append(g)
                seen.append(g)
    return sorted(out)


def _intervals_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return not (a[1] < b[0] or b[1] < a[0])
