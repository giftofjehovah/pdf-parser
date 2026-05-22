"""Stage 4: build DocNode subtree per TableRegion; recurse into cells for nested tables."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pdfplumber

from pdf_parser.model import BBox, MAX_DEPTH, DocNode
from pdf_parser.stages.detect_tables import TableRegion, detect_tables, find_tables_visible

_OVERLAP_TOL = 2.0  # points; guards against sub-pixel boundary mismatches


def _cell_align(page_chars: list[dict], cbox: BBox) -> str:
    """Return 'right' if cell text is right-aligned, 'left' otherwise.

    Compares the gap between the text's left edge and the cell's left edge
    against the gap between the text's right edge and the cell's right edge.
    A text block that sits much closer to the right wall is right-aligned.
    """
    chars = [
        c for c in page_chars
        if c.get("x0", 0) >= cbox.x0 - 1
        and c.get("x1", 0) <= cbox.x1 + 1
        and c.get("top", 0) >= cbox.y0 - 1
        and c.get("bottom", 0) <= cbox.y1 + 1
        and c.get("text", "").strip()
    ]
    if not chars:
        return "left"
    cell_w = cbox.x1 - cbox.x0
    if cell_w < 2:
        return "left"
    text_x0 = min(c["x0"] for c in chars)
    text_x1 = max(c["x1"] for c in chars)
    left_gap = text_x0 - cbox.x0
    right_gap = cbox.x1 - text_x1
    # Right-aligned: text sits markedly closer to the right wall.
    # Threshold: right gap < 30 % of the left gap AND < 6 pt absolute.
    if right_gap < left_gap * 0.30 and right_gap < 6.0:
        return "right"
    return "left"


def _page_y(page_height: float, pdf_y: float) -> float:
    """Convert PDF y (bottom-origin) to pdfplumber page y (top-origin)."""
    return page_height - pdf_y


def _outer_line_ys(page, table_bbox: tuple[float, float, float, float]) -> list[float]:
    """Return sorted page-space y-values of horizontal lines that mark row boundaries.

    Accepts any line that lies within the table's x-range AND y-range and spans
    at least 50 % of the table width.  The relaxed width threshold (vs. the
    original 100 %) handles row-spanning cells: a row-dividing line is suppressed
    only for the spanned column but still drawn across the remaining columns,
    producing a partial-width segment (≥ 50 % when at most half the columns are
    merged).

    The y-range filter is critical: without it, horizontal lines from unrelated
    tables elsewhere on the same page can be included as row boundaries, producing
    phantom extra rows whose cell bboxes extend into the unrelated table's region.
    """
    table_x0, table_y0, table_x1, table_y1 = table_bbox
    table_width = table_x1 - table_x0
    page_height = page.height
    ys: set[float] = set()
    for line in page.lines:
        if abs(line["y0"] - line["y1"]) < 1:  # horizontal
            # Must overlap with the table's y-range (top-origin pdfplumber coords).
            ln_top = min(line["top"], line["bottom"])
            if ln_top < table_y0 - _OVERLAP_TOL or ln_top > table_y1 + _OVERLAP_TOL:
                continue
            line_width = line["x1"] - line["x0"]
            in_x = (line["x0"] >= table_x0 - _OVERLAP_TOL
                    and line["x1"] <= table_x1 + _OVERLAP_TOL)
            if in_x and line_width >= 0.50 * table_width:
                ys.add(round(_page_y(page_height, line["y0"]), 1))
    return sorted(ys)


def _outer_col_xs(raw_header_row: list) -> list[tuple[float, float]]:
    """Return (x0, x1) for each logical outer column, derived from non-None header cells.

    Used as a fallback when the table lacks full-height vertical borders.
    Vulnerable to nested-table interference if the header row itself is split
    by inner-table vertical edges; prefer :func:`_outer_col_xs_from_lines`.
    """
    return [(cell[0], cell[2]) for cell in raw_header_row if cell is not None]


def _outer_col_xs_from_lines(
    page, table_bbox: tuple[float, float, float, float]
) -> list[tuple[float, float]]:
    """Return (x0, x1) for each outer column, derived from tall vertical lines.

    Nested sub-tables draw vertical edges confined to a single cell, so they
    span only a fraction of the table height.  The ``≥ 70 %`` threshold keeps
    those out while still capturing outer column boundaries that are suppressed
    in a colspan header row (typically 1 out of N rows, leaving (N-1)/N ≥ 80 %
    for N ≥ 5; the threshold was originally 95 % which broke on any colspan).

    Lines are pre-filtered to those whose x lies within the table's x-range and
    whose y-span overlaps the table's y-range.  Without both filters,
    vertical lines from unrelated tables elsewhere on the same page can satisfy
    the height threshold (because the merged-cells table may be very short) and
    produce spurious columns.
    """
    table_x0, table_y0, table_x1, table_y1 = table_bbox
    table_height = table_y1 - table_y0
    if table_height <= 0:
        return []
    xs: set[float] = set()
    for ln in page.lines:
        if abs(ln["x0"] - ln["x1"]) < 1:  # vertical line
            ln_x = round(ln["x0"], 1)
            # Must lie within the table's x-range.
            if ln_x < table_x0 - _OVERLAP_TOL or ln_x > table_x1 + _OVERLAP_TOL:
                continue
            # Must overlap with the table's y-range (top-origin pdfplumber coords).
            ln_top = min(ln["top"], ln["bottom"])
            ln_bot = max(ln["top"], ln["bottom"])
            if ln_bot < table_y0 - _OVERLAP_TOL or ln_top > table_y1 + _OVERLAP_TOL:
                continue
            length = abs(ln["y1"] - ln["y0"])
            if length >= 0.70 * table_height:
                xs.add(ln_x)
    xs_sorted = sorted(xs)
    if len(xs_sorted) < 2:
        return []
    return list(zip(xs_sorted[:-1], xs_sorted[1:]))


def _logical_grid_from_table(
    page, t, page_index: int
) -> Optional[tuple[list[list[str]], list[list[BBox]], set[tuple[int, int]]]]:
    """
    Reconstruct the logical (merged-cell) grid for a pdfplumber table.

    Returns (logical_grid, logical_cell_bboxes, covered) where *covered* is the
    set of (row_idx, col_idx) positions that are spanned over by an earlier cell
    (colspan or rowspan).  Returns None if no outer-line structure is found.
    """
    raw_rows = [r.cells for r in t.rows]
    texts = t.extract()
    if not texts:
        return None

    outer_ys = _outer_line_ys(page, t.bbox)
    if len(outer_ys) < 2:
        return None

    # Prefer full-height vertical lines: robust when the first/last row contains
    # a nested sub-table that would otherwise pollute the header-cell positions.
    col_xs = _outer_col_xs_from_lines(page, t.bbox)
    if not col_xs:
        col_xs = _outer_col_xs(raw_rows[0])
    # A colspan row that spans the full table width suppresses all inner vertical
    # edges for that row.  When the fragment is short (e.g. the top portion of a
    # split merged-cells table), inner dividers only appear in the remaining rows
    # and may not reach the 70 % height threshold.  Fall back to the raw row with
    # the most non-None cells so we still recover the correct column structure.
    if len(col_xs) < 2:
        for row in raw_rows:
            cand = _outer_col_xs(row)
            if len(cand) > len(col_xs):
                col_xs = cand
    if not col_xs:
        return None

    row_boundaries = list(zip(outer_ys[:-1], outer_ys[1:]))
    logical_grid: list[list[str]] = []
    logical_cell_bboxes: list[list[BBox]] = []
    covered: set[tuple[int, int]] = set()

    for r_idx, (row_y0, row_y1) in enumerate(row_boundaries):
        row_texts: list[str] = []
        row_bboxes: list[BBox] = []
        for c_idx, (col_x0, col_x1) in enumerate(col_xs):
            if (r_idx, c_idx) in covered:
                row_texts.append("")
                row_bboxes.append(BBox(
                    page=page_index, x0=col_x0, y0=row_y0, x1=col_x1, y1=row_y1
                ))
                continue

            # Collect text from all raw sub-cells that START in this logical cell.
            # Using the top-left corner (cx0, cy0) rather than the centre avoids
            # merged cells being assigned to multiple logical cells: a colspan/rowspan
            # cell's centre lands exactly on a boundary, but its top-left corner is
            # unambiguously inside the first logical cell of the span.
            #
            # Symmetric tolerance: [boundary - tol, boundary + tol] for the lower
            # edge (handles sub-pixel rounding where cy0 is slightly below row_y0)
            # and [boundary - tol, next_boundary - tol] for the upper edge (prevents
            # a cell whose start drifted to within tol of the next row from matching
            # two rows simultaneously).  In practice pdfplumber coordinates are exact
            # to within 1-2 pt, so tol=2.0 is sufficient.
            cell_texts: list[str] = []
            primary_raw: tuple[float, float, float, float] | None = None
            for ri, rrow in enumerate(raw_rows):
                for ci, cell in enumerate(rrow):
                    if cell is None:
                        continue
                    cx0, cy0, cx1, cy1 = cell
                    in_x = col_x0 - _OVERLAP_TOL <= cx0 <= col_x1 - _OVERLAP_TOL
                    in_y = row_y0 - _OVERLAP_TOL <= cy0 <= row_y1 - _OVERLAP_TOL
                    if in_x and in_y:
                        t_val = texts[ri][ci]
                        if t_val:
                            cell_texts.append(t_val)
                        if primary_raw is None:
                            primary_raw = (cx0, cy0, cx1, cy1)

            text = " ".join(cell_texts)

            # Detect colspan / rowspan from the primary raw cell's extent.
            actual_x1 = col_x1
            actual_y1 = row_y1
            if primary_raw is not None and text:
                _, _, raw_x1, raw_y1 = primary_raw
                # Colspan: raw cell extends beyond this column's right edge.
                if raw_x1 > col_x1 + _OVERLAP_TOL:
                    actual_x1 = raw_x1
                    for nc in range(c_idx + 1, len(col_xs)):
                        if col_xs[nc][0] < raw_x1 - _OVERLAP_TOL:
                            covered.add((r_idx, nc))
                # Rowspan: raw cell extends below this row's bottom edge.
                if raw_y1 > row_y1 + _OVERLAP_TOL:
                    actual_y1 = raw_y1
                    for nr in range(r_idx + 1, len(row_boundaries)):
                        if row_boundaries[nr][0] < raw_y1 - _OVERLAP_TOL:
                            covered.add((nr, c_idx))

            row_texts.append(text)
            row_bboxes.append(BBox(
                page=page_index, x0=col_x0, y0=row_y0, x1=actual_x1, y1=actual_y1
            ))
        logical_grid.append(row_texts)
        logical_cell_bboxes.append(row_bboxes)

    return logical_grid, logical_cell_bboxes, covered


# Bullets we recognise as list-item leads.  Mirrors LIST_BULLETS in segment.py
# but adds (cid:127), which pdfplumber emits when a font's CID-to-Unicode map
# is missing for the disc-bullet character (typical for ReportLab output).
_CELL_LIST_BULLETS = ("•", "-", "*", "◦", "▪", "o", "(cid:127)")


def _between_text_nodes(
    page_chars: list[dict],
    cell_bbox: BBox,
    nested_bboxes: list[BBox],
    tol: float = 2.0,
) -> list[DocNode]:
    """Return paragraph DocNodes for text in *cell_bbox* that falls outside all *nested_bboxes*.

    When a cell contains multiple nested sub-tables, text that lives between
    those sub-tables (in the cell's y-range but not inside any sub-table's bbox)
    is extracted here and returned as paragraph nodes, so callers can add them
    as siblings to the sub-table nodes in the correct vertical order.
    """
    # Include all chars in cell (using c.get("text") to keep explicit space glyphs
    # which pdfplumber may emit; they are included here and handled below).
    cell_chars = [
        c for c in page_chars
        if (c.get("x0", 0) >= cell_bbox.x0 - tol
            and c.get("x1", 0) <= cell_bbox.x1 + tol
            and c.get("top", 0) >= cell_bbox.y0 - tol
            and c.get("bottom", 0) <= cell_bbox.y1 + tol
            and c.get("text"))
    ]
    # Remove chars that fall inside any nested table.
    outside: list[dict] = [
        c for c in cell_chars
        if not any(
            c.get("x0", 0) >= nb.x0 - tol
            and c.get("x1", 0) <= nb.x1 + tol
            and c.get("top", 0) >= nb.y0 - tol
            and c.get("bottom", 0) <= nb.y1 + tol
            for nb in nested_bboxes
        )
    ]
    if not outside:
        return []
    # Group into visual lines by y-midpoint bucket (2-pt rounding matches segment.py).
    lines: dict[int, list[dict]] = {}
    for c in outside:
        cy_mid = (c.get("top", 0) + c.get("bottom", 0)) / 2
        lines.setdefault(round(cy_mid / 2), []).append(c)
    nodes: list[DocNode] = []
    for bucket in sorted(lines):
        lc = sorted(lines[bucket], key=lambda c: c.get("x0", 0))
        # Reconstruct inter-word spaces from horizontal gaps between chars.
        # Many PDFs encode spaces as positioning adjustments (not explicit space
        # glyphs), so a naive join produces run-together text.  We insert a
        # synthetic space whenever the gap between the previous char's x1 and
        # the current char's x0 exceeds 40 % of the average char width.
        non_space = [c for c in lc if c.get("text", "").strip()]
        if not non_space:
            continue
        avg_w = sum(c.get("x1", 0) - c.get("x0", 0) for c in non_space) / len(non_space)
        space_gap = max(avg_w * 0.4, 1.0)
        parts: list[str] = []
        prev_x1: float | None = None
        for c in lc:
            char_text = c.get("text", "")
            if not char_text.strip():
                continue  # explicit space glyph: handled via gap detection below
            x0 = c.get("x0", 0)
            if prev_x1 is not None and x0 - prev_x1 > space_gap:
                parts.append(" ")
            parts.append(char_text)
            prev_x1 = c.get("x1", x0 + 1)
        line_text = "".join(parts).strip()
        if not line_text:
            continue
        line_bbox = BBox(
            page=cell_bbox.page,
            x0=min(c.get("x0", 0) for c in lc),
            y0=min(c.get("top", 0) for c in lc),
            x1=max(c.get("x1", 0) for c in lc),
            y1=max(c.get("bottom", 0) for c in lc),
        )
        # Detect bullet leads.  A line beginning with a bullet glyph (or
        # the (cid:127) artifact for unmapped disc-bullet fonts) is a
        # list_item; we also normalise the bullet to "•" so downstream
        # rendering doesn't show the raw CID.
        stripped = line_text.lstrip()
        is_list_item = stripped.startswith(_CELL_LIST_BULLETS)
        if is_list_item and stripped.startswith("(cid:127)"):
            line_text = "• " + stripped[len("(cid:127)"):].lstrip()
        nodes.append(DocNode(
            kind="list_item" if is_list_item else "paragraph",
            bbox=line_bbox,
            text=line_text,
            provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
        ))
    return _join_wrapped_cell_lines(_absorb_dangling_bullets_in_cell(nodes))


def _absorb_dangling_bullets_in_cell(nodes: list[DocNode]) -> list[DocNode]:
    """Merge lone bullet-glyph list_items into the adjacent paragraph.

    pdfplumber's char extraction sometimes places a bullet glyph at a y-center
    that doesn't fall in the same 2 pt bucket as its companion text (the glyph
    is taller and sits a couple of points above the baseline).  The bullet
    then surfaces as its own ``list_item`` with only the bullet character,
    while the actual bullet text becomes a sibling ``paragraph`` immediately
    before or after it.  Without absorbing them here, the downstream wrapped-
    line joiner happily merges the *following* line into the lone bullet,
    swallowing the next item's lead text.

    Mirrors :func:`pdf_parser.stages.segment._absorb_dangling_bullets`.
    """
    if not nodes:
        return nodes

    def _is_lone_bullet(n: DocNode) -> bool:
        if n.kind != "list_item":
            return False
        t = (n.text or "").strip()
        return t in {"•", "o", "*", "-", "◦", "▪"}

    def _bbox(n: DocNode) -> BBox:
        return n.bbox if isinstance(n.bbox, BBox) else n.bbox[0]

    Y_TOL = 12.0  # ~one body line height

    out: list[DocNode] = []
    i = 0
    while i < len(nodes):
        n = nodes[i]
        if _is_lone_bullet(n):
            nb = _bbox(n)
            ny = (nb.y0 + nb.y1) / 2

            def _eligible(cand: DocNode | None) -> tuple[float, BBox] | None:
                if cand is None or cand.kind != "paragraph":
                    return None
                cb = _bbox(cand)
                cy = (cb.y0 + cb.y1) / 2
                dy = abs(cy - ny)
                if dy <= Y_TOL and cb.x0 > nb.x0:
                    return (dy, cb)
                return None

            prev_cand = out[-1] if out else None
            next_cand = nodes[i + 1] if i + 1 < len(nodes) else None
            prev_score = _eligible(prev_cand)
            next_score = _eligible(next_cand)

            # Pick whichever paragraph is closest in y to the bullet glyph.
            # Bullets typically sit a couple of pt above (or below) the baseline
            # of their companion text, so the smallest y-distance is the
            # correct pairing.  Case A (forward) and Case B (backward) both
            # appear in practice; only y-distance reliably disambiguates.
            pick = None
            if prev_score and next_score:
                pick = "prev" if prev_score[0] <= next_score[0] else "next"
            elif prev_score:
                pick = "prev"
            elif next_score:
                pick = "next"

            if pick == "next":
                assert next_cand is not None and next_score is not None
                xb = next_score[1]
                merged_bbox = BBox(
                    page=nb.page,
                    x0=nb.x0,
                    y0=min(nb.y0, xb.y0),
                    x1=max(nb.x1, xb.x1),
                    y1=max(nb.y1, xb.y1),
                )
                out.append(DocNode(
                    kind="list_item",
                    bbox=merged_bbox,
                    text=(n.text or "").strip() + " " + (next_cand.text or ""),
                    provenance=n.provenance,
                ))
                i += 2
                continue
            if pick == "prev":
                assert prev_cand is not None and prev_score is not None
                pb = prev_score[1]
                merged_bbox = BBox(
                    page=nb.page,
                    x0=nb.x0,
                    y0=min(nb.y0, pb.y0),
                    x1=max(nb.x1, pb.x1),
                    y1=max(nb.y1, pb.y1),
                )
                out[-1] = DocNode(
                    kind="list_item",
                    bbox=merged_bbox,
                    text=(n.text or "").strip() + " " + (prev_cand.text or ""),
                    provenance=prev_cand.provenance,
                )
                i += 1
                continue
        out.append(n)
        i += 1
    return out


def _join_wrapped_cell_lines(nodes: list[DocNode]) -> list[DocNode]:
    """Merge per-line paragraph nodes into the preceding paragraph or list_item.

    Mirrors :func:`pdf_parser.stages.segment._join_wrapped_lines` but operates
    on the per-line DocNodes that :func:`_between_text_nodes` emits.  Without
    this pass, every wrapped line inside an outer-table cell surfaces as its
    own paragraph node — the page-level segmenter's joining never runs here.
    """
    if not nodes:
        return nodes
    Y_TOL = 12.0  # ~1× body line height; same scale as segment._join_wrapped_lines
    Y_OVERLAP_TOL = 4.0  # allow small y-overlap when bullet glyph is taller than body
    X_TOL = 3.0

    out: list[DocNode] = [nodes[0]]
    for n in nodes[1:]:
        prev = out[-1]
        prev_bbox = prev.bbox if isinstance(prev.bbox, BBox) else prev.bbox[0]
        n_bbox = n.bbox if isinstance(n.bbox, BBox) else n.bbox[0]
        gap = n_bbox.y0 - prev_bbox.y1
        # For list_item prev: the continuation aligns with the text-after-bullet
        # indent.  We don't have per-glyph spans here, so accept any x0 that's
        # at or to the right of the bullet's x0 within a generous tolerance.
        if prev.kind == "list_item":
            x_aligned = n_bbox.x0 >= prev_bbox.x0 - X_TOL
        else:
            x_aligned = abs(n_bbox.x0 - prev_bbox.x0) <= X_TOL
        # Bullet glyphs are often rendered ~12 pt tall in a 9 pt body, so a
        # bullet-absorbed list_item's y1 extends a few pt below the line's
        # baseline.  The next continuation line then has y0 slightly *above*
        # that, producing a small negative gap.  Accept that as "adjacent".
        can_merge = (
            n.kind == "paragraph"
            and prev.kind in ("paragraph", "list_item")
            and -Y_OVERLAP_TOL <= gap <= Y_TOL
            and x_aligned
        )
        if can_merge:
            merged_bbox = BBox(
                page=prev_bbox.page,
                x0=min(prev_bbox.x0, n_bbox.x0),
                y0=min(prev_bbox.y0, n_bbox.y0),
                x1=max(prev_bbox.x1, n_bbox.x1),
                y1=max(prev_bbox.y1, n_bbox.y1),
            )
            out[-1] = DocNode(
                kind=prev.kind,
                bbox=merged_bbox,
                text=(prev.text or "") + " " + (n.text or ""),
                attrs=prev.attrs,
                provenance=prev.provenance,
            )
        else:
            out.append(n)
    return out


def _build_cell(text: str, bbox: BBox, pdf, depth: int,
                covered: bool = False, align: str = "left",
                page_chars: list[dict] | None = None) -> DocNode:
    children: list[DocNode] = []
    nested_bboxes: list[BBox] = []
    if not covered and depth + 1 < MAX_DEPTH:
        shrunk = BBox(
            page=bbox.page,
            x0=bbox.x0 + 1,
            y0=bbox.y0 + 1,
            x1=bbox.x1 - 1,
            y1=bbox.y1 - 1,
        )
        # Guard against degenerate (zero-area) cells after shrinking.
        if shrunk.x1 > shrunk.x0 and shrunk.y1 > shrunk.y0:
            nested = detect_tables(pdf=pdf, region_bbox=shrunk)
            for region in nested:
                # Skip if the detected region is as wide as the cell itself (echoed parent).
                if abs(region.bbox.x1 - region.bbox.x0) >= abs(bbox.x1 - bbox.x0) - 1:
                    continue
                children.append(_build_table(region, pdf, depth + 1, page_chars=page_chars))
                nested_bboxes.append(region.bbox)
    # Preserve text that lives between nested sub-tables in the same cell.
    # Without this, `text=None` (set when children is non-empty) drops any
    # paragraphs whose bbox is inside the cell but outside every sub-table.
    if children and page_chars:
        extra = _between_text_nodes(page_chars, bbox, nested_bboxes)
        if extra:
            children.extend(extra)
            children.sort(key=lambda n: n.bbox.y0 if isinstance(n.bbox, BBox) else n.bbox[0].y0)
    attrs: dict = {"align": align}
    if covered:
        attrs["covered"] = True
    return DocNode(
        kind="cell",
        bbox=bbox,
        text=text if not children else None,
        children=children,
        attrs=attrs,
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def _build_table(
    region: TableRegion, pdf, depth: int,
    page_chars: list[dict] | None = None,
) -> DocNode:
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(region.grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (
                region.cell_bboxes[r_idx][c_idx]
                if r_idx < len(region.cell_bboxes)
                and c_idx < len(region.cell_bboxes[r_idx])
                else region.bbox
            )
            align = _cell_align(page_chars, cbox) if page_chars else "left"
            cells.append(_build_cell(text, cbox, pdf, depth, align=align, page_chars=page_chars))
        rows.append(DocNode(
            kind="row",
            bbox=region.bbox,
            children=cells,
            attrs={"page": region.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=region.bbox,
        children=rows,
        attrs={
            "n_rows": len(region.grid),
            "n_cols": len(region.grid[0]) if region.grid else 0,
            "header_signature": tuple(region.grid[0]) if region.grid else (),
            "page": region.page_index,
            "page_height": region.page_height,
        },
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def _build_table_from_logical(
    logical_grid: list[list[str]],
    cell_bboxes: list[list[BBox]],
    region: TableRegion,
    pdf,
    depth: int,
    covered: set[tuple[int, int]] | None = None,
    page_chars: list[dict] | None = None,
) -> DocNode:
    """Build a DocNode table from a reconstructed logical (merged-cell) grid."""
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(logical_grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (
                cell_bboxes[r_idx][c_idx]
                if r_idx < len(cell_bboxes) and c_idx < len(cell_bboxes[r_idx])
                else region.bbox
            )
            is_covered = covered is not None and (r_idx, c_idx) in covered
            align = _cell_align(page_chars, cbox) if page_chars else "left"
            cells.append(_build_cell(text, cbox, pdf, depth, covered=is_covered, align=align, page_chars=page_chars))
        rows.append(DocNode(
            kind="row",
            bbox=region.bbox,
            children=cells,
            attrs={"page": region.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=region.bbox,
        children=rows,
        attrs={
            "n_rows": len(logical_grid),
            "n_cols": len(logical_grid[0]) if logical_grid else 0,
            "header_signature": tuple(logical_grid[0]) if logical_grid else (),
            "page": region.page_index,
            "page_height": region.page_height,
        },
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )



def _filter_outer_regions(regions: list) -> list:
    """Return only outermost TableRegions, dropping any fully contained within another.

    pdfplumber's line strategy detects nested sub-tables as independent top-level
    tables alongside their outer table.  Without this filter those sub-tables
    appear twice in the final tree: once nested inside the outer table's cell
    (via the recursive _build_cell detection) and once as a top-level sibling.
    """
    tol = _OVERLAP_TOL
    result = []
    for r in regions:
        rb = r.bbox
        dominated = any(
            other.bbox.page == rb.page
            and other.bbox.x0 <= rb.x0 + tol
            and other.bbox.y0 <= rb.y0 + tol
            and other.bbox.x1 >= rb.x1 - tol
            and other.bbox.y1 >= rb.y1 - tol
            and other is not r
            # 'other' must be strictly larger (not merely the same table).
            and (other.bbox.x0 < rb.x0 - tol
                 or other.bbox.y0 < rb.y0 - tol
                 or other.bbox.x1 > rb.x1 + tol
                 or other.bbox.y1 > rb.y1 + tol)
            for other in regions
        )
        if not dominated:
            result.append(r)
    return result

def extract_tables(pdf_path: Path) -> list[DocNode]:
    """
    Build a DocNode subtree for each top-level table in *pdf_path*.

    For each top-level table we first attempt to reconstruct the logical
    (merged-cell) outer grid using full-width horizontal lines.  This lets us
    correctly identify cells that contain nested tables even when pdfplumber's
    default detection "flattens" inner grids into the parent.  Cells whose bbox
    contains an inner table are given ``text=None`` and a table child; leaf
    cells carry ``text`` and ``children=[]``.
    """
    result: list[DocNode] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        regions = _filter_outer_regions(detect_tables(pdf=pdf))
        # Cache per-page state across all regions: pdfplumber's Page.chars and
        # find_tables_visible(page) are both expensive to recompute, and a
        # multi-table page would otherwise re-pay that cost once per region.
        page_chars_cache: dict[int, list[dict]] = {}
        page_tables_cache: dict[int, list] = {}
        for region in regions:
            page_idx = region.page_index
            page = pdf.pages[page_idx]
            if page_idx not in page_chars_cache:
                page_chars_cache[page_idx] = page.chars
            page_chars = page_chars_cache[page_idx]
            # find_tables() may return multiple tables on the page; match by bbox.
            # Always uses the default (line) strategy.  If `detect_tables`
            # found this region via the text-strategy fallback, this call
            # returns an empty list and `matched_pt` stays None, so the
            # logical-grid path is skipped.  Borderless tables therefore
            # rely on the pre-extracted `region.grid`; colspan/rowspan
            # detection is not available for them.
            if page_idx not in page_tables_cache:
                page_tables_cache[page_idx] = list(find_tables_visible(page))
            page_tables = page_tables_cache[page_idx]
            matched_pt = None
            for pt in page_tables:
                if abs(pt.bbox[0] - region.bbox.x0) < 2 and abs(pt.bbox[1] - region.bbox.y0) < 2:
                    matched_pt = pt
                    break

            logical = None
            # Redistributed regions (ruled-header / open-body tables) already
            # carry the corrected grid; rebuilding via outer-line geometry would
            # discard the body-row redistribution and re-merge the cells.
            if matched_pt is not None and not region.redistributed:
                logical = _logical_grid_from_table(page, matched_pt, region.page_index)

            if logical is not None:
                grid, bboxes, cov = logical
                result.append(
                    _build_table_from_logical(
                        grid, bboxes, region, pdf, depth=0, covered=cov,
                        page_chars=page_chars,
                    )
                )
            else:
                result.append(_build_table(region, pdf, depth=0, page_chars=page_chars))

    return result
