"""Markdown renderer. GFM pipe tables; cells containing tables fall back to inline HTML."""

from __future__ import annotations

from pdf_parser.model import DocNode


def _escape_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def _has_nested_table(table: DocNode) -> bool:
    for row in table.children:
        for cell in row.children:
            for c in cell.children:
                if c.kind == "table":
                    return True
    return False


def _render_html_cell(cell: DocNode) -> str:
    parts: list[str] = []
    if cell.text:
        parts.append(cell.text)
    for c in cell.children:
        if c.kind == "table":
            parts.append(_render_html_table(c))
        elif c.text:
            parts.append(c.text)
    return "".join(parts)


def _render_html_table(table: DocNode) -> str:
    out = ["<table>"]
    for row in table.children:
        out.append("<tr>")
        for cell in row.children:
            out.append(f"<td>{_render_html_cell(cell)}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _render_gfm_table(table: DocNode) -> str:
    if not table.children:
        return ""
    header = table.children[0]
    body = table.children[1:]
    header_cells = [_escape_cell(c.text or "") for c in header.children]
    lines = [
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join(["---"] * len(header_cells)) + " |",
    ]
    for row in body:
        cells = [_escape_cell(c.text or "") for c in row.children]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_node(node: DocNode) -> str:
    if node.kind in ("document", "page", "section"):
        return "\n\n".join(_render_node(c) for c in node.children if _render_node(c))
    if node.kind == "heading":
        level = max(1, min(6, node.attrs.get("level", 1)))
        return f"{'#' * level} {node.text or ''}"
    if node.kind == "paragraph":
        return node.text or ""
    if node.kind == "list":
        return "\n".join(f"- {c.text or ''}" for c in node.children)
    if node.kind == "list_item":
        return f"- {node.text or ''}"
    if node.kind == "table":
        return _render_html_table(node) if _has_nested_table(node) else _render_gfm_table(node)
    if node.kind == "figure":
        path = node.attrs.get("path", "")
        return f"![{node.text or ''}]({path})"
    return node.text or ""


def to_markdown(tree: DocNode) -> str:
    return _render_node(tree).strip() + "\n"
