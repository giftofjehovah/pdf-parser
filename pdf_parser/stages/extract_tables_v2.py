"""Stage 4 (bottom-up): detect_cells → aggregate_tables → DocNode trees.

Owns the between-text helper (``_between_text_nodes``) outright: it is a pure
``(page_chars, cell_bbox, nested_bboxes) → list[paragraph DocNode]`` function
that does NOT belong to the legacy cascade.  Phase 10 inlines it from the
old ``extract_tables.py`` so the bottom-up path stands alone and the legacy
cascade can be deleted.
"""
from __future__ import annotations

from pathlib import Path

import pdfplumber

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.aggregate_tables import CellTable, aggregate
from pdf_parser.stages.detect_cells import detect_cells

_PROVENANCE = {"extractor": "bottom_up", "stage": "extract_tables_v2"}

# Between-text provenance keeps the legacy label ("pdfplumber" / "extract_tables")
# so existing goldens that pin paragraph provenance under bottom-up stay stable.
# This is the same convention the function carried before Phase 10's inline.
_BETWEEN_TEXT_PROVENANCE = {"extractor": "pdfplumber", "stage": "extract_tables"}


def extract_tables(pdf_path: Path, *, pdf=None) -> list[DocNode]:
    if pdf is not None:
        return _extract(pdf)
    with pdfplumber.open(str(pdf_path)) as opened:
        return _extract(opened)


def _extract(pdf) -> list[DocNode]:
    out: list[DocNode] = []
    for _page_idx, _cells, _cell_tables, nodes in _per_page(pdf):
        out.extend(nodes)
    return out


def _per_page(pdf):
    """Yield ``(page_idx, cells, cell_tables, docnodes)`` for each page.

    Factored out of :func:`_extract` so the debug bundle (which needs the
    intermediate ``cells`` and ``cell_tables``) can iterate the same loop
    without duplicating its logic.  Production callers ignore everything
    except ``docnodes``.
    """
    for page_idx, page in enumerate(pdf.pages):
        cells = detect_cells(page, page_idx)
        # ``page.extract_words`` feeds aggregate's between-text gap split so
        # inter-table prose (NOTE-MID1/2 on fixture 25) survives when the
        # outer pure-closed_rect frame would otherwise fuse two sub-tables
        # sharing column anchors into one table region.  See
        # ``aggregate_tables._gap_has_between_text``.
        page_words = page.extract_words(
            keep_blank_chars=False, use_text_flow=False,
        )
        cell_tables = aggregate(
            cells, page_height=float(page.height), page_words=page_words,
        )
        page_chars = page.chars
        nodes = [_celltable_to_docnode(t, page_chars=page_chars) for t in cell_tables]
        yield page_idx, cells, cell_tables, nodes


def _celltable_to_docnode(
    t: CellTable, page_chars: list[dict] | None = None,
) -> DocNode:
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(t.grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (t.cell_bboxes[r_idx][c_idx]
                    if r_idx < len(t.cell_bboxes) and c_idx < len(t.cell_bboxes[r_idx])
                    else t.bbox)
            is_covered = (r_idx, c_idx) in t.covered
            align = _cell_align(page_chars, cbox) if page_chars else "left"
            attrs: dict = {"align": align}
            if is_covered:
                attrs["covered"] = True
            nested_in_cell = [sub for sub in t.nested if _bbox_inside(sub.bbox, cbox)]
            nested_children = [
                _celltable_to_docnode(sub, page_chars=page_chars)
                for sub in nested_in_cell
            ]
            # Between-text: paragraphs that sit in this cell's y-range but
            # outside every nested sub-table.  Sorted with the nested
            # children so vertical order is preserved.
            extras: list[DocNode] = []
            if nested_children and page_chars is not None:
                extras = _between_text_nodes(
                    page_chars, cbox, [sub.bbox for sub in nested_in_cell],
                )
            combined = sorted(
                nested_children + extras,
                key=lambda n: n.bbox.y0 if hasattr(n.bbox, "y0") else n.bbox[0].y0,
            )
            cells.append(DocNode(
                kind="cell",
                bbox=cbox,
                text=text if not combined else None,
                children=combined,
                attrs=attrs,
                provenance=_PROVENANCE,
            ))
        row_bbox = (t.row_bboxes[r_idx]
                    if t.bbox_style == "tight" and r_idx < len(t.row_bboxes)
                    else t.bbox)
        rows.append(DocNode(
            kind="row",
            bbox=row_bbox,
            children=cells,
            attrs={"page": t.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=t.bbox,
        children=rows,
        attrs={
            "n_rows": len(t.grid),
            "n_cols": len(t.grid[0]) if t.grid else 0,
            "header_signature": t.header_signature,
            "page": t.page_index,
            "page_height": t.page_height,
        },
        provenance=_PROVENANCE,
    )


def _bbox_inside(inner: BBox, outer: BBox, tol: float = 2.0) -> bool:
    return (inner.page == outer.page
            and inner.x0 >= outer.x0 - tol and inner.y0 >= outer.y0 - tol
            and inner.x1 <= outer.x1 + tol and inner.y1 <= outer.y1 + tol)


# ---------------------------------------------------------------------------
# Cell alignment detection.
#
# Compares the gap from the cell's left wall to the text's left edge against
# the gap from the text's right edge to the cell's right wall.  Text that
# sits markedly closer to the right wall (right_gap < 30 % of left_gap AND
# right_gap < 6 pt absolute) is classified as right-aligned.  Empty cells,
# left-hugging text, and roughly-centred text all default to ``"left"``.
#
# Inlined verbatim from the legacy ``extract_tables._cell_align`` so the
# HTML renderer's ``.num`` CSS class (gated on ``attrs["align"] == "right"``)
# wires back up under bottom-up — pre-existing regression since the Phase-10
# rewrite hardcoded ``"left"`` for every cell.
# ---------------------------------------------------------------------------

def _cell_align(page_chars: list[dict], cbox: BBox, tol: float = 1.0) -> str:
    """Return ``"right"`` when chars inside ``cbox`` hug its right wall, else ``"left"``."""
    chars = [
        c for c in page_chars
        if c.get("x0", 0) >= cbox.x0 - tol
        and c.get("x1", 0) <= cbox.x1 + tol
        and c.get("top", 0) >= cbox.y0 - tol
        and c.get("bottom", 0) <= cbox.y1 + tol
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
    if right_gap < left_gap * 0.30 and right_gap < 6.0:
        return "right"
    return "left"


# ---------------------------------------------------------------------------
# Between-text paragraph extraction.
#
# When a parent cell hosts multiple nested sub-tables, text that lives between
# those sub-tables (in the cell's y-range, outside every sub-table bbox) is
# extracted here and returned as paragraph / list_item DocNodes so the caller
# can interleave them with the nested children in correct vertical order.
#
# Inlined from the legacy ``extract_tables.py`` in Phase 10; behaviour
# preserved verbatim (including the legacy provenance label so goldens stay
# stable across the cutover).
# ---------------------------------------------------------------------------

# Unicode/CID bullet glyphs that are unambiguous prefixes — when a cell
# line starts with one of these characters, it is definitely a bullet
# regardless of what follows.
_CELL_UNAMBIGUOUS_BULLETS = ("•", "◦", "▪", "(cid:127)")
# ASCII characters that act as bullets only when followed by whitespace
# (the bullet glyph proper, not a word lead).  Without the trailing-space
# guard, every wrapped cell line beginning with a word like "outer" or
# "or" would be misclassified as a list_item.
_CELL_ASCII_BULLETS = ("o", "-", "*")


def _is_cell_bullet_lead(stripped: str) -> bool:
    """True if ``stripped`` (already left-stripped) begins with a recognised
    cell-level bullet glyph.  Unicode/CID bullets match as bare prefixes;
    ASCII bullets ("o", "-", "*") must be followed by whitespace or the
    end of the line so words starting with the same letter (``outer``,
    ``older``, ``or``) do not flip to ``list_item``.
    """
    if stripped.startswith(_CELL_UNAMBIGUOUS_BULLETS):
        return True
    for b in _CELL_ASCII_BULLETS:
        if stripped.startswith(b) and (
            len(stripped) == len(b) or stripped[len(b)].isspace()
        ):
            return True
    return False


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
        is_list_item = _is_cell_bullet_lead(stripped)
        if is_list_item and stripped.startswith("(cid:127)"):
            line_text = "• " + stripped[len("(cid:127)"):].lstrip()
        nodes.append(DocNode(
            kind="list_item" if is_list_item else "paragraph",
            bbox=line_bbox,
            text=line_text,
            provenance=_BETWEEN_TEXT_PROVENANCE,
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
