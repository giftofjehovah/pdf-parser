"""Render final-tree bbox overlays onto each page of a PDF for human spot-checking.

Thin shim over :mod:`pdf_parser.overlay`.  Kept as a script entry point so the
existing ``--visualize`` CLI flag (and the ``scripts.visualize.render_overlays``
test contract) continues to work unchanged.

Output mode is chosen by the destination path:

  * a path ending in ``.pdf`` writes a single multi-page debug PDF;
  * any other path is treated as a directory and gets one PNG per page
    (``page_000.png``, ``page_001.png``, ...).
"""

from __future__ import annotations

from pathlib import Path

from pdf_parser.model import DocNode
from pdf_parser.overlay import (
    annotations_from_tree, render_overlay_pdf, render_overlay_pngs,
)


def render_overlays(pdf_path: Path, tree: DocNode, out: Path) -> None:
    out = Path(out)
    annotations = annotations_from_tree(tree)
    if out.suffix.lower() == ".pdf":
        render_overlay_pdf(pdf_path, annotations, out)
    else:
        render_overlay_pngs(pdf_path, annotations, out)
