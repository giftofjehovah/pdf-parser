"""HTML renderer — native nested tables. Escapes text."""

from __future__ import annotations

import html as _h

from pdf_parser.model import DocNode


def _esc(s: str | None) -> str:
    return _h.escape(s or "", quote=False)


def _render_table(t: DocNode) -> str:
    out = ["<table>"]
    for i, row in enumerate(t.children):
        out.append("<tr>")
        tag = "th" if i == 0 else "td"
        for cell in row.children:
            out.append(f"<{tag}>{_render_cell(cell)}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _render_cell(cell: DocNode) -> str:
    parts: list[str] = []
    if cell.text:
        parts.append(_esc(cell.text))
    for c in cell.children:
        if c.kind == "table":
            parts.append(_render_table(c))
        else:
            parts.append(_render(c))
    return "".join(parts)


def _render(node: DocNode) -> str:
    if node.kind == "document":
        return "<article>" + "".join(_render(c) for c in node.children) + "</article>"
    if node.kind == "page":
        return "<section data-page=\"" + str(node.attrs.get("page_index", 0)) + "\">" + \
               "".join(_render(c) for c in node.children) + "</section>"
    if node.kind == "section":
        return "<section>" + "".join(_render(c) for c in node.children) + "</section>"
    if node.kind == "heading":
        level = max(1, min(6, node.attrs.get("level", 1)))
        return f"<h{level}>{_esc(node.text)}</h{level}>"
    if node.kind == "paragraph":
        return f"<p>{_esc(node.text)}</p>"
    if node.kind == "list":
        return "<ul>" + "".join(f"<li>{_esc(c.text)}</li>" for c in node.children) + "</ul>"
    if node.kind == "list_item":
        return f"<li>{_esc(node.text)}</li>"
    if node.kind == "table":
        return _render_table(node)
    if node.kind == "figure":
        path = node.attrs.get("path", "")
        return f'<figure><img src="{_esc(path)}" alt="{_esc(node.text)}"></figure>'
    return _esc(node.text)


def to_html(tree: DocNode) -> str:
    return _render(tree)
