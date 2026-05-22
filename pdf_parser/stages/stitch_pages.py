"""Stage 5: merge tables that continue across pages via column-anchor + header match."""

from __future__ import annotations

from pdf_parser.model import BBox, DocNode

COLUMN_ANCHOR_TOL = 4.0   # points
# A table that ends before this fraction of the page height from the bottom is
# complete on that page and must NOT be stitched to a same-column table on the
# next page.  Standard reportlab bottom margin is 1 in (72 pt) on a 792 pt LETTER
# page, so a table cut at the page break ends at ~720 pt (≈ 91% down the page).
# Using 0.15 means any table that ends before the bottom 15% (≥ y1 < 673 pt on
# LETTER) is treated as complete.  Only applied when page_height is stored in
# the table's attrs; synthetic tables built in tests are unaffected.
BOTTOM_MARGIN_FRAC = 0.15
# Fraction of page height within which a genuinely split row's cells must reach
# the physical page edge.  Much tighter than BOTTOM_MARGIN_FRAC: normal row
# boundaries end at the content margin (~10 % from the edge on LETTER+72pt
# margins), while rows that literally span pages extend to within a few points
# of the physical page boundary (y1 ≈ page_height, y0 ≈ 0 in pdfplumber
# top-origin coords).  3 % of 792 pt ≈ 24 pt — enough to tolerate hairline
# overruns from PDF generators, but far below any real content margin.
SPLIT_ROW_EDGE_FRAC = 0.03


def _col_anchors(table: DocNode) -> list[tuple[float, float]]:
    """Return (x0, x1) per logical column.

    Scans every row and picks the one with the most non-covered cells.
    This avoids colspan rows (where one cell spans the full table width)
    misreporting the column structure and preventing a legitimate stitch.
    """
    best: list[tuple[float, float]] = []
    for row in table.children:
        anchors = [
            (c.bbox.x0, c.bbox.x1)
            for c in row.children
            if not c.attrs.get("covered")
        ]
        if len(anchors) > len(best):
            best = anchors
    return best


def _anchors_match(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(a[i][0] - b[i][0]) <= COLUMN_ANCHOR_TOL and abs(a[i][1] - b[i][1]) <= COLUMN_ANCHOR_TOL
               for i in range(len(a)))


def _header_signature(table: DocNode) -> tuple[str, ...]:
    sig = table.attrs.get("header_signature")
    return tuple(sig) if sig else ()


def _first_bbox(node: DocNode) -> BBox:
    return node.bbox if isinstance(node.bbox, BBox) else node.bbox[0]


def _can_merge(prev: DocNode, nxt: DocNode) -> bool:
    if not _anchors_match(_col_anchors(prev), _col_anchors(nxt)):
        return False
    p_page = prev.bbox[-1].page if isinstance(prev.bbox, list) else prev.bbox.page
    n_page = _first_bbox(nxt).page
    if n_page != p_page + 1:
        return False
    # Guard against stitching two independent tables that happen to share column
    # anchors: a table cut by a page break fills near the page bottom; a table
    # that ends well before the bottom is complete and should not be merged.
    page_height: float | None = prev.attrs.get("page_height")
    if page_height:
        prev_last_bbox = prev.bbox[-1] if isinstance(prev.bbox, list) else prev.bbox
        if prev_last_bbox.y1 < page_height * (1.0 - BOTTOM_MARGIN_FRAC):
            return False
    return True


def _merge_two(prev: DocNode, nxt: DocNode) -> DocNode:
    prev_bboxes = prev.bbox if isinstance(prev.bbox, list) else [prev.bbox]
    next_bbox = _first_bbox(nxt)
    rows_next = list(nxt.children)
    if _header_signature(prev) and _header_signature(nxt) == _header_signature(prev):
        rows_next = rows_next[1:]  # drop duplicate header
    # Reindex row indices and ensure each row's attrs.page is set.
    rebuilt_rows: list[DocNode] = []
    for row in list(prev.children) + rows_next:
        new_attrs = dict(row.attrs)
        new_attrs["row_index"] = len(rebuilt_rows)
        new_attrs.setdefault("page", _first_bbox(row).page)
        rebuilt_rows.append(DocNode(
            kind=row.kind, bbox=row.bbox, children=row.children, attrs=new_attrs,
            text=row.text, provenance=row.provenance,
        ))
    merged_attrs = dict(prev.attrs)
    merged_attrs["n_rows"] = len(rebuilt_rows)
    merged_attrs["spans_pages"] = sorted({_first_bbox(r).page for r in rebuilt_rows})
    return DocNode(
        kind="table",
        bbox=prev_bboxes + [next_bbox],
        children=rebuilt_rows,
        attrs=merged_attrs,
        provenance={"extractor": "pdfplumber+stitch", "stage": "stitch_pages"},
    )


# ---------------------------------------------------------------------------
# Split-row stitching
#
# When a single outer-table row is so tall that it straddles a page break,
# pdfplumber sees it as two separate rows: a "top half" on page P whose cells
# reach the page bottom, and a "bottom half" on page P+1 whose cells start at
# the page top.  After _merge_two appends rows from both pages, those two
# halves appear as consecutive rows in the merged table.  The functions below
# detect and collapse such pairs, recursively stitching any sub-table fragments
# and reassembling paragraph text found in the joined cells.
# ---------------------------------------------------------------------------

def _row_page(row: DocNode) -> int:
    """Return the page a row belongs to (prefer attrs["page"] set during build)."""
    p = row.attrs.get("page")
    if p is not None:
        return int(p)
    return _first_bbox(row).page


def _cell_primary_bbox(cell: DocNode) -> BBox:
    """Return the first BBox for a cell (handles list bbox from prior merges)."""
    return cell.bbox[0] if isinstance(cell.bbox, list) else cell.bbox


def _is_split_row_pair(
    row_top: DocNode, row_bot: DocNode, page_height: float
) -> bool:
    """Return True when row_top and row_bot are the two halves of a single
    outer-table row split by a page break.

    Conditions (all must hold):
      1. row_bot is on the page immediately after row_top.
      2. Both rows have the same column count.
      3. row_top's cells extend to within BOTTOM_MARGIN_FRAC of the page bottom.
      4. row_bot's cells start within BOTTOM_MARGIN_FRAC of the page top.
    """
    top_page = _row_page(row_top)
    bot_page = _row_page(row_bot)
    if bot_page != top_page + 1:
        return False
    if len(row_top.children) != len(row_bot.children):
        return False
    # row_top must reach the page bottom.
    top_y1 = max(
        (_cell_primary_bbox(c).y1 for c in row_top.children),
        default=0.0,
    )
    if top_y1 < page_height * (1.0 - SPLIT_ROW_EDGE_FRAC):
        return False
    # row_bot must start at the page top.
    bot_y0 = min(
        (_cell_primary_bbox(c).y0 for c in row_bot.children),
        default=page_height,
    )
    if bot_y0 > page_height * SPLIT_ROW_EDGE_FRAC:
        return False
    return True


def _node_sort_key(n: DocNode) -> tuple[int, float]:
    """Sort key for child nodes: (page, y0) using the primary bbox."""
    b = n.bbox[0] if isinstance(n.bbox, list) else n.bbox
    return (b.page, b.y0)


def _merge_split_cells(cell_top: DocNode, cell_bot: DocNode) -> DocNode:
    """Merge two page-split cell halves into one cell node.

    Container cells (children present on either half):
        Combine all children, stitch any sub-table fragments that straddle the
        page break, then sort everything into document order by (page, y0).

    Leaf cells (only text, no children on either half):
        Concatenate the text strings with a single space.
    """
    top_ch = list(cell_top.children)
    bot_ch = list(cell_bot.children)

    if top_ch or bot_ch:
        combined = top_ch + bot_ch
        # Stitch sub-table fragments that straddle the page break.  The same
        # column-anchor + page-adjacency + bottom-margin logic applies here as
        # for top-level tables: a sub-table fragment that ends near the page
        # bottom will be stitched to a matching fragment that starts on the next
        # page.  Independent sub-tables within the same cell (A then B, not a
        # split of one table) will NOT be stitched because sub-table A ends well
        # before the page bottom.
        sub_tables = [c for c in combined if c.kind == "table"]
        non_tables = [c for c in combined if c.kind != "table"]
        stitched = stitch_tables(sub_tables) if sub_tables else []
        combined = non_tables + stitched
        combined.sort(key=_node_sort_key)
        return DocNode(
            kind="cell",
            bbox=cell_top.bbox,
            children=combined,
            text=None,
            attrs=cell_top.attrs,
            provenance={**cell_top.provenance, "split_cell_merged": True},
        )
    else:
        # Leaf cell: join text from both halves.
        parts = [t for t in [cell_top.text, cell_bot.text] if t and t.strip()]
        return DocNode(
            kind="cell",
            bbox=cell_top.bbox,
            children=[],
            text=" ".join(parts) if parts else None,
            attrs=cell_top.attrs,
            provenance={**cell_top.provenance, "split_cell_merged": True},
        )


def _merge_split_row_pair(row_top: DocNode, row_bot: DocNode) -> DocNode:
    """Merge two page-split row halves into one row node."""
    merged_cells = [
        _merge_split_cells(c_top, c_bot)
        for c_top, c_bot in zip(row_top.children, row_bot.children)
    ]
    return DocNode(
        kind="row",
        bbox=row_top.bbox,
        children=merged_cells,
        attrs={**row_top.attrs, "split_row_merged": True},
        provenance=row_top.provenance,
    )


def _merge_split_rows_in_table(table: DocNode) -> DocNode:
    """Within a stitched multi-page table, detect and collapse any rows that
    were split at a page break.

    A split row manifests as two consecutive rows after _merge_two: row_top on
    page P whose cells reach the page bottom, followed by row_bot on page P+1
    whose cells start at the page top.  After merging, their cell contents are
    combined and sub-table fragments within cells are stitched recursively via
    stitch_tables.
    """
    page_height: float | None = table.attrs.get("page_height")
    if not page_height:
        return table
    rows = list(table.children)
    if len(rows) < 2:
        return table

    merged: list[DocNode] = []
    i = 0
    while i < len(rows):
        if (
            i + 1 < len(rows)
            and _is_split_row_pair(rows[i], rows[i + 1], page_height)
        ):
            merged.append(_merge_split_row_pair(rows[i], rows[i + 1]))
            i += 2
        else:
            merged.append(rows[i])
            i += 1

    if len(merged) == len(rows):
        return table  # no split pairs found; skip rebuild

    rebuilt = [
        DocNode(
            kind=r.kind,
            bbox=r.bbox,
            children=r.children,
            attrs={**r.attrs, "row_index": idx},
            text=r.text,
            provenance=r.provenance,
        )
        for idx, r in enumerate(merged)
    ]
    return DocNode(
        kind=table.kind,
        bbox=table.bbox,
        children=rebuilt,
        attrs={**table.attrs, "n_rows": len(rebuilt)},
        provenance=table.provenance,
    )


def stitch_tables(tables: list[DocNode]) -> list[DocNode]:
    if not tables:
        return []
    out: list[DocNode] = [tables[0]]
    for t in tables[1:]:
        if _can_merge(out[-1], t):
            merged = _merge_two(out[-1], t)
            out[-1] = _merge_split_rows_in_table(merged)
        else:
            out.append(t)
    return out
