"""Render bbox overlays onto each page of a PDF for human spot-checking."""

from __future__ import annotations

from pathlib import Path

import pymupdf
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


def _walk(node: DocNode):
    yield node
    for c in node.children:
        yield from _walk(c)


def _bboxes(node: DocNode) -> list[BBox]:
    return node.bbox if isinstance(node.bbox, list) else [node.bbox]


def render_overlays(pdf_path: Path, tree: DocNode, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open(str(pdf_path))
    try:
        for page_index, page in enumerate(doc):
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            draw = ImageDraw.Draw(img)
            scale_x = pix.width / page.rect.width
            scale_y = pix.height / page.rect.height
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
            img.save(out_dir / f"page_{page_index:03d}.png")
    finally:
        doc.close()
