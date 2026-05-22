"""Chunk RAG-ready records from a DocNode tree.

Rules:
- Never split a row or a cell.
- Heading ancestors flow into breadcrumb (not text).
- Big tables split on row-group boundaries; header row is repeated.
- Paragraph chunks get token overlap; table chunks do not.
"""

from __future__ import annotations

from pdf_parser.model import BBox, Chunk, DocNode


def _est_tokens(s: str) -> int:
    # Rough: 1 token ≈ 0.75 words. Words = whitespace split.
    return max(1, int(len(s.split()) / 0.75))


def _bbox_pages(node: DocNode) -> tuple[int, int]:
    bboxes = node.bbox if isinstance(node.bbox, list) else [node.bbox]
    pages = [b.page for b in bboxes]
    return (min(pages), max(pages))


def _md_cell_text(text: str) -> str:
    """Normalise cell text for markdown emission: collapse internal whitespace
    (including newlines introduced by rowspan'd multi-line cells) to single
    spaces, then escape pipes.
    """
    return " ".join(text.split()).replace("|", "\\|")


def _row_texts_filled(row: DocNode, fill_state: list[str]) -> list[str]:
    """Return per-cell text for ``row``, filling rowspan continuations from
    the most recent non-covered value at the same column index.

    Mutates ``fill_state`` so subsequent calls in document order see the
    updated last-seen values.  This is how the chunker carries merged-cell
    context (e.g. ``Pacific Northwest Division``) into every continuation
    row's markdown — without it, embeddings for the continuation rows lack
    the merge label and vector retrieval misses by-row queries that mention
    the merged value.
    """
    cells = [c for c in row.children if c.kind == "cell"]
    while len(fill_state) < len(cells):
        fill_state.append("")
    out: list[str] = []
    for c_idx, cell in enumerate(cells):
        if cell.attrs.get("covered"):
            out.append(fill_state[c_idx])
        else:
            text = cell.text or ""
            out.append(text)
            fill_state[c_idx] = text
    return out


def _row_to_md(row: DocNode, fill_state: list[str]) -> str:
    return "| " + " | ".join(_md_cell_text(t) for t in _row_texts_filled(row, fill_state)) + " |"


def _split_table(table: DocNode, max_tokens: int) -> list[Chunk]:
    rows = table.children
    if not rows:
        return []
    header = rows[0]
    body = rows[1:]
    # Fill-state threads through every row in document order so each emitted
    # markdown line includes any rowspan source value above it.
    fill_state: list[str] = []
    header_md = _row_to_md(header, fill_state)
    n_cols = len(header.children)

    chunks: list[Chunk] = []
    buf: list[DocNode] = []
    buf_lines: list[str] = []
    buf_tokens = _est_tokens(header_md)
    for row in body:
        line = _row_to_md(row, fill_state)
        if buf_tokens + _est_tokens(line) > max_tokens and buf:
            chunks.append(_table_chunk(table, header_md, buf, buf_lines, n_cols))
            buf = []
            buf_lines = []
            buf_tokens = _est_tokens(header_md)
        buf.append(row)
        buf_lines.append(line)
        buf_tokens += _est_tokens(line)
    if buf:
        chunks.append(_table_chunk(table, header_md, buf, buf_lines, n_cols))
    return chunks


def _table_chunk(
    table: DocNode, header_md: str, rows: list[DocNode], body_lines: list[str], n_cols: int,
) -> Chunk:
    body_md = "\n".join(body_lines)
    text = header_md + "\n" + body_md
    pages = sorted({_bbox_pages(r)[0] for r in rows})
    return Chunk(
        text=text,
        breadcrumb=[],  # filled in by caller using ancestor stack
        page_range=(min(pages), max(pages)),
        source_ids=[table.id] + [r.id for r in rows],
        kind_summary=f"table:{table.attrs.get('n_rows', '?')}x{n_cols}",
    )


def _split_paragraph(node: DocNode, max_tokens: int, overlap: int) -> list[Chunk]:
    words = (node.text or "").split()
    if not words:
        return []
    chunks: list[Chunk] = []
    start = 0
    page_range = _bbox_pages(node)
    while start < len(words):
        end = start + max(1, int(max_tokens * 0.75))
        text = " ".join(words[start:end])
        chunks.append(Chunk(
            text=text,
            breadcrumb=[],
            page_range=page_range,
            source_ids=[node.id],
            kind_summary="paragraph",
        ))
        if end >= len(words):
            break
        start = end - max(0, int(overlap * 0.75))
    return chunks


def _walk_with_breadcrumb(node: DocNode, crumbs: list[str], max_tokens: int, overlap: int) -> list[Chunk]:
    out: list[Chunk] = []
    if node.kind == "paragraph":
        ch = _split_paragraph(node, max_tokens, overlap)
        for c in ch:
            c.breadcrumb = list(crumbs)
        return ch
    if node.kind == "list":
        text = "\n".join(f"- {(c.text or '').strip()}" for c in node.children)
        if not text:
            return []
        return [Chunk(
            text=text, breadcrumb=list(crumbs),
            page_range=_bbox_pages(node),
            source_ids=[node.id] + [c.id for c in node.children],
            kind_summary="list",
        )]
    if node.kind == "table":
        ch = _split_table(node, max_tokens)
        for c in ch:
            c.breadcrumb = list(crumbs)
        return ch

    # Containers (document, page, section, cell): iterate children and carry
    # a running crumbs list so each heading updates the context for its
    # subsequent siblings — headings themselves are never emitted as chunks.
    current_crumbs = crumbs
    for child in node.children:
        if child.kind == "heading" and child.text:
            current_crumbs = current_crumbs + [child.text]
        else:
            out.extend(_walk_with_breadcrumb(child, current_crumbs, max_tokens, overlap))
    return out


def chunk_tree(tree: DocNode, max_tokens: int = 800, overlap: int = 100) -> list[Chunk]:
    return _walk_with_breadcrumb(tree, crumbs=[], max_tokens=max_tokens, overlap=overlap)
