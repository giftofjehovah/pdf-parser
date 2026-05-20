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


def _row_to_md(row: DocNode) -> str:
    return "| " + " | ".join((c.text or "").replace("|", "\\|") for c in row.children) + " |"


def _split_table(table: DocNode, max_tokens: int) -> list[Chunk]:
    rows = table.children
    if not rows:
        return []
    header = rows[0]
    body = rows[1:]
    header_md = _row_to_md(header)
    n_cols = len(header.children)

    chunks: list[Chunk] = []
    buf: list[DocNode] = []
    buf_tokens = _est_tokens(header_md)
    for row in body:
        line = _row_to_md(row)
        if buf_tokens + _est_tokens(line) > max_tokens and buf:
            chunks.append(_table_chunk(table, header_md, buf, n_cols))
            buf = []
            buf_tokens = _est_tokens(header_md)
        buf.append(row)
        buf_tokens += _est_tokens(line)
    if buf:
        chunks.append(_table_chunk(table, header_md, buf, n_cols))
    return chunks


def _table_chunk(table: DocNode, header_md: str, rows: list[DocNode], n_cols: int) -> Chunk:
    body_md = "\n".join(_row_to_md(r) for r in rows)
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
