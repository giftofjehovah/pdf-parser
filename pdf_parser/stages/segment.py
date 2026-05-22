"""Stage 2: cluster spans into blocks; tag heading/paragraph/list candidates."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Literal

from pdf_parser.model import BBox
from pdf_parser.stages.ingest import ImageInfo, PageRaw, Span

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
    images: list[ImageInfo] = field(default_factory=list)


def _line_key(span: Span) -> int:
    # Group spans by line: bucket by 2pt rounded y-center.
    return round((span.bbox.y0 + span.bbox.y1) / 2 / 2)


def _split_columns(spans: list[Span], col_gap: float) -> list[list[Span]]:
    """Split same-line spans into separate column groups at large horizontal gaps.

    Two-column layouts produce spans at the same y-position.  Without this split
    they would be joined into a single block, mixing text from both columns.
    *col_gap* is the minimum x-gap (pt) between span x1 and the next span x0
    that signals a column boundary rather than normal inter-word spacing.
    """
    if not spans:
        return []
    ordered = sorted(spans, key=lambda s: s.bbox.x0)
    groups: list[list[Span]] = [[ordered[0]]]
    for s in ordered[1:]:
        gap = s.bbox.x0 - groups[-1][-1].bbox.x1
        if gap > col_gap:
            groups.append([s])
        else:
            groups[-1].append(s)
    return groups



def _absorb_dangling_bullets(blocks: list[Block]) -> list[Block]:
    """Merge lone bullet blocks into the adjacent paragraph on the same line.

    Some PDF generators (e.g. reportlab's ListFlowable) place the bullet glyph
    as a separate text object at a slightly different y-position or x-position
    from the body text, causing the two to land in separate blocks.  Two cases:

    * Bullet before text (bullet x0 < text x0, sorted by x0): the bullet block
      appears first; we look forward and merge with the next block.
    * Bullet after text (y-center slightly above text y-center): the text block
      appears first; we merge the following bullet into the previous block.
    """
    if not blocks:
        return blocks
    out: list[Block] = []
    i = 0
    while i < len(blocks):
        b = blocks[i]
        if b.kind_hint == "list_item" and b.text.strip() in LIST_BULLETS:
            bull_cy = (b.bbox.y0 + b.bbox.y1) / 2

            # Case A: bullet precedes text (bullet x0 < text x0).
            if i + 1 < len(blocks):
                nxt = blocks[i + 1]
                nxt_cy = (nxt.bbox.y0 + nxt.bbox.y1) / 2
                if abs(nxt_cy - bull_cy) <= 10.0 and nxt.bbox.x0 > b.bbox.x0:
                    merged_bbox = BBox(
                        page=b.bbox.page,
                        x0=b.bbox.x0,
                        y0=min(b.bbox.y0, nxt.bbox.y0),
                        x1=nxt.bbox.x1,
                        y1=max(b.bbox.y1, nxt.bbox.y1),
                    )
                    out.append(Block(
                        bbox=merged_bbox,
                        text=b.text.strip() + " " + nxt.text,
                        kind_hint="list_item",
                        spans=b.spans + nxt.spans,
                        level=0,
                    ))
                    i += 2
                    continue

            # Case B: text precedes bullet (text appeared first due to y-sort).
            if out:
                prev = out[-1]
                prev_cy = (prev.bbox.y0 + prev.bbox.y1) / 2
                if abs(prev_cy - bull_cy) <= 10.0 and prev.bbox.x0 > b.bbox.x0:
                    merged_bbox = BBox(
                        page=prev.bbox.page,
                        x0=b.bbox.x0,
                        y0=min(b.bbox.y0, prev.bbox.y0),
                        x1=prev.bbox.x1,
                        y1=max(b.bbox.y1, prev.bbox.y1),
                    )
                    out[-1] = Block(
                        bbox=merged_bbox,
                        text=b.text.strip() + " " + prev.text,
                        kind_hint="list_item",
                        spans=b.spans + prev.spans,
                        level=0,
                    )
                    i += 1
                    continue

        out.append(b)
        i += 1
    return out

def _join_wrapped_lines(blocks: list[Block], body_size: float) -> list[Block]:
    """Merge visual lines that continue the preceding paragraph or list_item.

    The segmenter emits one Block per visual line.  Wrapped body text therefore
    arrives as N adjacent blocks that all belong to the same logical paragraph
    (or to the same list_item if the predecessor is a bullet).  This pass walks
    the blocks in order and folds each "continuation" line into the block above
    it when all of the following hold:

      - the current block is a paragraph (no bullet);
      - the previous block is a paragraph or list_item;
      - the vertical gap between them is ≤ 1× body_size (i.e. they're on
        consecutive printed lines, not separated by a paragraph break);
      - the current block's x0 is aligned with the previous block's text
        start: equal x0 within ~3 pt for paragraph→paragraph, or aligned with
        the bullet's text-start indent for paragraph→list_item.

    Headings, lone bullet glyphs, and column-split fragments are left alone.
    """
    if not blocks:
        return blocks

    Y_TOL = max(body_size * 1.0, 6.0)
    X_TOL = 3.0

    def _text_start_x(b: Block) -> float:
        """For a list_item, x0 of the first non-bullet span (i.e. where the
        wrapped continuation should align).  For other kinds, just b.bbox.x0."""
        if b.kind_hint != "list_item":
            return b.bbox.x0
        for s in b.spans:
            t = s.text.strip()
            if t and t not in LIST_BULLETS and not (
                len(t) == 1 and t in LIST_BULLETS
            ):
                # Skip a span that is *only* a bullet character (already covered
                # by the tuple check above, but also catches " • " etc.).
                if t.lstrip().startswith(LIST_BULLETS):
                    continue
                return s.bbox.x0
        return b.bbox.x0

    out: list[Block] = [blocks[0]]
    for b in blocks[1:]:
        prev = out[-1]
        gap = b.bbox.y0 - prev.bbox.y1
        prev_anchor_x = _text_start_x(prev)
        x_aligned = abs(b.bbox.x0 - prev_anchor_x) <= X_TOL
        can_merge = (
            b.kind_hint == "paragraph"
            and prev.kind_hint in ("paragraph", "list_item")
            and 0 <= gap <= Y_TOL
            and x_aligned
        )
        if can_merge:
            merged_bbox = BBox(
                page=prev.bbox.page,
                x0=min(prev.bbox.x0, b.bbox.x0),
                y0=min(prev.bbox.y0, b.bbox.y0),
                x1=max(prev.bbox.x1, b.bbox.x1),
                y1=max(prev.bbox.y1, b.bbox.y1),
            )
            out[-1] = Block(
                bbox=merged_bbox,
                text=prev.text + " " + b.text,
                kind_hint=prev.kind_hint,
                spans=prev.spans + b.spans,
                level=prev.level,
            )
        else:
            out.append(b)
    return out


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

    # Column-gap threshold: at least 1× the body font size.
    # Two-column layouts typically have gutters of 10–30 pt; normal inter-word
    # space (already absorbed into spans by ingest) is at most a few points.
    col_gap = max(body_size, 8.0)

    # Split each visual line at column boundaries before making blocks.
    split_lines: list[list[Span]] = []
    for raw_line in sorted_lines:
        for col in _split_columns(raw_line, col_gap):
            split_lines.append(col)

    blocks: list[Block] = []
    for line in split_lines:
        text = _join_text(line)
        if not text:
            continue
        avg_size = statistics.mean(s.font_size for s in line)
        is_bold = all(s.bold for s in line)
        bbox = _line_bbox(line)

        if text.lstrip().startswith(LIST_BULLETS):
            kind: BlockKind = "list_item"
            level = 0
        elif avg_size > body_size * 1.15 or (is_bold and avg_size >= body_size):
            kind = "heading"
            # Bigger size → smaller level number (h1 > h2 > ...).
            level = 1 if avg_size > body_size * 1.6 else 2 if avg_size > body_size * 1.3 else 3
        else:
            kind = "paragraph"
            level = 0

        blocks.append(Block(bbox=bbox, text=text, kind_hint=kind, spans=line, level=level))

    # Sort by (row bucket, x0) to restore reading order after column splitting.
    # PDFium gives per-glyph tight bboxes so same-row cells have slightly
    # different y0 values; a 2pt bucket aligns them before sorting by x0.
    blocks.sort(key=lambda b: (round(b.bbox.y0 / 2), b.bbox.x0))
    blocks = _absorb_dangling_bullets(blocks)
    blocks = _join_wrapped_lines(blocks, body_size)
    return PageSegmented(index=page.index, width=page.width, height=page.height,
                         blocks=blocks)


def segment(pages: list[PageRaw]) -> list[PageSegmented]:
    result = [_segment_page(p) for p in pages]
    for seg, raw in zip(result, pages):
        seg.images = list(raw.images)
    return result
