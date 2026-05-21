"""Stage 1: ingest. Pure: PDF path → PageRaw list.

Text extraction uses pypdfium2 (via PDFium) for correct Unicode decoding.
Image extraction uses pdfplumber (via pdfminer) for stream-level access.
Both are production dependencies via the pdfplumber package.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw

from pdf_parser.model import BBox

# Images smaller than this in either dimension are decorative artefacts (rules,
# bullets, background fills) and are not surfaced as figure nodes.
_MIN_IMAGE_PT = 10.0

# Characters with these text values from the PDF text stream are line/paragraph
# separators, not real glyphs — skip them.
_SKIP_CHARS = frozenset({'\r', '\n', '\x00', '\ufffe', '\uffff'})


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
    image_id: int   # sequential unique ID; stable across ingest and render passes
    width: int      # pixel width of the source image
    height: int     # pixel height of the original image


@dataclass
class PageRaw:
    index: int
    width: float
    height: float
    spans: list[Span] = field(default_factory=list)
    drawings: list[dict] = field(default_factory=list)  # retained for API compatibility; not populated
    images: list[ImageInfo] = field(default_factory=list)


def _is_bold(font_name: str) -> bool:
    n = font_name.lower()
    return "bold" in n or "heavy" in n


def _is_italic(font_name: str) -> bool:
    n = font_name.lower()
    return "italic" in n or "oblique" in n


# ---------------------------------------------------------------------------
# pypdfium2-based text extraction
# ---------------------------------------------------------------------------

def _extract_text_spans(
    textpage: pdfium.PdfTextPage,
    page_height: float,
    page_idx: int,
) -> list[Span]:
    """Extract Span objects from a PDFium text page.

    Uses PDFium's text rect grouping (its own line-breaking logic) to assign
    characters to visual lines, then splits by font run within each line.
    The raw C API is used for per-character font name/size, which the high-level
    pypdfium2 wrapper does not expose.

    Coordinates are in PDF bottom-origin space internally; bboxes in the returned
    Spans use top-origin (0 at top of page), matching the rest of the pipeline.
    """
    raw_tp = textpage.raw
    n = textpage.count_chars()
    if not n:
        return []

    # ---- collect per-character data -----------------------------------------
    chars: list[dict] = []
    for i in range(n):
        char_text = textpage.get_text_range(i, 1)
        if char_text in _SKIP_CHARS or not char_text:
            continue

        # get_charbox → (left, bottom, right, top) in PDF coords (y from bottom).
        box = textpage.get_charbox(i, loose=False)
        x0, pdf_bottom, x1, pdf_top = box[0], box[1], box[2], box[3]

        font_size = float(pdfium_raw.FPDFText_GetFontSize(raw_tp, i) or 0.0)
        if font_size < 0.1:
            continue

        buf = ctypes.create_string_buffer(256)
        flags_c = ctypes.c_int(0)
        pdfium_raw.FPDFText_GetFontInfo(raw_tp, i, buf, 256, ctypes.byref(flags_c))
        font_name = buf.value.decode("utf-8", errors="replace")

        chars.append({
            "text": char_text,
            "x0": x0,
            "x1": x1,
            # PDF-native coordinates for line assignment and top-origin conversion.
            "pdf_bottom": pdf_bottom,
            "pdf_top": pdf_top,
            "page_height": page_height,
            "fontname": font_name,
            "size": font_size,
        })

    if not chars:
        return []

    # ---- assign each char to a PDFium line rect ------------------------------
    # PDFium's count_rects / get_rect gives us its own line grouping, which
    # correctly handles descenders (their bboxes extend below the baseline).
    n_rects = textpage.count_rects(0, n)
    # rects: list of (pdf_bottom, pdf_top, rect_index) sorted top-to-bottom on page
    rects: list[tuple[float, float, int]] = []
    for ri in range(n_rects):
        r = textpage.get_rect(ri)       # (left, pdf_bottom, right, pdf_top)
        rects.append((r[1], r[3], ri))  # (pdf_bottom, pdf_top, ri)
    # Sort by pdf_top descending (top of page first).
    rects.sort(key=lambda r: -r[1])

    def _assign_rect(c: dict) -> int:
        """Return the rect index this character belongs to."""
        cy = (c["pdf_bottom"] + c["pdf_top"]) / 2  # char y-centre in PDF coords
        for pdf_b, pdf_t, ri in rects:
            if pdf_b - 2.0 <= cy <= pdf_t + 2.0:
                return ri
        # Fallback: use the closest rect.
        return min(rects, key=lambda r: abs((r[0] + r[1]) / 2 - cy))[2]

    for c in chars:
        c["line_rect"] = _assign_rect(c)

    # ---- group chars by line rect, then by font run -------------------------
    # line_order maps rect_index → sort key for reading order (top-to-bottom).
    line_order = {ri: idx for idx, (_, _, ri) in enumerate(rects)}

    # Sort all chars: primary = line order, secondary = x0.
    chars.sort(key=lambda c: (line_order.get(c["line_rect"], 9999), c["x0"]))

    return _group_chars_into_spans(chars, page_idx)


def _group_chars_into_spans(chars: list[dict], page_idx: int) -> list[Span]:
    """Convert character dicts into font-run Span objects.

    Chars are pre-sorted in reading order (by line_rect then x0).  Within each
    line, chars are split into spans by font run (same fontname + size).
    A large horizontal gap (> 0.5× font size) also breaks a span — handles
    multi-column layouts where two columns share a line rect.
    """
    if not chars:
        return []

    spans: list[Span] = []
    group: list[dict] = []

    def _flush(grp: list[dict]) -> None:
        if not grp:
            return
        text = "".join(c["text"] for c in grp)
        if not text.strip():
            return
        fn = grp[0]["fontname"]
        fs = float(grp[0]["size"])
        # Convert PDF-native coords to top-origin for the span bbox.
        ph = grp[0]["page_height"]
        spans.append(Span(
            text=text,
            bbox=BBox(
                page=page_idx,
                x0=min(c["x0"] for c in grp),
                y0=ph - max(c["pdf_top"] for c in grp),
                x1=max(c["x1"] for c in grp),
                y1=ph - min(c["pdf_bottom"] for c in grp),
            ),
            font_name=fn,
            font_size=fs,
            bold=_is_bold(fn),
            italic=_is_italic(fn),
        ))

    for c in chars:
        if not group:
            group.append(c)
            continue
        prev = group[-1]
        same_line = c.get("line_rect") == prev.get("line_rect")
        same_font = (
            c["fontname"] == prev["fontname"]
            and abs(c["size"] - prev["size"]) < 0.1
        )
        # Break on a large horizontal gap to handle multi-column layouts.
        # 0.5× font size exceeds word spacing (0–0.3 em) but stays below
        # typical inter-column gaps (≥ 1× font size).
        x_gap = c["x0"] - prev["x1"]
        same_flow = x_gap <= prev["size"] * 0.5
        if same_line and same_font and same_flow:
            group.append(c)
        else:
            _flush(group)
            group = [c]

    _flush(group)
    return spans


# ---------------------------------------------------------------------------
# pdfplumber-based image extraction
# ---------------------------------------------------------------------------

def _extract_images(
    plumber_pages,
    stream_to_id: dict[int, int],
    seen_page_streams: set[tuple[int, int]],
    image_counter_ref: list[int],
    page_idx: int,
) -> list[ImageInfo]:
    """Extract ImageInfo objects from a pdfplumber page."""
    images: list[ImageInfo] = []
    page = plumber_pages[page_idx]
    for img in page.images:
        bb_x0 = img.get("x0")
        bb_top = img.get("top")
        bb_x1 = img.get("x1")
        bb_bottom = img.get("bottom")
        if bb_x0 is None or bb_top is None or bb_x1 is None or bb_bottom is None:
            continue
        if (bb_x1 - bb_x0) < _MIN_IMAGE_PT or (bb_bottom - bb_top) < _MIN_IMAGE_PT:
            continue
        stream = img.get("stream")
        if stream is None:
            continue

        sid = id(stream)
        # Deduplicate: same XObject placed multiple times on this page.
        if (page_idx, sid) in seen_page_streams:
            continue
        seen_page_streams.add((page_idx, sid))

        # Assign a stable image_id; same physical image → same ID across pages.
        if sid not in stream_to_id:
            stream_to_id[sid] = image_counter_ref[0]
            image_counter_ref[0] += 1
        image_id = stream_to_id[sid]

        # srcsize = (pixel_width, pixel_height) of the source image.
        srcsize = img.get("srcsize") or (img.get("width", 0), img.get("height", 0))
        images.append(ImageInfo(
            bbox=BBox(page=page_idx, x0=bb_x0, y0=bb_top, x1=bb_x1, y1=bb_bottom),
            image_id=image_id,
            width=int(srcsize[0]),
            height=int(srcsize[1]),
        ))
    return images


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def ingest(pdf_path: Path) -> list[PageRaw]:
    path_str = str(pdf_path)
    pages: list[PageRaw] = []

    stream_to_id: dict[int, int] = {}
    image_counter_ref: list[int] = [0]  # mutable counter via list indirection
    seen_page_streams: set[tuple[int, int]] = set()

    pdfium_doc = pdfium.PdfDocument(path_str)
    try:
        with pdfplumber.open(path_str) as plumber_doc:
            for idx in range(len(pdfium_doc)):
                pdfium_page = pdfium_doc[idx]
                page_width = pdfium_page.get_width()
                page_height = pdfium_page.get_height()

                raw = PageRaw(
                    index=idx,
                    width=float(page_width),
                    height=float(page_height),
                )

                # Text extraction via pypdfium2 (correct Unicode, font info).
                textpage = pdfium_page.get_textpage()
                raw.spans = _extract_text_spans(textpage, page_height, idx)

                # Image extraction via pdfplumber (stream-level access).
                raw.images = _extract_images(
                    plumber_doc.pages,
                    stream_to_id,
                    seen_page_streams,
                    image_counter_ref,
                    idx,
                )
                pages.append(raw)
    finally:
        pdfium_doc.close()

    return pages
