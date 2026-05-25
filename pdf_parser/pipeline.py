"""Pipeline orchestrator: PDF path → DocNode tree.

Stages 1–6 are pure and deterministic. Stage 7 (validate) is run separately by
the caller via `pdf_parser.validate`. Stage 8 (render) is per-format.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.build_tree import build_tree
from pdf_parser.stages.extract_tables import extract_tables
from pdf_parser.stages.ingest import ingest
from pdf_parser.stages.segment import segment
from pdf_parser.stages.stitch_pages import stitch_tables

if TYPE_CHECKING:
    from pdf_parser.fallback.llm import LLMFallback


def _has_leaf_text(node: DocNode) -> bool:
    """Return True if any descendant carries non-empty text."""
    stack = [node]
    while stack:
        n = stack.pop()
        if n.text:
            return True
        stack.extend(n.children)
    return False


def _apply_llm_fallback(
    tree: DocNode,
    pdf_path: Path,
    fb: "LLMFallback",
    raw_pages: list,
) -> DocNode:
    """Replace empty page nodes with LLM-extracted content."""
    from pdf_parser.fallback.llm import fallback_for_region  # lazy: keeps anthropic optional

    new_pages: list[DocNode] = []
    for page_node in tree.children:
        if not _has_leaf_text(page_node):
            page_idx = page_node.bbox.page
            if page_idx >= len(raw_pages):  # should never happen; indicates build_tree/ingest mismatch
                raise IndexError(
                    f"page_idx={page_idx} out of range for raw_pages (len={len(raw_pages)})"
                )
            width  = raw_pages[page_idx].width
            height = raw_pages[page_idx].height
            region = BBox(page=page_idx, x0=0, y0=0, x1=width, y1=height)
            fallback_page = fallback_for_region(fb, pdf_path, region)
            if fallback_page is not None:
                new_pages.append(DocNode(
                    kind="page",
                    bbox=page_node.bbox,
                    children=fallback_page.children,
                    attrs=page_node.attrs,
                    provenance={**page_node.provenance, "llm_fallback": True},
                ))
                continue
        new_pages.append(page_node)
    return DocNode(
        kind="document",
        bbox=tree.bbox,
        children=new_pages,
        attrs=tree.attrs,
        provenance=tree.provenance,
    )


def parse(
    pdf_path: Path | str,
    llm_fallback: Optional["LLMFallback"] = None,
    table_detector: str = "legacy",
) -> DocNode:
    """Parse ``pdf_path`` and return the document tree.

    ``table_detector``:

    * ``"legacy"`` (default) — current ``detect_tables`` cascade only.
    * ``"experimental"`` — legacy cascade **plus** the column-anchor detector
      (see :mod:`pdf_parser.stages.detect_tables_anchor`).  Anchor candidates
      that overlap any legacy table are dropped, so this is purely additive
      on fixtures where the legacy cascade is already correct.  Use it to
      recover borderless tables with long-text cells that the legacy
      ``_MAX_CELL_TEXT_CHARS`` heuristic rejects.
    """
    pdf_path = Path(pdf_path)
    raw_pages = ingest(pdf_path)
    segments  = segment(raw_pages)
    tables    = stitch_tables(extract_tables(pdf_path))

    if table_detector == "experimental":
        from pdf_parser.stages.detect_tables_anchor import augment_with_anchor_tables
        tables = augment_with_anchor_tables(tables, pdf_path)
    elif table_detector != "legacy":
        raise ValueError(
            f"unknown table_detector: {table_detector!r} (expected 'legacy' or 'experimental')"
        )

    tree      = build_tree(segments, tables)

    if llm_fallback is not None and llm_fallback.enabled:
        tree = _apply_llm_fallback(tree, pdf_path, llm_fallback, raw_pages)

    return tree
