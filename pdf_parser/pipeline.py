"""Pipeline orchestrator: PDF path → DocNode tree.

Stages 1–6 are pure and deterministic. Stage 7 (validate) is run separately by
the caller via `pdf_parser.validate`. Stage 8 (render) is per-format.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pdfplumber

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.build_tree import build_tree
from pdf_parser.stages.detect_tables_anchor import augment_with_anchor_tables
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
    *,
    use_anchor: bool = True,
    use_bottom_up: bool = True,
) -> DocNode:
    """Parse ``pdf_path`` and return the document tree.

    ``use_bottom_up`` (default ``True``) selects the bottom-up
    cell-clustering extractor (:mod:`pdf_parser.stages.extract_tables_v2`).
    The bottom-up path produces one ``detect_cells`` primitive
    (line / gutter / text evidence) feeding one ``aggregate_tables``
    clusterer that emits the same per-page ``DocNode(kind="table")`` tree
    the downstream :mod:`stitch_pages` + :mod:`build_tree` stages consume.

    ``use_anchor`` (default ``True``) is retained for the legacy cascade
    only; ignored when ``use_bottom_up=True`` (bottom-up subsumes the
    anchor detector's borderless-table recovery).  Pass
    ``use_bottom_up=False`` to fall back to the legacy
    ``detect_tables → extract_tables → augment_with_anchor`` pipeline; in
    that mode ``use_anchor=False`` further disables the anchor overlay.
    """
    pdf_path = Path(pdf_path)
    raw_pages = ingest(pdf_path)
    segments  = segment(raw_pages)

    with pdfplumber.open(str(pdf_path)) as pdf:
        if use_bottom_up:
            from pdf_parser.stages.extract_tables_v2 import extract_tables as extract_tables_v2
            tables = extract_tables_v2(pdf_path, pdf=pdf)
        else:
            tables = extract_tables(pdf_path, pdf=pdf)
            if use_anchor:
                tables = augment_with_anchor_tables(tables, pdf_path, pdf=pdf)

    tables = stitch_tables(tables)
    tree   = build_tree(segments, tables)

    if llm_fallback is not None and llm_fallback.enabled:
        tree = _apply_llm_fallback(tree, pdf_path, llm_fallback, raw_pages)

    return tree
