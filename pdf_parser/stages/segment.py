"""Stage 2: cluster spans into blocks; tag heading/paragraph/list candidates."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Literal

from pdf_parser.model import BBox
from pdf_parser.stages.ingest import PageRaw, Span

BlockKind = Literal["heading", "paragraph", "list_item", "unknown"]

LIST_BULLETS = ("•", "-", "*", "◦", "▪")


@dataclass
class Block:
    bbox: BBox
    text: str
    kind_hint: BlockKind
    spans: list[Span] = field(default_factory=list)
    level: int = 0  # heading level guess, 1=biggest


@dataclass
class PageSegmented:
    index: int
    width: float
    height: float
    blocks: list[Block] = field(default_factory=list)


def _line_key(span: Span) -> int:
    # Group spans by line: bucket by 2pt rounded y-center.
    return round((span.bbox.y0 + span.bbox.y1) / 2 / 2)


def _join_text(spans: list[Span]) -> str:
    return " ".join(s.text.strip() for s in spans).strip()


def _line_bbox(spans: list[Span]) -> BBox:
    return BBox(
        page=spans[0].bbox.page,
        x0=min(s.bbox.x0 for s in spans),
        y0=min(s.bbox.y0 for s in spans),
        x1=max(s.bbox.x1 for s in spans),
        y1=max(s.bbox.y1 for s in spans),
    )


def _segment_page(page: PageRaw) -> PageSegmented:
    if not page.spans:
        return PageSegmented(index=page.index, width=page.width, height=page.height)

    # Group into lines.
    lines: dict[int, list[Span]] = {}
    for s in page.spans:
        lines.setdefault(_line_key(s), []).append(s)
    sorted_lines = [
        sorted(line, key=lambda s: s.bbox.x0)
        for _, line in sorted(lines.items())
    ]

    # Determine body font size as the median; anything notably larger = heading.
    sizes = [s.font_size for line in sorted_lines for s in line]
    body_size = statistics.median(sizes) if sizes else 0.0

    blocks: list[Block] = []
    for line in sorted_lines:
        text = _join_text(line)
        if not text:
            continue
        avg_size = statistics.mean(s.font_size for s in line)
        is_bold = all(s.bold for s in line)
        bbox = _line_bbox(line)

        if avg_size > body_size * 1.15 or (is_bold and avg_size >= body_size):
            kind: BlockKind = "heading"
            # Bigger size → smaller level number (h1 > h2 > ...).
            level = 1 if avg_size > body_size * 1.6 else 2 if avg_size > body_size * 1.3 else 3
        elif text.lstrip().startswith(LIST_BULLETS):
            kind = "list_item"
            level = 0
        else:
            kind = "paragraph"
            level = 0

        blocks.append(Block(bbox=bbox, text=text, kind_hint=kind, spans=line, level=level))

    return PageSegmented(index=page.index, width=page.width, height=page.height, blocks=blocks)


def segment(pages: list[PageRaw]) -> list[PageSegmented]:
    return [_segment_page(p) for p in pages]
