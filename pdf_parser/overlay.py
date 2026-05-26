"""Render bbox overlays onto each page of a PDF for human spot-checking.

This module is the rendering primitive: it takes a PDF path and a list of
:class:`Annotation` records (each one bbox + color), rasterises every page,
and draws the boxes on top.  Callers decide what to put in the annotation
list — final-tree kinds for production visualisation, per-stage records
for debug bundles.

Color palettes for each stage live here too so the README in a debug
bundle can document them from one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from pdf_parser.model import BBox, DocNode

# --- color palettes -------------------------------------------------------
# Each stage gets its own palette so an overlay can be read without
# cross-referencing the others.  Colors are deliberately distinct
# (max-perceptual-distance) and consistent with the final-tree palette
# wherever the same concept reappears (heading is always red, etc.).

TREE_KIND_COLORS: dict[str, tuple[int, int, int]] = {
    "heading":   (200, 30, 30),
    "paragraph": (30, 100, 200),
    "table":     (30, 180, 30),
    "row":       (180, 180, 30),
    "cell":      (180, 100, 180),
    "list":      (100, 30, 180),
    "list_item": (100, 30, 180),
    "figure":    (200, 120, 30),
}

INGEST_COLORS: dict[str, tuple[int, int, int]] = {
    "span":  (30, 150, 200),    # cyan
    "image": (200, 120, 30),    # orange
}

SEGMENT_COLORS: dict[str, tuple[int, int, int]] = {
    "heading":   (200, 30, 30),
    "paragraph": (30, 100, 200),
    "list_item": (100, 30, 180),
    "unknown":   (120, 120, 120),
}

CELL_SOURCE_COLORS: dict[str, tuple[int, int, int]] = {
    "line":   (30, 180, 30),     # green   — bounded by visible edges (highest trust)
    "gutter": (220, 180, 30),    # yellow  — bounded by whitespace gutters
    "text":   (220, 60, 180),    # magenta — text-strategy fallback (lowest trust)
}

# --- core renderer --------------------------------------------------------


@dataclass(frozen=True)
class Annotation:
    """One bbox + color to draw on the overlay."""
    bbox: BBox
    color: tuple[int, int, int]


_DPI = 110           # debug overlays don't need print-grade quality; smaller files
_SCALE = _DPI / 72   # PDF points → pixels


def _render_page(
    page: pdfium.PdfPage,
    page_index: int,
    by_page: dict[int, list[Annotation]],
) -> Image.Image:
    bitmap = page.render(scale=_SCALE)
    img = bitmap.to_pil().convert("RGB")
    draw = ImageDraw.Draw(img)
    sx = img.width / page.get_width()
    sy = img.height / page.get_height()
    for a in by_page.get(page_index, ()):
        draw.rectangle(
            (a.bbox.x0 * sx, a.bbox.y0 * sy, a.bbox.x1 * sx, a.bbox.y1 * sy),
            outline=a.color, width=2,
        )
    return img


def _index_by_page(annotations: Iterable[Annotation]) -> dict[int, list[Annotation]]:
    out: dict[int, list[Annotation]] = {}
    for a in annotations:
        out.setdefault(a.bbox.page, []).append(a)
    return out


def render_overlay_pdf(
    pdf_path: Path, annotations: Iterable[Annotation], out_path: Path,
) -> None:
    """Render *pdf_path* to a single multi-page PDF with boxes overlaid per page."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    by_page = _index_by_page(annotations)
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        pages = [_render_page(doc[i], i, by_page) for i in range(len(doc))]
        if not pages:
            raise ValueError(f"{pdf_path} has no pages to render")
        pages[0].save(out_path, save_all=True, append_images=pages[1:])
    finally:
        doc.close()


def render_overlay_pngs(
    pdf_path: Path, annotations: Iterable[Annotation], out_dir: Path,
) -> None:
    """Render *pdf_path* as ``page_NNN.png`` files into *out_dir*."""
    out_dir.mkdir(parents=True, exist_ok=True)
    by_page = _index_by_page(annotations)
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        for i in range(len(doc)):
            _render_page(doc[i], i, by_page).save(out_dir / f"page_{i:03d}.png")
    finally:
        doc.close()


# --- helpers that build annotation lists from pipeline objects -----------

def annotations_from_tree(tree: DocNode) -> list[Annotation]:
    """Annotations for a final DocNode tree, colored by ``DocNode.kind``."""
    out: list[Annotation] = []
    stack = [tree]
    while stack:
        n = stack.pop()
        stack.extend(n.children)
        color = TREE_KIND_COLORS.get(n.kind)
        if color is None:
            continue
        bboxes = n.bbox if isinstance(n.bbox, list) else [n.bbox]
        for b in bboxes:
            out.append(Annotation(bbox=b, color=color))
    return out
