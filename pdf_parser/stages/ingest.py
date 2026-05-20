"""Stage 1: pymupdf-based ingest. Pure: PDF path → PageRaw list."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pdf_parser.model import BBox

# Images smaller than this in either dimension are decorative artefacts (rules,
# bullets, background fills) and are not surfaced as figure nodes.
_MIN_IMAGE_PT = 10.0


@dataclass(frozen=True)
class Span:
    text: str
    bbox: BBox
    font_name: str
    font_size: float
    bold: bool
    italic: bool


@dataclass(frozen=True)
class ImageInfo:
    """Embedded image found on a PDF page."""
    bbox: BBox
    xref: int    # pymupdf cross-reference index; used to extract bytes later
    width: int   # pixel width of the original image
    height: int  # pixel height of the original image


@dataclass
class PageRaw:
    index: int
    width: float
    height: float
    spans: list[Span] = field(default_factory=list)
    drawings: list[dict] = field(default_factory=list)
    images: list[ImageInfo] = field(default_factory=list)


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
            seen_xrefs: set[int] = set()
            for img in page.get_image_info(xrefs=True):
                bb = img.get("bbox")
                xref = img.get("xref", 0)
                if not bb or not xref:
                    continue
                x0, y0, x1, y1 = bb
                if (x1 - x0) < _MIN_IMAGE_PT or (y1 - y0) < _MIN_IMAGE_PT:
                    continue
                if xref in seen_xrefs:
                    continue  # same image referenced multiple times on this page
                seen_xrefs.add(xref)
                raw.images.append(ImageInfo(
                    bbox=BBox(page=idx, x0=x0, y0=y0, x1=x1, y1=y1),
                    xref=xref,
                    width=img.get("width", 0),
                    height=img.get("height", 0),
                ))
            pages.append(raw)
    finally:
        doc.close()
    return pages
