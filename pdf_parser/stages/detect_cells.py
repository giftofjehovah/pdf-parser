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
# How the cell's bbox relates to its column neighbours.
#   * ``shared`` — col_i.x1 == col_{i+1}.x0 (pdfplumber text-strategy style).
#     Row bbox spans the full table x-extent; all rows share that bbox.
#   * ``tight``  — bbox tight to this cell's own word content (anchor-detector
#     style).  Row bbox is per-row (min/max over its non-empty cells).
# Drives row-bbox selection in ``extract_tables_v2._celltable_to_docnode``.
CellBboxStyle = Literal["shared", "tight"]


@dataclass(frozen=True)
class Cell:
    bbox: BBox
    text: str
    source: CellSource
    confidence: float
    bbox_style: CellBboxStyle = "shared"



def detect_cells(page, page_index: int) -> list[Cell]:
    """Return cells from the highest-confidence source(s) that find anything.

    Line-bounded and borderless-frame promotion are unioned: ``_frame_cells``
    augments the line output with synthesised outer-wrapper Cells on pages
    where pdfplumber's line strategy emits the inner cells but not the
    surrounding section frame (fixtures 17 / Annex D in 13_comprehensive).
    Per-bbox dedupe happens downstream in
    :func:`pdf_parser.stages.aggregate_tables._dedupe_cells`.

    When neither line nor frame fires, fall back to gutter-based detection,
    and finally to the pdfplumber text-strategy fallback (lowest confidence,
    prose-guarded).
    """
    line = _line_cells(page, page_index)
    frame = _frame_cells(page, page_index, line_cells=line)
    if line or frame:
        return line + frame
    gutter = _gutter_cells(page, page_index)
    if gutter:
        return gutter
    return _text_cells(page, page_index)


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

# ---------------------------------------------------------------------------
# Borderless-frame promotion (Phase-10 prep, Residual D).
#
# Ported from the legacy ``detect_tables._find_borderless_frames``: a pair of
# long vertical rails plus one or two horizontal cap bands defines a "section
# frame" -- the outer wrapper of a multi-page or flush-edge table whose body
# has no internal horizontal grid lines.  pdfplumber's line strategy needs
# intersecting H+V edges, so these frames slip through detection entirely and
# the inner sub-tables surface as page-level siblings instead of nesting.
#
# This pass synthesises ``Cell`` records (not a whole table) so the carve in
# :func:`pdf_parser.stages.aggregate_tables._carve_container_frames` plus
# :func:`pdf_parser.stages.aggregate_tables._build_single_col_wrapper` build
# the 1xN outer wrapper downstream and ``stitch_pages`` joins per-page halves
# on matching column anchors -- the same plug-in point that already supports
# the line-detected wrapper of fixture 16 / Annex C.
#
# Gates (cheapest first):
#   * Outermost rail pair MUST have no internal tall rail between it.  Internal
#     tall rails are column dividers (e.g. fixtures 03/06/07/26), not frame
#     side-rails; pairing them would synthesise a spurious wrapper around an
#     ordinary multi-column table.
#   * No existing line cell may already span the rail pair -- pdfplumber's
#     line strategy already emitted the wrapper (fixtures 16, 19, 20) so a
#     duplicate would either lose data on dedupe or pollute the row cluster.
#   * At least one header / footer cap-band MUST exist (``has_header`` or
#     ``has_footer``).  Pure closed_rect (one top + one bot cap) cannot form
#     a multi-row wrapper from a single content cell -- documented Phase-10+
#     residual for fixtures 22 / 25 (legacy reaches them via the megatable
#     decomposition pass which is not in this port).
#   * Resulting cell list must contain >=2 entries after zero-height bands
#     are dropped, so ``_build_single_col_wrapper`` (len(rows) >= 2) accepts
#     the candidate.
# ---------------------------------------------------------------------------

_FRAME_MIN_RAIL_LEN  = 100.0  # pt; vertical rail must span at least this much
_FRAME_MIN_WIDTH     = 50.0   # pt; rails must be at least this far apart
_FRAME_X_TOL         = 1.0    # pt; cap endpoint snap to rail x
_FRAME_CAP_NEAR_END  = 30.0   # pt; cap is "near" frame top/bottom if within this
_FRAME_SIDE_X_TOL    = 1.5    # pt; rail-x match against an existing line cell


def _is_full_width_cap(ln, lx: float, rx: float, tol: float = _FRAME_X_TOL) -> bool:
    return abs(ln["x0"] - lx) <= tol and abs(ln["x1"] - rx) <= tol


def _band_text(page, lx: float, ty: float, rx: float, by: float) -> str:
    """Collapsed text from a rectangular band on the page."""
    if by <= ty or rx <= lx:
        return ""
    crop = page.crop((lx, ty, rx, by))
    return (crop.extract_text() or "").strip()


def _frame_cells(
    page, page_index: int, line_cells: list[Cell] | None = None
) -> list[Cell]:
    """Synthesise outer-frame Cells from vertical rails + horizontal caps.

    Recovers wrapper cells for layouts where pdfplumber's line strategy
    cannot intersect the side rails with cap H-lines (fixtures 17 / Annex D
    in 13_comprehensive).  Each surviving frame emits up to three Cells
    stacked vertically -- header band, content (empty container), footer
    band -- which feed the existing 1xN wrapper builder in
    :mod:`aggregate_tables` without requiring any aggregate-stage change.

    ``line_cells`` is the existing line-detector output for this page; pass
    it explicitly to avoid re-running ``_line_cells`` when the caller has
    already computed it.  When ``None`` the helper recomputes it (used by
    unit tests that drive ``_frame_cells`` directly).
    """
    if line_cells is None:
        line_cells = _line_cells(page, page_index)

    # Candidate vertical rails: axis-aligned, long enough, not background.
    v_lines = [
        ln for ln in page.lines
        if abs(ln["x0"] - ln["x1"]) < _LINE_AXIS_TOL
        and (ln["bottom"] - ln["top"]) >= _FRAME_MIN_RAIL_LEN
        and not _is_background_color(ln.get("stroking_color"))
    ]
    if len(v_lines) < 2:
        return []

    # Bucket rails by x (snap to ``_FRAME_X_TOL``); keep the longest per bucket.
    by_x: dict[float, dict] = {}
    for ln in v_lines:
        kx = round(ln["x0"] / _FRAME_X_TOL) * _FRAME_X_TOL
        cur = by_x.get(kx)
        if cur is None or (ln["bottom"] - ln["top"]) > (cur["bottom"] - cur["top"]):
            by_x[kx] = ln
    sorted_xs = sorted(by_x)
    if len(sorted_xs) < 2:
        return []

    # Internal tall rails between the outermost pair indicate column dividers,
    # not a frame.  Reject -- the outermost pair is then NOT a section frame.
    lx, rx = sorted_xs[0], sorted_xs[-1]
    if any(lx < x < rx for x in sorted_xs):
        return []
    if (rx - lx) < _FRAME_MIN_WIDTH:
        return []

    left, right = by_x[lx], by_x[rx]
    y0 = max(left["top"], right["top"])
    y1 = min(left["bottom"], right["bottom"])
    if (y1 - y0) < _FRAME_MIN_RAIL_LEN:
        return []

    # If an existing line cell already spans this rail pair, the wrapper was
    # already emitted by pdfplumber's line strategy -- skip to avoid a
    # duplicate that would either lose text on dedupe or split the row cluster.
    if any(
        abs(lx - c.bbox.x0) <= _FRAME_SIDE_X_TOL
        and abs(rx - c.bbox.x1) <= _FRAME_SIDE_X_TOL
        for c in line_cells
    ):
        return []

    # Horizontal cap detection: full-width H-lines anchored to this rail pair.
    h_lines = [
        ln for ln in page.lines
        if abs(ln["y0"] - ln["y1"]) < _LINE_AXIS_TOL
        and not _is_background_color(ln.get("stroking_color"))
    ]
    cap_tops = sorted(
        ln["top"] for ln in h_lines if _is_full_width_cap(ln, lx, rx)
    )
    top_caps = [t for t in cap_tops if (t - y0) <= _FRAME_CAP_NEAR_END]
    bot_caps = [t for t in cap_tops if (y1 - t) <= _FRAME_CAP_NEAR_END]
    has_header = len(top_caps) >= 2
    has_footer = len(bot_caps) >= 2
    if not (has_header or has_footer):
        # Pure closed_rect (1 top + 1 bot cap) cannot form a multi-row
        # wrapper from a single content cell.  Documented Phase-10+
        # residual: fixtures 22 / 25 still xfail in parity.
        return []

    # Synthesise: [header band?] + content (empty) + [footer band?].
    out: list[Cell] = []
    content_top, content_bot = y0, y1
    if has_header:
        hdr_top, hdr_bot = top_caps[0], top_caps[1]
        if hdr_bot > hdr_top:
            out.append(Cell(
                bbox=BBox(page=page_index, x0=lx, y0=hdr_top, x1=rx, y1=hdr_bot),
                text=_band_text(page, lx, hdr_top, rx, hdr_bot),
                source="line",
                confidence=1.0,
            ))
        content_top = hdr_bot
    if has_footer:
        ft_top, ft_bot = bot_caps[-2], bot_caps[-1]
        content_bot = ft_top
    if content_bot > content_top:
        out.append(Cell(
            bbox=BBox(page=page_index, x0=lx, y0=content_top, x1=rx, y1=content_bot),
            text="",
            source="line",
            confidence=1.0,
        ))
    if has_footer:
        ft_top, ft_bot = bot_caps[-2], bot_caps[-1]
        if ft_bot > ft_top:
            out.append(Cell(
                bbox=BBox(page=page_index, x0=lx, y0=ft_top, x1=rx, y1=ft_bot),
                text=_band_text(page, lx, ft_top, rx, ft_bot),
                source="line",
                confidence=1.0,
            ))

    # Need >=2 cells for ``_build_single_col_wrapper`` to accept the candidate.
    if len(out) < 2:
        return []
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
    """Inter-word gaps of at least ``min_gap_pt`` wide as ``(x0, x1)`` intervals."""
    gaps: list[tuple[float, float]] = []
    for prev, cur in zip(words, words[1:]):
        if cur["x0"] - prev["x1"] >= min_gap_pt:
            gaps.append((prev["x1"], cur["x0"]))
    return gaps


def _intervals_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return not (a[1] < b[0] or b[1] < a[0])


def _find_column_gutters(
    lines: list[list[dict]],
    min_run: int = _GUTTER_MIN_RUN_LINES,
    min_gap_pt: float = _GUTTER_MIN_GAP_PT,
) -> list[tuple[float, float]]:
    """Return (x0, x1) gutter intervals that persist across ≥ ``min_run`` lines.

    Each reported interval is the INTERSECTION of every overlapping per-line
    gap in the run — i.e. the x-range that is whitespace on every consecutive
    line.  Using the first line's gap (or the union) over-narrows or
    over-widens the column on tables with ragged right edges (e.g.
    14b_borderless_long_text, where col1's right edge swings from 312pt on the
    header row to 364pt on a data row): binning words into the first-line
    range silently drops "renewal", "delayed", etc. from their cells.
    """
    if len(lines) < min_run:
        return []
    gaps_per_line = [_line_gaps(ln, min_gap_pt) for ln in lines]
    out: list[tuple[float, float]] = []
    seen: list[tuple[float, float]] = []
    for i, gaps_i in enumerate(gaps_per_line):
        for g in gaps_i:
            if any(_intervals_overlap(g, s) for s in seen):
                continue
            current = g
            run = 1
            for gaps_j in gaps_per_line[i + 1:]:
                # Pick the line's gap with widest intersection with ``current``.
                best, best_w = None, -1.0
                for gj in gaps_j:
                    if not _intervals_overlap(current, gj):
                        continue
                    w = min(current[1], gj[1]) - max(current[0], gj[0])
                    if w > best_w:
                        best_w, best = w, gj
                if best is None:
                    break
                current = (max(current[0], best[0]), min(current[1], best[1]))
                run += 1
            if run >= min_run:
                out.append(current)
                seen.append(current)
    return sorted(out)

_GUTTER_CONFIDENCE  = 0.7
_GUTTER_LINE_TOL    = 2.0
# Bounds within this distance collapse to one entry; gutter ranges within
# this distance of a candidate column are recognised as the gutter itself
# (i.e. dropped, not emitted as a sliver column).
_GUTTER_MATCH_TOL   = 0.5
# Words whose x-midpoint sits within this slack of a column edge bin into
# that column.  Cheap robustness against PDF coordinate drift.
_BIN_MIDPOINT_TOL   = 0.5


def _column_ranges_from_gutters(
    page_x0: float, page_x1: float, gutters: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Convert gutters → list of column x-ranges spanning [page_x0, page_x1].

    Bounds are merged within ``_GUTTER_MATCH_TOL`` so FP drift in the source
    PDF (e.g. ``199.23000000000002``) collapses to one boundary instead of
    emitting an epsilon-wide sliver column. Adjacent gutters whose facing
    edges sit within the same tolerance collapse the same way, so a gap of
    ``0.3pt`` between two near-touching gutters does not become its own
    column either.
    """
    if not gutters:
        return [(page_x0, page_x1)]
    raw = [page_x0]
    for g in gutters:
        raw.extend(g)
    raw.append(page_x1)
    raw.sort()
    bounds: list[float] = [raw[0]]
    for v in raw[1:]:
        if v - bounds[-1] > _GUTTER_MATCH_TOL:
            bounds.append(v)
    cols: list[tuple[float, float]] = []
    for i in range(len(bounds) - 1):
        a, b = bounds[i], bounds[i + 1]
        # If (a, b) lines up with a gutter, drop it — the column is the
        # whitespace between two real columns, not a column itself.
        if any(abs(a - g[0]) < _GUTTER_MATCH_TOL and abs(b - g[1]) < _GUTTER_MATCH_TOL
               for g in gutters):
            continue
        cols.append((a, b))
    return cols


def _bin_words_per_col(
    words: list[dict], cols: list[tuple[float, float]]
) -> list[list[dict]]:
    """Bin words into columns by x-midpoint; returns word lists per column.

    Joined-text view: ``" ".join(w["text"] for w in sorted(bin, key=x0))``.
    """
    bins: list[list[dict]] = [[] for _ in cols]
    for w in words:
        xmid = (w["x0"] + w["x1"]) / 2.0
        for i, (cx0, cx1) in enumerate(cols):
            if cx0 - _BIN_MIDPOINT_TOL <= xmid <= cx1 + _BIN_MIDPOINT_TOL:
                bins[i].append(w)
                break
    for b in bins:
        b.sort(key=lambda w: w["x0"])
    return bins


def _bin_to_text(bin_words: list[dict]) -> str:
    return " ".join(w["text"] for w in bin_words)


def _bin_words_to_columns(
    words: list[dict], cols: list[tuple[float, float]]
) -> list[str]:
    """Joined-text view of :func:`_bin_words_per_col`."""
    return [_bin_to_text(b) for b in _bin_words_per_col(words, cols)]

# Tuned narrower than the legacy text-strategy guard because line-bounded
# detection already absorbs most real tables; gutter only fires on borderless
# layouts where false positives (multi-column prose) are the dominant risk.
_GUTTER_MIN_CELL_COUNT             = 4    # below this, not enough signal for a table
_GUTTER_MAX_AVG_CELL_CHARS         = 30   # avg cell length must stay ≤ this
_GUTTER_MAX_LOWERCASE_START_RATIO  = 0.40 # ≤40% of cells may start lowercase
# At or below this avg cell length the column-anchor / pdfplumber text-strategy
# convention is used: cells share column boundaries (col_i.x1 == col_{i+1}.x0)
# and all rows take the full table bbox.  Above it, ``tight`` per-cell bboxes
# match the anchor-detector convention.  7 = the empirically calibrated knee
# in ``detect_tables._MAX_CELL_TEXT_CHARS`` — short data cells vs longer
# borderless descriptions — and preserves byte-identical id parity with the
# two legacy producers (text-strategy + anchor) the bottom-up path replaces.
_SHARED_LANE_AVG_CHARS_MAX = 7


def _is_gutter_table_shape(grid: list[list[str]]) -> bool:
    """True when ``grid`` looks like a table, not a slice of body prose.

    Three predicates: ≥ ``_GUTTER_MIN_CELL_COUNT`` non-empty cells, average
    cell length ≤ ``_GUTTER_MAX_AVG_CELL_CHARS``, lowercase-start ratio
    ≤ ``_GUTTER_MAX_LOWERCASE_START_RATIO``.  Empty / whitespace-only cells
    are filtered internally, so callers may pass either pre-filtered grids
    or raw rows with empty placeholders.
    """
    cells = [c.strip() for row in grid for c in row if c.strip()]
    if len(cells) < _GUTTER_MIN_CELL_COUNT:
        return False
    avg_len = sum(len(c) for c in cells) / len(cells)
    if avg_len > _GUTTER_MAX_AVG_CELL_CHARS:
        return False
    lowercase_starts = sum(1 for c in cells if c[:1].islower())
    if lowercase_starts / len(cells) > _GUTTER_MAX_LOWERCASE_START_RATIO:
        return False
    return True


def _longest_signature_run(
    row_bins: list[list[list[dict]]],
) -> tuple[int, int]:
    """Return ``(start, end)`` half-open slice for the longest consecutive run
    of lines sharing the same populated-column signature.

    Filters out non-table lines (page headings, section labels) that bin into
    a different column count than the dominant body — analogous to the
    consecutive-run logic in ``detect_tables_anchor._column_anchor_detector``.
    """
    if not row_bins:
        return (0, 0)
    sigs = [tuple(bool(b) for b in row) for row in row_bins]
    best_start, best_end = 0, 0
    cur_start = 0
    for i in range(1, len(sigs)):
        if sigs[i] != sigs[cur_start]:
            if i - cur_start > best_end - best_start:
                best_start, best_end = cur_start, i
            cur_start = i
    if len(sigs) - cur_start > best_end - best_start:
        best_start, best_end = cur_start, len(sigs)
    return best_start, best_end


def _gutter_cells(page, page_index: int) -> list[Cell]:
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    if not words:
        return []
    lines = _group_words_into_lines(words, tol=_GUTTER_LINE_TOL)
    if len(lines) < _GUTTER_MIN_RUN_LINES:
        return []
    gutters = _find_column_gutters(lines)
    if not gutters:
        return []
    page_x0 = float(page.bbox[0])
    page_x1 = float(page.bbox[2])
    cols = _column_ranges_from_gutters(page_x0, page_x1, gutters)
    if len(cols) < 2:
        return []

    # Per-line per-column word bins, dropping all-empty lines.
    row_bins: list[list[list[dict]]] = []
    for ln in lines:
        bins = _bin_words_per_col(ln, cols)
        if any(bins):
            row_bins.append(bins)

    # Keep only the longest consecutive same-signature run so page-level
    # headings ("Long-Cell Table" above 14b's body) don't pollute the table.
    s, e = _longest_signature_run(row_bins)
    row_bins = row_bins[s:e]
    if len(row_bins) < _GUTTER_MIN_RUN_LINES:
        return []

    # Prose guard runs on the joined-text grid view.
    candidate_rows = [[_bin_to_text(b) for b in row] for row in row_bins]
    if not _is_gutter_table_shape(candidate_rows):
        return []

    # Decide bbox convention from the candidate's avg cell text length.
    non_empty = [c.strip() for row in candidate_rows for c in row if c.strip()]
    avg_len = sum(len(c) for c in non_empty) / len(non_empty)
    style: CellBboxStyle = "shared" if avg_len <= _SHARED_LANE_AVG_CHARS_MAX else "tight"

    # For shared style: per-column lane edges from word content, with adjacent
    # columns sharing boundaries (col_i.x1 == col_{i+1}.x0) — matches the
    # pdfplumber text-strategy lane geometry the legacy ``extract_tables``
    # emits on 14_borderless_table.
    if style == "shared":
        lane_x0: list[float | None] = []
        lane_x1: list[float | None] = []
        for i in range(len(cols)):
            col_words = [w for row in row_bins for w in row[i]]
            if col_words:
                lane_x0.append(min(w["x0"] for w in col_words))
                lane_x1.append(max(w["x1"] for w in col_words))
            else:
                lane_x0.append(None)
                lane_x1.append(None)
        # Interior boundary collapses to the next column's left edge.
        for i in range(len(cols) - 1):
            if lane_x0[i + 1] is not None:
                lane_x1[i] = lane_x0[i + 1]

    out: list[Cell] = []
    for row in row_bins:
        line_words = [w for col in row for w in col]
        if not line_words:
            continue
        y0 = min(w["top"] for w in line_words)
        y1 = max(w["bottom"] for w in line_words)
        for col_idx, bin_words in enumerate(row):
            if not bin_words:
                continue
            if style == "shared":
                cx0 = lane_x0[col_idx]
                cx1 = lane_x1[col_idx]
                if cx0 is None or cx1 is None:
                    continue
            else:
                cx0 = min(w["x0"] for w in bin_words)
                cx1 = max(w["x1"] for w in bin_words)
            out.append(Cell(
                bbox=BBox(page=page_index, x0=cx0, y0=y0, x1=cx1, y1=y1),
                text=_bin_to_text(bin_words).strip(),
                source="gutter",
                confidence=_GUTTER_CONFIDENCE,
                bbox_style=style,
            ))
    return out

# ---------------------------------------------------------------------------
# Text-strategy cell detection (lowest-confidence fallback).
#
# pdfplumber's text-strategy table finding uses vertical/horizontal whitespace
# lanes to segment words; tuned here for lowest-confidence prose-guarded
# fallback when neither line-bounded nor gutter-based detection fires.
# ---------------------------------------------------------------------------

_TEXT_FALLBACK_SETTINGS = {
    "vertical_strategy":    "text",
    "horizontal_strategy":  "text",
    "snap_tolerance":       3,
    "join_tolerance":       3,
    "edge_min_length":      3,
    "min_words_vertical":   2,
    "min_words_horizontal": 1,
}
_TEXT_CELL_CONFIDENCE = 0.4


def _text_cells(page, page_index: int) -> list[Cell]:
    """Return cells detected via pdfplumber's text-strategy table finding,
    guarded by _is_gutter_table_shape to reject multi-column prose.
    """
    tables = page.find_tables(table_settings=_TEXT_FALLBACK_SETTINGS)
    out: list[Cell] = []
    for t in tables:
        rows = t.extract()
        if not _is_gutter_table_shape([[c.strip() for c in row if c] for row in rows]):
            continue
        for r_idx, row in enumerate(t.rows):
            for c_idx, cbox in enumerate(row.cells):
                if cbox is None:
                    continue
                x0, y0, x1, y1 = cbox
                txt = (rows[r_idx][c_idx] or "").strip() if r_idx < len(rows) else ""
                out.append(Cell(
                    bbox=BBox(page=page_index, x0=x0, y0=y0, x1=x1, y1=y1),
                    text=txt,
                    source="text",
                    confidence=_TEXT_CELL_CONFIDENCE,
                ))
    return out
