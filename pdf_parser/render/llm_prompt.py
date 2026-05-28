"""LLM-prompt renderer: per-page, ID-anchored, spanning-table-slice aware.

Emits one document page as a YAML-like envelope plus indented HTML+Markdown.
Designed as input to an LLM that decides chunk boundaries against the parsed
tree:

  * Every leaf carries its DocNode ``id`` as a ``data-id`` attribute or
    ``<!-- id:X -->`` anchor.  The LLM returns chunk decisions referencing
    those ids; it never re-emits text.
  * Prose blocks (heading / paragraph / list / figure) render as Markdown
    so the model reads them at the lowest possible token cost.
  * Tables render as indented semantic HTML so nesting depth is visible to
    the model.  Merged cells are encoded with ``colspan`` / ``rowspan``;
    rowspan-placeholder rows (every cell ``attrs.covered``) are skipped
    entirely so they cannot mislead the model into chunking at empty rows.
  * Tables that stitch across pages are emitted **as a slice**: only the
    rows whose ``bbox.page`` matches the current page, plus the header row
    repeated when this is a continuation.  The same ``data-id`` appears on
    every slice so the model can recognise "this is the same logical table"
    across calls.  ``data-continued-from`` / ``data-continues-to`` mark the
    slice's position in the sequence; ``data-shape`` carries the total
    shape and ``data-row-range`` the slice's row indices.

The envelope carries cross-page context the page body cannot:

  page: 0-indexed page number (matches ``BBox.page``).
  pages_total: number of pages in the document.
  breadcrumb: ancestor heading stack at the start of this page,
              accumulated from prior pages.
  continued_tables: tables anchored before this page that extend into it,
              with id / shape / header / first_page so the LLM has stable
              metadata even when the table body is below the fold.

Free text on a continuation page is interleaved with the spanning-table
slice in y0 (reading) order, so a footnote at the bottom of a
table-dominated page renders below the slice rather than alongside it.
"""

from __future__ import annotations

import json
from typing import Optional

from pdf_parser.model import BBox, DocNode

INDENT = "  "
_PROSE_KINDS = ("paragraph", "heading", "list", "list_item", "figure")


# --------------------------------------------------------------------------- #
# tree helpers                                                                #
# --------------------------------------------------------------------------- #


def _table_pages(t: DocNode) -> list[int]:
    """All pages spanned by ``t``, in order."""
    return [b.page for b in t.bbox] if isinstance(t.bbox, list) else [t.bbox.page]


def _table_bbox_on_page(t: DocNode, page: int) -> Optional[BBox]:
    bboxes = t.bbox if isinstance(t.bbox, list) else [t.bbox]
    for b in bboxes:
        if b.page == page:
            return b
    return None


def _row_page(r: DocNode) -> int:
    bb = r.bbox if not isinstance(r.bbox, list) else r.bbox[0]
    return bb.page


def _is_placeholder_row(row: DocNode) -> bool:
    """True if every cell is rowspan-covered (synthetic continuation row)."""
    cells = [c for c in row.children if c.kind == "cell"]
    return bool(cells) and all(c.attrs.get("covered") for c in cells)


def _breadcrumb_at_page(tree: DocNode, page_index: int) -> list[str]:
    """Heading stack as it stands at the start of ``page_index``."""
    stack: list[tuple[int, str]] = []
    for page in tree.children[:page_index]:
        for n in page.children:
            if n.kind == "heading" and n.text:
                level = n.attrs.get("level", 1)
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, n.text))
    return [text for _, text in stack]


def _active_continuations(tree: DocNode, page_index: int) -> list[DocNode]:
    """Tables anchored on a prior page that extend into ``page_index``."""
    out: list[DocNode] = []
    for prior in tree.children[:page_index]:
        for child in prior.children:
            if child.kind != "table":
                continue
            pages = _table_pages(child)
            if page_index in pages and pages[0] != page_index:
                out.append(child)
    return out


# --------------------------------------------------------------------------- #
# rendering                                                                   #
# --------------------------------------------------------------------------- #


def _esc(s: str) -> str:
    """Minimal escaping for content inside ``<td>`` text. Pre-escapes ``&``
    so the LLM never sees an entity we have to manage; ``<`` is escaped so
    a stray ``<`` inside cell text can't open a fake tag."""
    return s.replace("&", "&amp;").replace("<", "&lt;")


def _block_anchor(node: DocNode) -> str:
    parts = [f"id:{node.id}", f"kind:{node.kind}"]
    if node.kind == "heading":
        parts.append(f"level:{node.attrs.get('level', 1)}")
    return f"<!-- {' '.join(parts)} -->"


def _render_prose(node: DocNode) -> list[str]:
    """Render a non-table block as ``[anchor, body...]`` lines."""
    if node.kind == "heading":
        level = max(1, min(6, node.attrs.get("level", 1)))
        return [_block_anchor(node), f"{'#' * level} {node.text or ''}"]
    if node.kind == "paragraph":
        return [_block_anchor(node), node.text or ""]
    if node.kind == "list":
        lines = [_block_anchor(node)]
        for li in node.children:
            lines.append(f"- {(li.text or '').strip()}")
        return lines
    if node.kind == "list_item":
        return [_block_anchor(node), f"- {(node.text or '').strip()}"]
    if node.kind == "figure":
        return [_block_anchor(node), f"![{node.text or ''}]({node.attrs.get('path', '')})"]
    return []


def _render_cell_body(cell: DocNode, indent: int) -> list[str]:
    """Body lines that go between ``<td>`` and ``</td>`` for a cell that has
    nested structure (sub-tables, paragraphs, headings).  Cells with only
    plain text are emitted inline by the caller."""
    pad = INDENT * indent
    lines: list[str] = []
    text = (cell.text or "").strip()
    if text:
        lines.append(f"{pad}{_esc(text)}")
    for child in cell.children:
        if child.kind == "table":
            # Nested tables render in full at the current depth.  They do
            # not split across the outer table's page break — even when a
            # nested table is itself stitched, every row lives under one
            # outer cell so it renders together with that cell.
            lines.append(_render_table_full(child, indent))
        elif child.kind in _PROSE_KINDS:
            for line in _render_prose(child):
                lines.append(f"{pad}{line}")
    return lines


def _render_row(row: DocNode, indent: int) -> list[str]:
    """Render one ``<tr>``.  Returns an empty list for rowspan-placeholder
    rows so the LLM never sees them."""
    if _is_placeholder_row(row):
        return []
    pad = INDENT * indent
    lines = [f'{pad}<tr data-id="{row.id}">']
    for cell in row.children:
        if cell.kind != "cell" or cell.attrs.get("covered"):
            continue
        attrs = [f'data-id="{cell.id}"']
        for k in ("colspan", "rowspan"):
            v = cell.attrs.get(k)
            if v and v > 1:
                attrs.append(f'{k}="{v}"')
        attr_str = " ".join(attrs)
        has_inner = any(
            child.kind == "table" or child.kind in _PROSE_KINDS
            for child in cell.children
        )
        if not has_inner:
            text = _esc((cell.text or "").strip())
            lines.append(f"{pad}{INDENT}<td {attr_str}>{text}</td>")
        else:
            lines.append(f"{pad}{INDENT}<td {attr_str}>")
            lines.extend(_render_cell_body(cell, indent + 2))
            lines.append(f"{pad}{INDENT}</td>")
    lines.append(f"{pad}</tr>")
    return lines


def _table_shape(t: DocNode) -> tuple[int, int]:
    n_rows = t.attrs.get("n_rows", len(t.children))
    n_cols = t.attrs.get("n_cols",
                        len(t.children[0].children) if t.children else 0)
    return int(n_rows), int(n_cols)


def _render_table_full(table: DocNode, indent: int = 0) -> str:
    """Whole-table emission.  Used for non-spanning tables and for nested
    tables (which never split across the outer's page break in their own
    right)."""
    pad = INDENT * indent
    n_rows, n_cols = _table_shape(table)
    attrs = [f'data-id="{table.id}"', f'data-shape="{n_rows}x{n_cols}"']
    lines = [f'{pad}<table {" ".join(attrs)}>']
    for r in table.children:
        lines.extend(_render_row(r, indent + 1))
    lines.append(f"{pad}</table>")
    return "\n".join(lines)


def _render_table_slice(table: DocNode, page: int, indent: int = 0) -> str:
    """Emit only the rows of ``table`` whose physical page is ``page``.

    Header row is always present at the top of the slice; ``data-header-
    repeat="true"`` is added on continuations so the model knows the header
    is repeated context, not new content.
    """
    pages = _table_pages(table)
    if len(pages) == 1:
        return _render_table_full(table, indent)

    pad = INDENT * indent
    header = table.children[0] if table.children else None
    body_on_page = [r for r in table.children if _row_page(r) == page]
    if not body_on_page:
        return ""  # table claims this page but no rows live here

    header_repeated = header is not None and header not in body_on_page
    slice_rows = ([header] + body_on_page) if header_repeated else body_on_page

    n_rows, n_cols = _table_shape(table)
    body_idxs = [r.attrs.get("row_index", -1) for r in body_on_page
                 if r.attrs.get("row_index", -1) >= 0]
    row_range = f"{min(body_idxs)}-{max(body_idxs)}" if body_idxs else "-"

    attrs = [
        f'data-id="{table.id}"',
        f'data-shape="{n_rows}x{n_cols}"',
        f'data-row-range="{row_range}"',
    ]
    pos = pages.index(page)
    if pos > 0:
        attrs.append(f'data-continued-from="p{pages[pos - 1]}"')
    if pos < len(pages) - 1:
        attrs.append(f'data-continues-to="p{pages[pos + 1]}"')
    if header_repeated:
        attrs.append('data-header-repeat="true"')

    lines = [f'{pad}<table {" ".join(attrs)}>']
    for r in slice_rows:
        if r is None:
            continue
        lines.extend(_render_row(r, indent + 1))
    lines.append(f"{pad}</table>")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# envelope + top-level                                                        #
# --------------------------------------------------------------------------- #


def _envelope(
    page: int,
    total: int,
    breadcrumb: list[str],
    continuations: list[DocNode],
) -> str:
    cont: list[dict] = []
    for t in continuations:
        n_rows, n_cols = _table_shape(t)
        sig = t.attrs.get("header_signature") or ()
        cont.append({
            "id": t.id,
            "shape": f"{n_rows}x{n_cols}",
            "header": list(sig),
            "first_page": _table_pages(t)[0],
        })
    lines = [
        "---",
        f"page: {page}",
        f"pages_total: {total}",
        f"breadcrumb: {json.dumps(breadcrumb, ensure_ascii=False)}",
        f"continued_tables: {json.dumps(cont, ensure_ascii=False)}",
        "---",
    ]
    return "\n".join(lines)


def _item_y0(node_or_bbox) -> float:
    if isinstance(node_or_bbox, BBox):
        return node_or_bbox.y0
    bbox = node_or_bbox.bbox
    if isinstance(bbox, list):
        bbox = bbox[0]
    return bbox.y0


def to_llm_prompt(tree: DocNode, page_index: int) -> str:
    """Render one document page as an LLM prompt body.

    ``page_index`` is 0-indexed and must match ``BBox.page``.  Raises
    ``ValueError`` if out of range.

    Output layout::

        ---
        page: N
        pages_total: M
        breadcrumb: [...]
        continued_tables: [...]
        ---

        <!-- id:... kind:heading level:1 -->
        # Page title

        <!-- id:... kind:paragraph -->
        Lorem ipsum...

        <table data-id="..." data-shape="..." ...>
          <tr data-id="...">
            <td data-id="...">...</td>
          </tr>
        </table>
    """
    if not (0 <= page_index < len(tree.children)):
        raise ValueError(
            f"page_index {page_index} out of range "
            f"0..{len(tree.children) - 1}"
        )

    page_node = tree.children[page_index]
    breadcrumb = _breadcrumb_at_page(tree, page_index)
    continuations = _active_continuations(tree, page_index)
    envelope = _envelope(page_index, len(tree.children), breadcrumb,
                         continuations)

    # Items get a y0 sort key so a continuation table slice on a page
    # interleaves correctly with that page's free prose (e.g. a footnote
    # below the slice).
    items: list[tuple[float, str]] = []

    for child in page_node.children:
        if child.kind == "table":
            bb = _table_bbox_on_page(child, page_index)
            y0 = bb.y0 if bb else _item_y0(child)
            items.append((y0, _render_table_slice(child, page_index)))
        else:
            items.append((_item_y0(child), "\n".join(_render_prose(child))))

    for t in continuations:
        bb = _table_bbox_on_page(t, page_index)
        if bb is None:
            continue
        rendered = _render_table_slice(t, page_index)
        if rendered:
            items.append((bb.y0, rendered))

    items.sort(key=lambda x: x[0])
    body = "\n\n".join(s for _, s in items if s.strip())
    return envelope + ("\n\n" + body if body else "")


def to_llm_prompts(tree: DocNode) -> list[str]:
    """Render every page as a separate LLM prompt body, in page order."""
    return [to_llm_prompt(tree, i) for i in range(len(tree.children))]
