"""JSON renderers.

* ``to_json`` — full pydantic dump (bbox, provenance, attrs, id, everything).
  Use when downstream tooling needs the entire DocNode tree faithfully.
* ``to_tree_json`` — simplified hierarchy.  Drops bbox / provenance / id /
  ``attrs`` noise, keeps only kind + text + the handful of attributes that
  describe content shape (heading level, table shape, cell merges, figure
  path, page index, spanning info).  Use this when you want to *read* the
  document's structure — review, debugging, LLM context where positional
  metadata is irrelevant.
"""

from __future__ import annotations

import json
from typing import Any

from pdf_parser.model import BBox, DocNode


def to_json(tree: DocNode, indent: int | None = None) -> str:
    return tree.model_dump_json(indent=indent)


# --------------------------------------------------------------------------- #
# simplified tree                                                             #
# --------------------------------------------------------------------------- #


def _is_placeholder_row(row: DocNode) -> bool:
    cells = [c for c in row.children if c.kind == "cell"]
    return bool(cells) and all(c.attrs.get("covered") for c in cells)


def _visible_table_shape(table: DocNode) -> str:
    """Shape after filtering rowspan-placeholder rows and covered cells.

    Reflects what a reader perceives, not the parser's internal grid.
    """
    visible_rows = [r for r in table.children if not _is_placeholder_row(r)]
    if not visible_rows:
        return "0x0"
    n_cols = max(
        sum(1 for c in r.children
            if c.kind == "cell" and not c.attrs.get("covered"))
        for r in visible_rows
    )
    return f"{len(visible_rows)}x{n_cols}"


def _simplify(node: DocNode) -> dict[str, Any]:
    """Recursive node → dict.  Emits ``kind`` first, then per-kind shape
    attrs, then ``text``, then ``children``.  Insertion order is preserved
    when ``json.dumps`` is called without ``sort_keys``."""
    out: dict[str, Any] = {"kind": node.kind}

    # Per-kind shape attributes only — never bbox / provenance / id.
    if node.kind == "page":
        bb = node.bbox if isinstance(node.bbox, BBox) else node.bbox[0]
        out["index"] = bb.page
    elif node.kind == "heading":
        out["level"] = int(node.attrs.get("level", 1))
    elif node.kind == "table":
        out["shape"] = _visible_table_shape(node)
        if isinstance(node.bbox, list):
            out["spans_pages"] = sorted({b.page for b in node.bbox})
    elif node.kind == "cell":
        for k in ("colspan", "rowspan"):
            v = node.attrs.get(k)
            if v and v > 1:
                out[k] = int(v)
    elif node.kind == "figure":
        path = node.attrs.get("path")
        if path:
            out["path"] = path

    if node.text:
        out["text"] = node.text

    # Children: filter parser-artifact rows/cells out of tables.
    children: list[dict[str, Any]] = []
    if node.kind == "table":
        for r in node.children:
            if _is_placeholder_row(r):
                continue
            children.append(_simplify(r))
    elif node.kind == "row":
        for c in node.children:
            if c.kind == "cell" and c.attrs.get("covered"):
                continue
            children.append(_simplify(c))
    else:
        for c in node.children:
            children.append(_simplify(c))

    if children:
        out["children"] = children
    return out


def to_tree_json(tree: DocNode, indent: int | None = 2) -> str:
    """Dump the document as a simplified hierarchy.

    Drops bbox, provenance, id, and ``attrs`` not relevant to content shape.
    Drops rowspan-placeholder rows and rowspan-covered cells (which carry
    no semantic content of their own).
    """
    return json.dumps(_simplify(tree), indent=indent, ensure_ascii=False)
