"""Experimental column-anchor table detector.

Runs **alongside** the legacy ``detect_tables`` cascade and contributes
additional table ``DocNode``s for cases the cascade misses — primarily
borderless tables whose average cell-text length exceeds the legacy
``_MAX_CELL_TEXT_CHARS = 7`` heuristic.

Topology
--------
The legacy cascade emits ``list[DocNode]`` (table subtrees) via
:func:`pdf_parser.stages.extract_tables.extract_tables`. This module exposes
:func:`augment_with_anchor_tables` which:

    1. Runs the anchor detector on every page of ``pdf_path``.
    2. Drops anchor candidates whose score is below ``MIN_SCORE``.
    3. Drops anchor candidates whose bbox overlaps any legacy table with
       IoU above ``IOU_DROP_THRESHOLD`` (on the same page).
    4. Converts each survivor into a flat ``table → row → cell`` DocNode
       subtree (no nested-table recursion — anchor candidates target the
       borderless flat-table case).
    5. Returns ``legacy_tables + new_tables`` in stable order.

When no anchor candidate survives the overlap/score filter, the return value
is identical to the input — so this is a no-op on the fixtures where the
legacy cascade is already correct.

Why not modify ``detect_tables.py``?
------------------------------------
A peer agent is actively editing ``detect_tables.py``. Augmenting at the
post-extraction layer means **zero shared edit surface** with their work —
the legacy cascade is consumed as a black box.

Tunables
--------
``MIN_SCORE``, ``IOU_DROP_THRESHOLD`` and the per-signal weights are
documented near the constants. They came out of the
``scripts/explore_anchor_detector.py`` calibration run against the synthetic
fixture corpus (see that script for the empirical separation between true
tables and multi-column prose).
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber

from pdf_parser.model import BBox, DocNode

# ---------------------------------------------------------------------------
# Tunables.
# ---------------------------------------------------------------------------

# Below this combined score a candidate is too uncertain to use.  Set just
# below the prose false-positive observed in calibration (0.54) and well
# above the worst real-table score (0.83) — leaves ~0.1 of headroom on
# both sides.
MIN_SCORE = 0.65

# An anchor candidate is dropped when most of its area is geometrically
# contained inside a same-page legacy table — i.e., it has rediscovered a
# sub-region of a table the legacy detector already emits.  The metric is
# intersection-over-anchor-area (NOT IoU): a small candidate inside a large
# legacy table is the exact case we want to reject, and IoU under-reports
# containment whenever the legacy table is much larger than the candidate.
# 0.5 means "more than half of the candidate's area sits inside a legacy
# table" — a clean "this is a sub-region" predicate.
CONTAINMENT_DROP_THRESHOLD = 0.50

# --- column-anchor detector internals ---

_LINE_GROUP_TOL = 2.0      # pt; y-bucket size for grouping words into lines
_GAP_THRESHOLD_PT = 8.0    # pt; horizontal gap above this splits a line
_ANCHOR_TOL_PT = 4.0       # pt; cell.x0 bucket = same column anchor
_MIN_RUN_LINES = 3         # min consecutive matching-signature lines
_MIN_COLS = 2              # signature must have ≥ this many cells

# Score-component weights (sum to 1.0 before the fill penalty).
_W_ROWS, _W_COLS, _W_STAB, _W_SPACING, _W_NUMERIC = 0.25, 0.20, 0.25, 0.15, 0.15

# Fill-penalty parameters: anti-signal for prose (text wrapping edge-to-edge
# fills its column). Knee at 0.65 = top of the observed table fill range
# from calibration; ramp ×5 reaches saturation at fill=0.85.
_FILL_KNEE = 0.65
_FILL_RAMP = 5.0
_W_FILL_PENALTY = 0.40

# Treat a cell as numeric only if it contains at least one digit.  Without
# the digit requirement, single tokens like ".", ",", "$", "()" would all
# count as numeric and inflate the score on punctuation-heavy rows.
_NUMERIC_RE = re.compile(r"^[\s\d.,$%()+\-/]*\d[\s\d.,$%()+\-/]*$")


# ---------------------------------------------------------------------------
# Candidate model — internal to this module; converted to DocNode on output.
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    page_index: int
    bbox: BBox
    grid: list[list[str]]
    cell_bboxes: list[list[BBox]]
    score: float
    signals: dict[str, float]


# ---------------------------------------------------------------------------
# Per-line cell extraction.
# ---------------------------------------------------------------------------

def _group_words_into_lines(words: list[dict]) -> list[list[dict]]:
    """y-bucket pdfplumber word dicts into lines."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for w in words:
        cy = (w["top"] + w["bottom"]) / 2
        if lines:
            last_cy = (lines[-1][0]["top"] + lines[-1][0]["bottom"]) / 2
            if abs(cy - last_cy) < _LINE_GROUP_TOL:
                lines[-1].append(w)
                continue
        lines.append([w])
    return lines


def _line_to_cells(line_words: list[dict]) -> list[tuple[float, float, str]]:
    """Gap-cluster a line into ``(x0, x1, text)`` triples."""
    if not line_words:
        return []
    sorted_words = sorted(line_words, key=lambda w: w["x0"])
    cells: list[list[dict]] = [[sorted_words[0]]]
    for w in sorted_words[1:]:
        prev_x1 = max(cw["x1"] for cw in cells[-1])
        if w["x0"] - prev_x1 > _GAP_THRESHOLD_PT:
            cells.append([w])
        else:
            cells[-1].append(w)
    return [
        (
            min(w["x0"] for w in c),
            max(w["x1"] for w in c),
            " ".join(w["text"] for w in c),
        )
        for c in cells
    ]


def _signature(cells: list[tuple[float, float, str]]) -> tuple[int, ...]:
    """Bucketed left-edge tuple — the column-alignment fingerprint of a line."""
    return tuple(int(round(c[0] / _ANCHOR_TOL_PT)) for c in cells)


# ---------------------------------------------------------------------------
# Per-candidate signal computation.
# ---------------------------------------------------------------------------

def _numeric_ratio(grid: list[list[str]]) -> float:
    cells = [c.strip() for row in grid for c in row if c.strip()]
    if not cells:
        return 0.0
    return sum(1 for c in cells if _NUMERIC_RE.match(c)) / len(cells)


def _is_list_shape(grid: list[list[str]]) -> bool:
    """Reject candidates that look like a bulleted or numbered list.

    Lists satisfy the column-anchor signature (a fixed glyph in column 1,
    prose in column 2) but are structurally text, not tables.  Heuristic:
    the first column collapses to a single unique stripped value across
    every row of the run.  Catches ``•``, ``(cid:127)``, ``*``, ``-``,
    ``▪``, etc. without enumerating glyphs.

    Real data tables almost never have a constant key column over ≥
    ``_MIN_RUN_LINES`` consecutive rows; when they do, the legacy
    border-aware detector handles them and this overlay is unnecessary.
    """
    if not grid or not grid[0]:
        return False
    first_col = {row[0].strip() for row in grid if row}
    return len(first_col) == 1


def _spacing_regularity(line_tops: list[float]) -> float:
    """``1 - CV`` of line-to-line gaps, clamped to ``[0, 1]``."""
    if len(line_tops) < 3:
        return 1.0
    gaps = [line_tops[i + 1] - line_tops[i] for i in range(len(line_tops) - 1)]
    mean = statistics.fmean(gaps)
    if mean <= 0:
        return 0.0
    cv = statistics.pstdev(gaps) / mean
    return max(0.0, min(1.0, 1.0 - cv))


def _anchor_stability(rows: list[list[tuple[float, float, str]]]) -> float:
    """Mean per-column std-dev of cell.x0 across rows, linearly mapped to ``[0, 1]``.

    0 pt drift → 1.0; ``_ANCHOR_TOL_PT`` drift → 0.0.
    """
    if len(rows) < 2:
        return 1.0
    n_cols = len(rows[0])
    drifts = [
        statistics.pstdev([row[c][0] for row in rows])
        for c in range(n_cols)
        if len(rows) >= 2
    ]
    if not drifts:
        return 1.0
    return max(0.0, min(1.0, 1.0 - statistics.fmean(drifts) / _ANCHOR_TOL_PT))


def _fill_ratio_vs_slots(rows: list[list[tuple[float, float, str]]]) -> float:
    """Average per-cell ``text_width / allocated_slot_width``.

    Allocated slot widths come from column anchors (gap between successive
    anchor x0s), **not** from observed text spans. That distinguishes
    "column is mostly whitespace with short text" (table) from "text wraps
    right to the next column boundary" (prose). The last column borrows the
    max observed ``x1`` of its rows as its right edge.
    """
    if not rows or len(rows[0]) < 2:
        return 0.0
    n_cols = len(rows[0])
    col_starts = [
        statistics.fmean(row[c][0] for row in rows) for c in range(n_cols)
    ]
    slot_widths = [col_starts[c + 1] - col_starts[c] for c in range(n_cols - 1)]
    slot_widths.append(max(row[-1][1] for row in rows) - col_starts[-1])
    fills: list[float] = []
    for row in rows:
        for c in range(n_cols):
            slot = slot_widths[c]
            if slot <= 0:
                continue
            fills.append(min(1.0, (row[c][1] - row[c][0]) / slot))
    return statistics.fmean(fills) if fills else 0.0


# ---------------------------------------------------------------------------
# Detector.
# ---------------------------------------------------------------------------

def _column_anchor_detector(
    page: pdfplumber.page.Page, page_index: int
) -> list[_Candidate]:
    """Find consecutive line runs sharing a column signature; emit a candidate per run."""
    words = page.extract_words(use_text_flow=True)
    lines = _group_words_into_lines(words)

    decorated: list[tuple[list[tuple[float, float, str]], Optional[tuple[int, ...]], float, float]] = []
    for line in lines:
        cells = _line_to_cells(line)
        sig = _signature(cells) if len(cells) >= _MIN_COLS else None
        top = min(w["top"] for w in line)
        bottom = max(w["bottom"] for w in line)
        decorated.append((cells, sig, top, bottom))

    candidates: list[_Candidate] = []
    i = 0
    while i < len(decorated):
        sig = decorated[i][1]
        if sig is None:
            i += 1
            continue
        j = i + 1
        while j < len(decorated) and decorated[j][1] == sig:
            j += 1
        run = decorated[i:j]
        i = j
        if len(run) < _MIN_RUN_LINES:
            continue

        cells_per_row_pre = [r[0] for r in run]
        grid_pre = [[text for (_, _, text) in row] for row in cells_per_row_pre]
        if _is_list_shape(grid_pre):
            # Bulleted / numbered lists satisfy the column-anchor signature
            # (constant glyph in column 1, prose in column 2) but are not
            # tables.  Reject before scoring so they cannot survive any
            # weight/threshold tuning downstream.
            continue

        cells_per_row = cells_per_row_pre
        grid = grid_pre
        line_tops = [r[2] for r in run]
        line_bots = [r[3] for r in run]

        n_rows, n_cols = len(run), len(sig)
        rows_norm = min(n_rows / 5.0, 1.0)
        cols_norm = min(n_cols / 3.0, 1.0)
        stab = _anchor_stability(cells_per_row)
        spacing = _spacing_regularity(line_tops)
        numeric = _numeric_ratio(grid)
        fill = _fill_ratio_vs_slots(cells_per_row)
        fill_penalty = max(0.0, min(1.0, (fill - _FILL_KNEE) * _FILL_RAMP))

        score_pre = (
            _W_ROWS * rows_norm
            + _W_COLS * cols_norm
            + _W_STAB * stab
            + _W_SPACING * spacing
            + _W_NUMERIC * numeric
        )
        score = max(0.0, score_pre - _W_FILL_PENALTY * fill_penalty)

        # Per-cell bboxes for this run (parallel to ``grid``). Each row spans
        # the run's line top→bottom for that line; the line's bottom is used
        # so the row bbox is tight to the visible glyphs.
        cell_bboxes: list[list[BBox]] = []
        for r_idx, row in enumerate(cells_per_row):
            line_top = line_tops[r_idx]
            line_bot = line_bots[r_idx]
            cell_bboxes.append([
                BBox(page=page_index, x0=cx0, y0=line_top, x1=cx1, y1=line_bot)
                for (cx0, cx1, _txt) in row
            ])

        xs0 = [c[0] for row in cells_per_row for c in row]
        xs1 = [c[1] for row in cells_per_row for c in row]

        candidates.append(_Candidate(
            page_index=page_index,
            bbox=BBox(page=page_index, x0=min(xs0), y0=min(line_tops),
                      x1=max(xs1), y1=max(line_bots)),
            grid=grid,
            cell_bboxes=cell_bboxes,
            score=round(score, 4),
            signals={
                "rows_norm": round(rows_norm, 3),
                "cols_norm": round(cols_norm, 3),
                "stability": round(stab, 3),
                "spacing": round(spacing, 3),
                "numeric": round(numeric, 3),
                "fill": round(fill, 3),
                "fill_penalty": round(fill_penalty, 3),
            },
        ))
    return candidates


# ---------------------------------------------------------------------------
# DocNode adapter.
# ---------------------------------------------------------------------------

def _candidate_to_docnode(c: _Candidate, page_height: float) -> DocNode:
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(c.grid):
        cell_nodes: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = c.cell_bboxes[r_idx][c_idx]
            cell_nodes.append(DocNode(
                kind="cell",
                bbox=cbox,
                text=text,
                attrs={},
                provenance={"extractor": "anchor", "stage": "detect_tables_anchor"},
            ))
        row_cells = c.cell_bboxes[r_idx]
        row_bbox = BBox(
            page=c.page_index,
            x0=min(b.x0 for b in row_cells),
            y0=min(b.y0 for b in row_cells),
            x1=max(b.x1 for b in row_cells),
            y1=max(b.y1 for b in row_cells),
        )
        rows.append(DocNode(
            kind="row",
            bbox=row_bbox,
            children=cell_nodes,
            attrs={"page": c.page_index, "row_index": r_idx},
            provenance={"extractor": "anchor", "stage": "detect_tables_anchor"},
        ))
    return DocNode(
        kind="table",
        bbox=c.bbox,
        children=rows,
        attrs={
            "n_rows": len(c.grid),
            "n_cols": len(c.grid[0]) if c.grid else 0,
            "header_signature": tuple(c.grid[0]) if c.grid else (),
            "page": c.page_index,
            "page_height": page_height,
            "anchor_score": c.score,
            "anchor_signals": c.signals,
        },
        provenance={"extractor": "anchor", "stage": "detect_tables_anchor"},
    )


# ---------------------------------------------------------------------------
# Overlap check.
# ---------------------------------------------------------------------------

def _containment_of_anchor(anchor: BBox, legacy: BBox) -> float:
    """Fraction of ``anchor``'s area that lies inside ``legacy``.

    Returns 0.0 when the boxes are on different pages or do not overlap.
    Use this — not IoU — when asking "is the anchor candidate a sub-region
    of the legacy table?".  IoU shrinks toward zero whenever the legacy
    box is much larger, masking exactly the case we want to reject.
    """
    if anchor.page != legacy.page:
        return 0.0
    ix0, iy0 = max(anchor.x0, legacy.x0), max(anchor.y0, legacy.y0)
    ix1, iy1 = min(anchor.x1, legacy.x1), min(anchor.y1, legacy.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    anchor_area = (anchor.x1 - anchor.x0) * (anchor.y1 - anchor.y0)
    return inter / anchor_area if anchor_area > 0 else 0.0


def _overlaps_legacy(c: _Candidate, legacy: list[DocNode]) -> bool:
    """Symmetric containment check between the anchor candidate and every legacy table.

    A candidate is dropped when **either** direction is majority-contained:

    * ``anchor ⊂ legacy`` — the anchor rediscovered a sub-region of a table
      the legacy detector already emits.  Without this check, every borderless
      sub-block inside a large legacy table (e.g. the OpEx rows inside a P&L
      statement) leaks through as a duplicate.
    * ``legacy ⊂ anchor`` — the anchor spans a region that fully encloses a
      known legacy table.  This is the borderless-outer + bordered-inner
      nesting case: legacy correctly emits the inner table; if the anchor
      candidate survives, the inner table's text gets flattened into one of
      the anchor's cells and ends up duplicated in the tree.

    Threshold is ``CONTAINMENT_DROP_THRESHOLD`` (intersection-over-smaller-area
    in each direction).
    """
    for lt in legacy:
        # Legacy table bbox may be either a single BBox or a list (page-spanning).
        boxes = lt.bbox if isinstance(lt.bbox, list) else [lt.bbox]
        for lb in boxes:
            if _containment_of_anchor(c.bbox, lb) > CONTAINMENT_DROP_THRESHOLD:
                return True
            if _containment_of_anchor(lb, c.bbox) > CONTAINMENT_DROP_THRESHOLD:
                return True
    return False


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------

def augment_with_anchor_tables(
    legacy_tables: list[DocNode],
    pdf_path: Path,
) -> list[DocNode]:
    """Run the anchor detector and append surviving candidates to ``legacy_tables``.

    A candidate survives iff:

    * ``candidate.score >= MIN_SCORE``
    * No legacy bbox is majority-contained in the candidate, and the
      candidate is not majority-contained in any legacy bbox (see
      :func:`_overlaps_legacy` for the symmetric check).

    Survivors are converted to ``table → row → cell`` DocNode subtrees and
    appended to the legacy list. Order: legacy tables first (in their
    original order), then anchor tables in document reading order
    (page-index, then y0).
    """
    new_tables: list[DocNode] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for idx, page in enumerate(pdf.pages):
            for c in _column_anchor_detector(page, idx):
                if c.score < MIN_SCORE:
                    continue
                if _overlaps_legacy(c, legacy_tables):
                    continue
                new_tables.append(_candidate_to_docnode(c, page_height=float(page.height)))

    if not new_tables:
        return legacy_tables

    # Preserve legacy order for legacy tables; append new tables sorted by
    # reading order (page, then y0). Caller is responsible for any further
    # ordering at the build_tree stage.
    def _anchor_sort_key(n: DocNode) -> tuple[int, float]:
        bb = n.bbox if not isinstance(n.bbox, list) else n.bbox[0]
        return (bb.page, bb.y0)

    new_tables.sort(key=_anchor_sort_key)
    return list(legacy_tables) + new_tables
