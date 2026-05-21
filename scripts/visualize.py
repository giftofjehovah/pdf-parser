"""Render bbox overlays onto each page of a PDF for human spot-checking.

Output mode is chosen by the destination path:

  * a path ending in ``.pdf`` writes a single multi-page debug PDF;
  * any other path is treated as a directory and gets one PNG per page
    (``page_000.png``, ``page_001.png``, ...).
"""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageDraw

from pdf_parser.model import BBox, DocNode

KIND_COLORS = {
    "heading": (200, 30, 30),
    "paragraph": (30, 100, 200),
    "table": (30, 180, 30),
    "row": (180, 180, 30),
    "cell": (180, 100, 180),
    "list": (100, 30, 180),
    "list_item": (100, 30, 180),
    "figure": (200, 120, 30),
}

_DPI = 150
_SCALE = _DPI / 72  # PDF points → pixels


def _walk(node: DocNode):
    yield node
    for c in node.children:
        yield from _walk(c)


def _bboxes(node: DocNode) -> list[BBox]:
    return node.bbox if isinstance(node.bbox, list) else [node.bbox]


def _render_page(page: pdfium.PdfPage, page_index: int, tree: DocNode) -> Image.Image:
    bitmap = page.render(scale=_SCALE)
    img = bitmap.to_pil()
    draw = ImageDraw.Draw(img)
    scale_x = img.width / page.get_width()
    scale_y = img.height / page.get_height()
    for node in _walk(tree):
        color = KIND_COLORS.get(node.kind)
        if color is None:
            continue
        for b in _bboxes(node):
            if b.page != page_index:
                continue
            draw.rectangle(
                [b.x0 * scale_x, b.y0 * scale_y, b.x1 * scale_x, b.y1 * scale_y],
                outline=color, width=2,
            )
    return img


def render_overlays(pdf_path: Path, tree: DocNode, out: Path) -> None:
    out = Path(out)
    as_pdf = out.suffix.lower() == ".pdf"
    if as_pdf:
        out.parent.mkdir(parents=True, exist_ok=True)
    else:
        out.mkdir(parents=True, exist_ok=True)

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        if as_pdf:
            pages = [_render_page(doc[idx], idx, tree) for idx in range(len(doc))]
            if not pages:
                raise ValueError(f"{pdf_path} has no pages to render")
            pages[0].save(out, save_all=True, append_images=pages[1:])
        else:
            for idx in range(len(doc)):
                _render_page(doc[idx], idx, tree).save(out / f"page_{idx:03d}.png")
    finally:
        doc.close()
