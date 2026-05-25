"""Stage 3: pdfplumber-based table detection. Returns TableRegion list with cell grid."""

from __future__ import annotations

from dataclasses import dataclass, field
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

# A real data-table cell almost always starts with an uppercase letter, a
# digit, or a punctuation/symbol character (e.g. "$25.99", "(123)", "—").
# When pdfplumber's text-strategy slices through justified prose along
# vertical whitespace lanes, it produces fragments that begin **mid-word**:
# every wrapped line contributes cells like "tion", "or sit a", "ned future",
# all starting with lowercase letters that continue from the previous cell.
#
# Threshold tuned to:
#   * the worst real-table case (status/category columns with a handful of
#     lowercase values) tops out around 0.20.
#   * mid-word-split prose runs 0.50–0.75 in practice.
# 0.40 leaves margin on both sides.
_MAX_LOWERCASE_START_RATIO = 0.40


def _is_text_strategy_table(table) -> bool:
    """Return True if this text-strategy result looks like a real data table.

    Two complementary signals reject prose-mistaken-for-table:

    * **Average cell length** (``_MAX_CELL_TEXT_CHARS``).  Paragraph text on
      multi-column pages produces ``cells'' that are entire sentences.
    * **Lowercase-start ratio** (``_MAX_LOWERCASE_START_RATIO``).  When
      pdfplumber slices through wrapped justified prose along vertical
      whitespace lanes, the resulting fragments split mid-word — a real
      table cell starts at a word boundary (uppercase, digit, or symbol),
      a shredded prose cell starts with a lowercase letter that continues
      the previous cell.  Catches the case where average length sits just
      under the 7-char threshold but the cells are clearly fragments.
    """
    texts = table.extract()
    if not texts:
        return False
    all_cells = [cell for row in texts for cell in (row or []) if cell and cell.strip()]
    if not all_cells:
        return False
    avg_len = sum(len(c) for c in all_cells) / len(all_cells)
    if avg_len > _MAX_CELL_TEXT_CHARS:
        return False
    stripped = [c.strip() for c in all_cells]
    lowercase_starts = sum(1 for c in stripped if c[:1].islower())
    if lowercase_starts / len(stripped) > _MAX_LOWERCASE_START_RATIO:
        return False
    return True


@dataclass
class TableRegion:
    page_index: int
    bbox: BBox
    grid: list[list[str]]          # row-major text
    cell_bboxes: list[list[BBox]]  # parallel to grid
    page_height: float = 0.0       # original page height in points (for stitch proximity check)
    redistributed: bool = False    # set by `_redistribute_ruled_header_body`; skips logical-grid rebuild
    # Pre-detected nested sub-tables: cluster sub-tables extracted by
    # `_try_decompose_megatable` and attached to a synthesised outer frame so
    # the cell builder can splice them in directly, bypassing the recursive
    # `detect_tables(region_bbox=cell_bbox)` call that would crop away inner
    # sub-table edges flush with the outer frame.
    nested_regions: list["TableRegion"] = field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Visible-edge filtering.
#
# Some PDFs draw a full grid in black and then *overdraw* selected segments in
# the page background colour (typically white) to create a "visually merged"
# region without removing the underlying cells from the PDF data stream.
# pdfplumber's "lines" strategy is colour-blind: it sees both the black line
# and the white overdraw as independent edges, leaving the merged region
# fragmented into multiple rows or columns that don't exist visually.
#
# The fix is to subtract every background-coloured segment from the visible
# edge set, then drive ``find_tables`` with ``explicit`` strategies bound to
# the surviving segments.  When no background-coloured line is present the
# pre-pass is a no-op and the default ``lines`` strategy still runs.
# ---------------------------------------------------------------------------

_BG_COLOR_TOL = 0.95   # >= this in every channel (RGB/Grey) or <= 0.05 in CMYK = "background"
_LINE_SNAP_TOL = 1.0   # pt; group co-axial lines into the same "row of lines"
_AXIS_TOL = 0.5        # pt; line is horizontal if |y0-y1| < this, vertical if |x0-x1| < this


def _is_background_color(c) -> bool:
    """True if ``c`` is at or near the page background (default: near-white).

    Accepts the colour representations pdfplumber surfaces from PDF content
    streams: a single Grey float, a 3-tuple RGB, or a 4-tuple CMYK.  ``None``
    is treated as "not background" because the PDF spec defaults missing
    stroking colour to black, which is visible.
    """
    if c is None:
        return False
    if isinstance(c, (int, float)):
        return c >= _BG_COLOR_TOL
    if isinstance(c, (tuple, list)):
        if len(c) == 1:
            return c[0] >= _BG_COLOR_TOL
        if len(c) == 3:
            return all(ch >= _BG_COLOR_TOL for ch in c)
        if len(c) == 4:
            return all(ch <= 1.0 - _BG_COLOR_TOL for ch in c)  # CMYK: (0,0,0,0) = white
    return False


def _interval_subtract(
    base: list[tuple[float, float]], holes: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Return ``base \\ union(holes)`` as a sorted list of disjoint intervals.

    Both inputs may contain unsorted/overlapping intervals.  Holes are merged
    first, then carved out of each base interval in a single linear sweep.
    """
    if not holes:
        return [(min(a, b), max(a, b)) for a, b in base]
    sorted_holes = sorted((min(a, b), max(a, b)) for a, b in holes)
    merged_holes: list[tuple[float, float]] = []
    for h in sorted_holes:
        if merged_holes and h[0] <= merged_holes[-1][1]:
            merged_holes[-1] = (merged_holes[-1][0], max(merged_holes[-1][1], h[1]))
        else:
            merged_holes.append(h)
    out: list[tuple[float, float]] = []
    for a, b in base:
        a, b = min(a, b), max(a, b)
        cur = a
        for ha, hb in merged_holes:
            if hb <= cur:
                continue
            if ha >= b:
                break
            if ha > cur:
                out.append((cur, ha))
            cur = max(cur, hb)
            if cur >= b:
                break
        if cur < b:
            out.append((cur, b))
    return out


def _clip_line(src: dict, *, x0=None, x1=None, top=None, bottom=None) -> dict:
    """Clone a pdfplumber line dict, overriding endpoints and refreshing
    ``width``/``height``.  ``y0``/``y1`` (bottom-origin) are left untouched
    because pdfplumber's table engine only reads top/bottom/x0/x1/width/height.
    """
    d = dict(src)
    if x0 is not None:
        d["x0"] = x0
    if x1 is not None:
        d["x1"] = x1
    if top is not None:
        d["top"] = top
    if bottom is not None:
        d["bottom"] = bottom
    d["width"] = d["x1"] - d["x0"]
    d["height"] = d["bottom"] - d["top"]
    return d


def _visible_edges(page) -> tuple[list[dict], list[dict], bool]:
    """Return ``(h_lines, v_lines, had_overdraws)``.

    Groups each page line by its perpendicular coordinate (snapped to
    :data:`_LINE_SNAP_TOL`), then for every group computes
    ``union(visible) − union(background)`` along the line's axis.  When no
    background-coloured line exists anywhere on the page, returns
    ``([], [], False)`` so the caller can keep the default ``lines`` strategy.
    """
    h_raw = [ln for ln in page.lines if abs(ln["y0"] - ln["y1"]) < _AXIS_TOL]
    v_raw = [ln for ln in page.lines if abs(ln["x0"] - ln["x1"]) < _AXIS_TOL]
    had_overdraws = any(
        _is_background_color(ln.get("stroking_color")) for ln in h_raw + v_raw
    )
    if not had_overdraws:
        return [], [], False

    def collect(raw, key_fn, seg_fn, rebuild_fn):
        groups: dict[float, list[dict]] = {}
        for ln in raw:
            k = round(key_fn(ln) / _LINE_SNAP_TOL) * _LINE_SNAP_TOL
            groups.setdefault(k, []).append(ln)
        out: list[dict] = []
        for grp in groups.values():
            visible = [ln for ln in grp if not _is_background_color(ln.get("stroking_color"))]
            holes = [seg_fn(ln) for ln in grp if _is_background_color(ln.get("stroking_color"))]
            if not visible:
                continue
            spans = sorted(seg_fn(ln) for ln in visible)
            merged: list[tuple[float, float]] = []
            for s in spans:
                s_norm = (min(s), max(s))
                if merged and s_norm[0] <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(merged[-1][1], s_norm[1]))
                else:
                    merged.append(s_norm)
            segs = _interval_subtract(merged, holes)
            src = visible[0]
            for a, b in segs:
                out.append(rebuild_fn(src, a, b))
        return out

    horizontal = collect(
        h_raw,
        key_fn=lambda ln: ln["top"],
        seg_fn=lambda ln: (ln["x0"], ln["x1"]),
        rebuild_fn=lambda src, a, b: _clip_line(src, x0=a, x1=b),
    )
    vertical = collect(
        v_raw,
        key_fn=lambda ln: ln["x0"],
        seg_fn=lambda ln: (ln["top"], ln["bottom"]),
        rebuild_fn=lambda src, a, b: _clip_line(src, top=a, bottom=b),
    )
    return horizontal, vertical, True


def find_tables_visible(target, settings: Optional[dict] = None):
    """Find tables on ``target`` with background-coloured stroke overdraws
    subtracted from the edge set.

    Mirrors :func:`detect_tables`'s line-filtering policy so callers that need
    the raw pdfplumber ``Table`` objects (e.g. ``extract_tables`` matching
    bboxes for the logical-grid pass) see the same merged-cell view that
    ``detect_tables`` produced.  Falls back to the default ``lines`` strategy
    when no overdraws exist or the surviving segments are too sparse for
    ``find_tables`` (which requires ≥ 2 per axis under ``explicit``).
    """
    base = {**DEFAULT_TABLE_SETTINGS, **(settings or {})}
    if settings is None:
        h_vis, v_vis, had_overdraws = _visible_edges(target)
        if had_overdraws and len(h_vis) >= 2 and len(v_vis) >= 2:
            base = {
                **base,
                "vertical_strategy": "explicit",
                "horizontal_strategy": "explicit",
                "explicit_vertical_lines": v_vis,
                "explicit_horizontal_lines": h_vis,
            }
    return target.find_tables(table_settings=base)


# ---------------------------------------------------------------------------
# Borderless-frame detection.
#
# Many real-world documents draw a "section frame" — a rectangular outer box
# whose visible borders are two vertical side-rails plus tiny horizontal caps
# around an optional Header and/or Footer band.  The long middle stretch
# (body paragraphs, nested sub-tables) has NO internal horizontal grid lines.
# pdfplumber's line strategy needs intersecting horizontals + verticals, so
# these frames slip through detection entirely: only the inner sub-tables get
# found, the outer frame is lost, and any prose inside it surfaces as
# page-level paragraphs siblings of the would-be outer table.
#
# This pass scans for pairs of long vertical rails on each page, promotes
# each pair (plus its cap bands) to a single-column ``TableRegion`` with
# Header / Content / Footer rows.  The Content cell is left text-empty so
# the existing nested-detection in :mod:`extract_tables` can recurse, find
# inner sub-tables, and pull paragraphs that fall between them via
# :func:`_between_text_nodes`.  When the frame spans pages, the existing
# :mod:`stitch_pages` pass joins the per-page halves on matching
# single-column anchors — no special-casing required.
#
# Guards:
#   * Rails must be at least :data:`_FRAME_MIN_RAIL_LEN` tall, so column
#     dividers in multi-column body text don't qualify.
#   * A pair must enclose at least :data:`_FRAME_MIN_WIDTH` of x-space.
#   * At least one Header / Footer cap-band must exist, so unrelated parallel
#     rules (decorative sidebars, etc.) don't get promoted.
#   * Rails whose x-coordinates coincide with an already-detected table's
#     left/right sides are skipped — that table is the frame, not a missed
#     one.
#   * The pass runs ONLY at top-level (``region_bbox is None``) so recursive
#     nested-table detection inside the frame's own Content cell can't
#     re-discover the frame.
# ---------------------------------------------------------------------------

_FRAME_MIN_RAIL_LEN = 100.0   # pt; vertical rail must span at least this much
_FRAME_MIN_WIDTH    = 50.0    # pt; rails must be at least this far apart
_FRAME_X_TOL        = 1.0     # pt; cap endpoint snap to rail x
_FRAME_CAP_NEAR_END = 30.0    # pt; cap is "near" frame top/bottom if within this
_FRAME_SIDE_X_TOL   = 1.5     # pt; rail-x match against an existing-table side


def _is_full_width_cap(line: dict, lx: float, rx: float) -> bool:
    """A horizontal line that spans the full width of the candidate frame."""
    return (abs(line["x0"] - lx) <= _FRAME_X_TOL
            and abs(line["x1"] - rx) <= _FRAME_X_TOL)


def _band_text(page, lx: float, ty: float, rx: float, by: float) -> str:
    """Extract collapsed text from a rectangular band on the page."""
    if by <= ty or rx <= lx:
        return ""
    crop = page.crop((lx, ty, rx, by))
    return (crop.extract_text() or "").strip()


def _find_borderless_frames(
    page,
    page_index: int,
    page_height: float,
    existing: list["TableRegion"],
) -> list["TableRegion"]:
    """Promote vertical-rail-bounded section frames to single-column TableRegions."""
    # Sides of already-detected tables on this page; rails coinciding with
    # these belong to those tables, not to a missed frame.
    existing_sides: list[tuple[float, float]] = [
        (r.bbox.x0, r.bbox.x1) for r in existing if r.page_index == page_index
    ]

    def _is_existing_side_pair(lx: float, rx: float) -> bool:
        return any(
            abs(lx - sx0) <= _FRAME_SIDE_X_TOL and abs(rx - sx1) <= _FRAME_SIDE_X_TOL
            for sx0, sx1 in existing_sides
        )

    # Candidate rails: long, axis-aligned, visible.
    v_lines = [
        ln for ln in page.lines
        if abs(ln["x0"] - ln["x1"]) < _AXIS_TOL
        and (ln["bottom"] - ln["top"]) >= _FRAME_MIN_RAIL_LEN
        and not _is_background_color(ln.get("stroking_color"))
    ]
    if len(v_lines) < 2:
        return []

    # Bucket rails by x (snap to _FRAME_X_TOL); keep the longest per bucket.
    by_x: dict[float, dict] = {}
    for ln in v_lines:
        kx = round(ln["x0"] / _FRAME_X_TOL) * _FRAME_X_TOL
        cur = by_x.get(kx)
        if cur is None or (ln["bottom"] - ln["top"]) > (cur["bottom"] - cur["top"]):
            by_x[kx] = ln
    if len(by_x) < 2:
        return []

    # Visible horizontal lines for cap detection.
    h_lines = [
        ln for ln in page.lines
        if abs(ln["y0"] - ln["y1"]) < _AXIS_TOL
        and not _is_background_color(ln.get("stroking_color"))
    ]

    sorted_xs = sorted(by_x)
    frames: list[TableRegion] = []
    consumed: set[float] = set()
    for i, lx in enumerate(sorted_xs):
        if lx in consumed:
            continue
        left = by_x[lx]
        for rx in sorted_xs[i + 1:]:
            if rx in consumed or (rx - lx) < _FRAME_MIN_WIDTH:
                continue
            right = by_x[rx]
            y0 = max(left["top"], right["top"])
            y1 = min(left["bottom"], right["bottom"])
            if (y1 - y0) < _FRAME_MIN_RAIL_LEN:
                continue
            if _is_existing_side_pair(lx, rx):
                consumed.add(lx); consumed.add(rx)
                break

            # Cap bands: full-width horizontal lines anchored to this rail pair.
            caps_sorted = sorted(
                (ln["top"] for ln in h_lines if _is_full_width_cap(ln, lx, rx))
            )
            top_caps = [t for t in caps_sorted if (t - y0) <= _FRAME_CAP_NEAR_END]
            bot_caps = [t for t in caps_sorted if (y1 - t) <= _FRAME_CAP_NEAR_END]
            has_header = len(top_caps) >= 2
            has_footer = len(bot_caps) >= 2
            # A closed rectangle (single top border + single bottom border + two
            # side rails) is also a frame.  Distinct from header/footer bands:
            # there is no Header/Footer row to synthesise, just the wrapping
            # Content cell.  Without this case, a plain BOX-styled outer table
            # whose body holds inner sub-tables + free text gets dropped from
            # the parse tree entirely (pdfplumber can't see a 1×1 cell either).
            is_closed_rect = (
                not has_header
                and not has_footer
                and len(top_caps) >= 1
                and len(bot_caps) >= 1
            )
            if not (has_header or has_footer or is_closed_rect):
                continue

            # Synthesise rows: Header? Content Footer? — always ≥ 1 row.
            grid: list[list[str]] = []
            cell_bboxes: list[list[BBox]] = []
            content_top, content_bot = y0, y1
            if has_header:
                hdr_top, hdr_bot = top_caps[0], top_caps[1]
                grid.append([_band_text(page, lx, hdr_top, rx, hdr_bot)])
                cell_bboxes.append([
                    BBox(page=page_index, x0=lx, y0=hdr_top, x1=rx, y1=hdr_bot)
                ])
                content_top = hdr_bot
            if has_footer:
                ft_top, ft_bot = bot_caps[-2], bot_caps[-1]
                content_bot = ft_top
            # Content cell: text empty so _build_cell recurses into nested
            # sub-tables and pulls between-text paragraphs via the existing
            # extract_tables._between_text_nodes path.
            grid.append([""])
            cell_bboxes.append([
                BBox(page=page_index, x0=lx, y0=content_top, x1=rx, y1=content_bot)
            ])
            if has_footer:
                ft_top, ft_bot = bot_caps[-2], bot_caps[-1]
                grid.append([_band_text(page, lx, ft_top, rx, ft_bot)])
                cell_bboxes.append([
                    BBox(page=page_index, x0=lx, y0=ft_top, x1=rx, y1=ft_bot)
                ])

            frames.append(TableRegion(
                page_index=page_index,
                bbox=BBox(page=page_index, x0=lx, y0=y0, x1=rx, y1=y1),
                grid=grid,
                cell_bboxes=cell_bboxes,
                page_height=page_height,
                redistributed=True,  # synthetic grid; skip logical-grid rebuild
            ))
            consumed.add(lx); consumed.add(rx)
            break  # this left rail is now paired
    return frames


# ---------------------------------------------------------------------------
# Clustered-mega-table decomposition.
#
# When two sibling sub-tables nested in a single outer cell share their
# left/right vertical rails with the outer (and possibly with each other),
# pdfplumber's line strategy fuses every visible line into one giant grid
# and emits a single "mega-table" that interleaves the sub-tables' data
# rows with a tall single-column "gap row" holding the between-text.  The
# outer frame is lost, the two sub-tables are merged column-for-column,
# and any text between them is sliced along the inner column dividers.
#
# Typical real-world trigger: a multi-page outer table cut at a page
# boundary so that an inner sub-table sits flush against the top (or
# bottom) of the outer frame on the continuation (or non-final) page —
# both the outer's edge and the sub-table's edge end up at the same y.
#
# This pass detects the pattern via two signals:
#   * the region's bbox is a closed visible rectangle (full-height side
#     rails + full-width top/bottom horizontals);
#   * the region's rows split into ≥ 2 dense clusters separated by a
#     "gap row" whose height is several times the local median and whose
#     content occupies only one column.
#
# On a match it returns a single replacement TableRegion: an outer frame
# (1-row, 1-column, content-empty, `redistributed=True`) whose
# `nested_regions` carry the per-cluster sub-tables already extracted
# from the mega-table's grid.  The cell builder splices the nested
# regions in directly — bypassing the recursive `detect_tables` call
# that would crop the sub-tables' flush edges away.
# ---------------------------------------------------------------------------

_MEGATABLE_BOX_TOL          = 1.0   # pt; bbox-edge slack when matching closed-box lines
_MEGATABLE_GAP_HEIGHT_MULT  = 2.5   # gap row height must exceed this × median row height
_MEGATABLE_GAP_HEIGHT_MIN   = 20.0  # pt; absolute floor for a gap row height
_MEGATABLE_MIN_CLUSTER_ROWS = 2     # each side cluster must hold ≥ this many rows


def _has_closed_box(page, region: TableRegion) -> bool:
    """True if the page has 4 visible (non-background) lines forming the
    region's bounding rectangle: full-width top + bottom horizontals and
    full-height left + right verticals.
    """
    tol = _MEGATABLE_BOX_TOL
    x0, y0, x1, y1 = region.bbox.x0, region.bbox.y0, region.bbox.x1, region.bbox.y1

    def _visible_h_at(y: float) -> bool:
        for ln in page.lines:
            if _is_background_color(ln.get("stroking_color")):
                continue
            if abs(ln["top"] - y) > tol or abs(ln["bottom"] - y) > tol:
                continue
            if ln["x0"] <= x0 + tol and ln["x1"] >= x1 - tol:
                return True
        return False

    def _visible_v_at(x: float) -> bool:
        for ln in page.lines:
            if _is_background_color(ln.get("stroking_color")):
                continue
            if abs(ln["x0"] - x) > tol or abs(ln["x1"] - x) > tol:
                continue
            if ln["top"] <= y0 + tol and ln["bottom"] >= y1 - tol:
                return True
        return False

    return (_visible_h_at(y0) and _visible_h_at(y1)
            and _visible_v_at(x0) and _visible_v_at(x1))


def _row_height(row_bboxes: list[BBox]) -> float:
    """Height of a mega-table row.  All cells in a single row share the same
    y0/y1; pick the first non-zero-area cell to read it from.  Returns 0.0
    if every cell is degenerate (no valid bbox to draw from)."""
    for b in row_bboxes:
        if b.y1 > b.y0:
            return b.y1 - b.y0
    return 0.0


def _cluster_row_indices(
    cell_bboxes: list[list[BBox]], grid: list[list[str]]
) -> list[list[int]] | None:
    """Split row indices into clusters by detecting "gap rows" — rows whose
    height vastly exceeds the median and whose content is in a single
    column (a stretched cell holding between-tables paragraph text).

    Returns the cluster groupings (a list of row-index lists, one per
    cluster) when the split produces ≥ 2 clusters each holding
    ``_MEGATABLE_MIN_CLUSTER_ROWS`` rows or more.  Returns None
    otherwise.
    """
    heights = [_row_height(rb) for rb in cell_bboxes]
    valid_heights = [h for h in heights if h > 0]
    if not valid_heights:
        return None
    valid_sorted = sorted(valid_heights)
    median = valid_sorted[len(valid_sorted) // 2]
    if median <= 0:
        return None
    gap_threshold = max(_MEGATABLE_GAP_HEIGHT_MULT * median, _MEGATABLE_GAP_HEIGHT_MIN)

    gap_indices: list[int] = []
    for i, h in enumerate(heights):
        if h < gap_threshold:
            continue
        # A real "gap row" carries text in a single column at most — the
        # paragraph text between two sub-tables sits in whichever column
        # word-binning placed it.  A tall but multi-column row is a real
        # wrapped-prose data row and must NOT trigger decomposition.
        nonempty = sum(1 for cell in grid[i] if cell and cell.strip())
        if nonempty <= 1:
            gap_indices.append(i)

    if not gap_indices:
        return None

    clusters: list[list[int]] = []
    start = 0
    for g in gap_indices:
        if g > start:
            clusters.append(list(range(start, g)))
        start = g + 1
    if start < len(heights):
        clusters.append(list(range(start, len(heights))))

    clusters = [c for c in clusters if len(c) >= _MEGATABLE_MIN_CLUSTER_ROWS]
    if len(clusters) < 2:
        return None
    return clusters


def _cluster_subregion(
    region: TableRegion, row_indices: list[int]
) -> TableRegion:
    """Slice ``region`` to the rows in ``row_indices``, computing a tight
    bbox from those rows' cell bboxes."""
    sub_grid = [region.grid[i] for i in row_indices]
    sub_cell_bboxes = [region.cell_bboxes[i] for i in row_indices]

    xs: list[float] = []
    ys: list[float] = []
    for row in sub_cell_bboxes:
        for b in row:
            if b.y1 <= b.y0:
                continue
            xs.extend((b.x0, b.x1))
            ys.extend((b.y0, b.y1))
    if not xs or not ys:
        # Degenerate fallback: borrow the outer region's x-range and synthesise
        # a y-band from the row heights so the sub-region still has a valid bbox.
        cluster_bbox = BBox(
            page=region.bbox.page,
            x0=region.bbox.x0, y0=region.bbox.y0,
            x1=region.bbox.x1, y1=region.bbox.y1,
        )
    else:
        cluster_bbox = BBox(
            page=region.bbox.page,
            x0=min(xs), y0=min(ys),
            x1=max(xs), y1=max(ys),
        )

    return TableRegion(
        page_index=region.page_index,
        bbox=cluster_bbox,
        grid=sub_grid,
        cell_bboxes=sub_cell_bboxes,
        page_height=region.page_height,
        # The slice already has the correct logical grid; the ruled-header
        # redistribution pass would corrupt it by re-binning words against
        # a header row we have not validated.
        redistributed=True,
    )


def _try_decompose_megatable(page, region: TableRegion) -> TableRegion | None:
    """Recognise the "outer frame + 2+ flush sub-tables" mega-table pattern
    and return a replacement outer-frame TableRegion whose ``nested_regions``
    carry the cluster sub-tables.  Return None when the pattern does not
    match (region passed through to existing pipeline stages).
    """
    if len(region.grid) < 2 * _MEGATABLE_MIN_CLUSTER_ROWS + 1:
        # Too few rows to host two clusters plus a gap row — fast-reject.
        return None
    if not _has_closed_box(page, region):
        return None
    clusters = _cluster_row_indices(region.cell_bboxes, region.grid)
    if clusters is None:
        return None

    sub_regions = [_cluster_subregion(region, rows) for rows in clusters]

    # Outer frame: single-cell, single-row, content-empty.  The cell bbox
    # is the full mega-table bbox so `_between_text_nodes` can recover the
    # gap-row paragraph as a child of this cell.
    return TableRegion(
        page_index=region.page_index,
        bbox=region.bbox,
        grid=[[""]],
        cell_bboxes=[[region.bbox]],
        page_height=region.page_height,
        redistributed=True,
        nested_regions=sub_regions,
    )


# ---------------------------------------------------------------------------
# Ruled-header / open-body redistribution.
#
# Many real-world tables (financial reports, scientific papers, Word exports)
# draw cell borders only on the header row, leaving body rows free-form.  In
# the extreme case the header is the only row pdfplumber detects; more
# commonly, pdfplumber recovers the header plus a sequence of "merged" body
# rows where ``row.cells[0]`` spans the full header width and the remaining
# slots are ``None``.  Either way the body words are present on the page —
# they just need to be rebinned against the header's column x-bounds.
# ---------------------------------------------------------------------------

_MERGE_TOL = 1.0       # pt; bbox boundary slack when matching the merged-row pattern
_BIN_TOL = 0.5         # pt; column-bound slack when assigning a word
_LINE_GROUP_TOL = 2.0  # pt; words within this y-span are on the same line
_GAP_MULTIPLIER = 2.5  # multiplier on median line height; gap above this ends the scan
_MIN_GAP_PT = 6.0      # absolute floor for the gap threshold
_MIN_COLS_PER_BODY_ROW = 2  # accept a scanned line only if ≥ N columns are non-empty


def _is_merged_body_row(row_cells: list[BBox], header_x_range: tuple[float, float]) -> bool:
    """A 'merged' body row: first cell spans the full header width, all other
    cells are pdfplumber's empty-sentinel ``BBox(0,0,0,0)``.
    """
    if len(row_cells) < 2:
        return False
    first = row_cells[0]
    hx0, hx1 = header_x_range
    spans_full = first.x0 <= hx0 + _MERGE_TOL and first.x1 >= hx1 - _MERGE_TOL
    rest_empty = all(rc.x0 == 0 and rc.x1 == 0 for rc in row_cells[1:])
    return spans_full and rest_empty


def _bin_words_to_columns(
    words: list[dict], col_xs: list[tuple[float, float]]
) -> list[str]:
    """Bin words into header columns by word-center x.  Out-of-range words
    snap to the nearest column by center distance.
    """
    bins: list[list[tuple[float, str]]] = [[] for _ in col_xs]
    centers = [(cx0 + cx1) / 2 for cx0, cx1 in col_xs]
    for w in words:
        text = w.get("text", "").strip()
        if not text:
            continue
        cx = (w["x0"] + w["x1"]) / 2
        match = -1
        for i, (cx0, cx1) in enumerate(col_xs):
            if cx0 - _BIN_TOL <= cx <= cx1 + _BIN_TOL:
                match = i
                break
        if match < 0:
            match = min(range(len(centers)), key=lambda i: abs(centers[i] - cx))
        bins[match].append((w["x0"], text))
    return [" ".join(t for _, t in sorted(b)) for b in bins]


def _group_words_into_lines(words: list[dict]) -> list[list[dict]]:
    """Sort + bucket words into y-lines using :data:`_LINE_GROUP_TOL`."""
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


def _lines_to_rows(
    lines: list[list[dict]],
    col_xs: list[tuple[float, float]],
    page_idx: int,
    enforce_gap: bool,
) -> list[tuple[list[str], list[BBox]]]:
    """Convert y-grouped lines into ``(grid_row, cell_bboxes)`` pairs.

    When ``enforce_gap`` is true, stop at the first vertical gap >
    ``_GAP_MULTIPLIER × median line height`` or the first line with
    fewer than ``_MIN_COLS_PER_BODY_ROW`` non-empty columns.  Used by the
    header-only extension scan to avoid swallowing unrelated text below
    the table.  When false (within a confirmed merged cell), the cell's
    bbox already bounds the search, so every grouped line becomes a row.
    """
    if not lines:
        return []
    heights = sorted(
        (max(w["bottom"] for w in ln) - min(w["top"] for w in ln)) for ln in lines
    )
    median_h = heights[len(heights) // 2]
    max_gap = max(median_h * _GAP_MULTIPLIER, _MIN_GAP_PT)
    out: list[tuple[list[str], list[BBox]]] = []
    prev_bottom: Optional[float] = None
    for ln in lines:
        top = min(w["top"] for w in ln)
        bottom = max(w["bottom"] for w in ln)
        if enforce_gap and prev_bottom is not None and (top - prev_bottom) > max_gap:
            break
        binned = _bin_words_to_columns(ln, col_xs)
        if enforce_gap and sum(1 for b in binned if b.strip()) < _MIN_COLS_PER_BODY_ROW:
            break
        out.append((
            binned,
            [BBox(page=page_idx, x0=cx0, y0=top, x1=cx1, y1=bottom) for cx0, cx1 in col_xs],
        ))
        prev_bottom = bottom
    return out


def _scan_body_rows_below_header(
    plumber_page,
    header_x_range: tuple[float, float],
    header_y_bottom: float,
    col_xs: list[tuple[float, float]],
    page_idx: int,
) -> list[tuple[list[str], list[BBox]]]:
    """Header-only table extension: collect body rows in the strip below the
    header until a vertical gap or low-column-coverage line terminates the scan.
    """
    x0, x1 = header_x_range
    # Cropped recursive calls pass a sub-page whose bbox is tighter than the
    # parent.  When the header sits at (or below) the crop's bottom, the
    # strip below has zero or negative height and pdfplumber's crop()
    # raises — bail out instead of attempting an empty scan.
    page_bottom = plumber_page.bbox[3]
    if header_y_bottom >= page_bottom or x1 <= x0:
        return []
    crop = plumber_page.crop((x0, header_y_bottom, x1, page_bottom))
    words = crop.extract_words(use_text_flow=True)
    return _lines_to_rows(_group_words_into_lines(words), col_xs, page_idx, enforce_gap=True)


def _scan_body_rows_in_cell(
    plumber_page,
    cell_bbox: BBox,
    col_xs: list[tuple[float, float]],
    page_idx: int,
) -> list[tuple[list[str], list[BBox]]]:
    """Within a merged body cell, group words into per-line rows.  No gap
    enforcement: the cell's bbox already bounds the search.
    """
    crop = plumber_page.crop((cell_bbox.x0, cell_bbox.y0, cell_bbox.x1, cell_bbox.y1))
    words = crop.extract_words(use_text_flow=True)
    return _lines_to_rows(_group_words_into_lines(words), col_xs, page_idx, enforce_gap=False)


def _redistribute_ruled_header_body(plumber_page, region: TableRegion) -> TableRegion:
    """Rebuild a ``TableRegion`` whose body rows lack internal vertical
    separators.  No-op if the header has < 2 columns or the body is already
    populated cell-by-cell.
    """
    if not region.grid or len(region.cell_bboxes[0]) < 2:
        return region
    header_cells = region.cell_bboxes[0]
    col_xs = [(c.x0, c.x1) for c in header_cells]
    header_x_range = (col_xs[0][0], col_xs[-1][1])
    page_idx = region.page_index

    new_grid: list[list[str]] = [list(region.grid[0])]
    new_cells: list[list[BBox]] = [list(header_cells)]
    changed = False
    for ridx in range(1, len(region.grid)):
        row_cells = region.cell_bboxes[ridx]
        if not _is_merged_body_row(row_cells, header_x_range):
            new_grid.append(list(region.grid[ridx]))
            new_cells.append(list(row_cells))
            continue
        rows = _scan_body_rows_in_cell(plumber_page, row_cells[0], col_xs, page_idx)
        if not rows:
            new_grid.append(list(region.grid[ridx]))
            new_cells.append(list(row_cells))
            continue
        changed = True
        for g, b in rows:
            new_grid.append(g)
            new_cells.append(b)

    # Header-only table: try to extend downward.
    if len(new_grid) == 1:
        header_y_bottom = max(c.y1 for c in header_cells)
        extension = _scan_body_rows_below_header(
            plumber_page, header_x_range, header_y_bottom, col_xs, page_idx
        )
        if extension:
            changed = True
            for g, b in extension:
                new_grid.append(g)
                new_cells.append(b)

    if not changed:
        return region

    new_bbox = BBox(
        page=page_idx,
        x0=region.bbox.x0,
        y0=min(c.y0 for row in new_cells for c in row if c.x0 != 0 or c.x1 != 0),
        x1=region.bbox.x1,
        y1=max(c.y1 for row in new_cells for c in row if c.x0 != 0 or c.x1 != 0),
    )
    return TableRegion(
        page_index=page_idx,
        bbox=new_bbox,
        grid=new_grid,
        cell_bboxes=new_cells,
        page_height=region.page_height,
        redistributed=True,
    )


def detect_tables(
    pdf_path: Optional[Path] = None,
    region_bbox: Optional[BBox] = None,
    settings: Optional[dict] = None,
    *,
    pdf=None,
) -> list[TableRegion]:
    """Detect tables in ``pdf_path`` (or in an already-open ``pdf``).

    Passing ``pdf`` lets callers reuse a single ``pdfplumber.PDF`` across many
    invocations — critical for recursive nested-table detection, where opening
    the PDF afresh per cell otherwise dominates the runtime.  Exactly one of
    ``pdf_path`` or ``pdf`` must be provided.
    """
    if pdf is not None:
        return _detect_in_doc(pdf, region_bbox, settings)
    if pdf_path is None:
        raise TypeError("detect_tables requires either pdf_path or pdf=")
    with pdfplumber.open(str(pdf_path)) as opened:
        return _detect_in_doc(opened, region_bbox, settings)


def _detect_in_doc(
    pdf,
    region_bbox: Optional[BBox],
    settings: Optional[dict],
) -> list[TableRegion]:
    out: list[TableRegion] = []
    pages = pdf.pages if region_bbox is None else [pdf.pages[region_bbox.page]]
    for page in pages:
        target = page
        if region_bbox is not None:
            target = page.crop(
                (region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1)
            )
        page_height = float(page.height)
        page_idx   = page.page_number - 1

        found = find_tables_visible(target, settings)
        # If the line strategy found nothing and the caller did not supply
        # custom settings, retry with the text strategy.  This catches
        # tables that rely on whitespace alignment rather than vector borders
        # (Word exports, many financial PDFs).
        if not found and settings is None:
            fallback = target.find_tables(table_settings=_FALLBACK_TABLE_SETTINGS)
            found = [t for t in fallback if _is_text_strategy_table(t)]

        page_regions: list[TableRegion] = []
        for t in found:
            region = _extract_region(target, t, page_idx, page_height)
            if region is None:
                continue
            # Clustered-mega-table decomposition runs BEFORE ruled-header
            # redistribution: the latter rebuilds the grid by binning words
            # against the assumed header, destroying the cell_bboxes the
            # decomposer needs to spot the gap rows.
            decomposed = _try_decompose_megatable(target, region)
            if decomposed is not None:
                page_regions.append(decomposed)
                continue
            region = _redistribute_ruled_header_body(target, region)
            page_regions.append(region)

        # Borderless-frame pass: top-level only.  Recursive nested-table
        # detection (region_bbox != None) inside a frame's own Content cell
        # would otherwise re-discover the frame and infinitely nest it.
        if region_bbox is None and settings is None:
            page_regions.extend(
                _find_borderless_frames(target, page_idx, page_height, page_regions)
            )

        out.extend(page_regions)
    return out
