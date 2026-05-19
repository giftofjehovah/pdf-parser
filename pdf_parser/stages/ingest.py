"""Stage 1: pymupdf-based ingest. Pure: PDF path → PageRaw list."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pdf_parser.model import BBox


@dataclass(frozen=True)
class Span:
    text: str
    bbox: BBox
    font_name: str
    font_size: float
    bold: bool
    italic: bool


@dataclass
class PageRaw:
    index: int
    width: float
    height: float
    spans: list[Span] = field(default_factory=list)
    drawings: list[dict] = field(default_factory=list)
    images: list[BBox] = field(default_factory=list)


def ingest(pdf_path: Path) -> list[PageRaw]:
    doc = pymupdf.open(str(pdf_path))
    pages: list[PageRaw] = []
    try:
        for idx, page in enumerate(doc):
            raw = PageRaw(index=idx, width=page.rect.width, height=page.rect.height)
            text_dict = page.get_text("dict")
            for block in text_dict.get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span.get("text", "")
                        if not text.strip():
                            continue
                        x0, y0, x1, y1 = span["bbox"]
                        raw.spans.append(Span(
                            text=text,
                            bbox=BBox(page=idx, x0=x0, y0=y0, x1=x1, y1=y1),
                            font_name=span.get("font", ""),
                            font_size=float(span.get("size", 0.0)),
                            bold=bool(span.get("flags", 0) & 16),
                            italic=bool(span.get("flags", 0) & 2),
                        ))
            raw.drawings = page.get_drawings()
            for img in page.get_image_info():
                bb = img.get("bbox")
                if bb:
                    raw.images.append(BBox(page=idx, x0=bb[0], y0=bb[1], x1=bb[2], y1=bb[3]))
            pages.append(raw)
    finally:
        doc.close()
    return pages
