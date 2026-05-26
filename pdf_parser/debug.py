"""Debug bundle: re-runs the pipeline capturing every intermediate, then writes
a self-contained directory you can zip and share for remote diagnosis.

Layout produced by :func:`write_bundle`::

    DIR/
      README.md          — legend + how to read the bundle
      manifest.json      — versions, file hash, page sizes, per-stage timings, kind counts
      tree.json          — final DocNode tree (pydantic JSON)
      validate.json      — invariant errors + coverage diff
      overlays/
        00_final.pdf     — final tree boxes colored by DocNode.kind
        01_ingest.pdf    — raw text spans (cyan) + images (orange)
        02_segment.pdf   — classified blocks (heading/paragraph/list_item)
        03_cells.pdf     — cell candidates colored by evidence source
        04_tables.pdf    — table + row + cell boxes pre-stitch
      stages/
        01_ingest.json   — spans + images per page
        02_segment.json  — blocks per page
        03_cells.json    — cell records per page (bbox / text / source / confidence)
        04_tables.json   — table DocNodes pre-stitch
        05_stitch.json   — table DocNodes post-stitch
      pages/
        page_NNN.txt     — reading-order text dump per page from the final tree

Only :func:`parse_with_debug` re-runs the pipeline; production callers use
:func:`pdf_parser.pipeline.parse` and pay nothing for the debug machinery.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import platform
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pdfplumber

from pdf_parser.model import BBox, DocNode
from pdf_parser.overlay import (
    Annotation, CELL_SOURCE_COLORS, INGEST_COLORS, SEGMENT_COLORS,
    annotations_from_tree, render_overlay_pdf,
)
from pdf_parser.stages.build_tree import build_tree
from pdf_parser.stages.detect_cells import Cell
from pdf_parser.stages.extract_tables_v2 import _per_page as _extract_per_page
from pdf_parser.stages.ingest import PageRaw, ingest
from pdf_parser.stages.segment import PageSegmented, segment
from pdf_parser.stages.stitch_pages import stitch_tables
from pdf_parser.validate.report import validate

# --- captured state -------------------------------------------------------


@dataclass
class DebugBundle:
    """Every intermediate artifact captured while parsing ``pdf_path``."""
    pdf_path: Path
    raw_pages: list[PageRaw]
    segmented: list[PageSegmented]
    cells_per_page: list[list[Cell]]
    tables_pre_stitch: list[DocNode]
    tables_post_stitch: list[DocNode]
    tree: DocNode
    timings_ms: dict[str, float] = field(default_factory=dict)


def parse_with_debug(pdf_path: Path | str) -> DebugBundle:
    """Run the full pipeline capturing every stage's output.

    Mirrors :func:`pdf_parser.pipeline.parse` step-for-step.  Does NOT support
    the LLM fallback path — the debug bundle's purpose is to show what the
    deterministic stages produced, not to inspect a model's output.
    """
    pdf_path = Path(pdf_path)
    timings: dict[str, float] = {}
    t0 = time.perf_counter()

    raw_pages = ingest(pdf_path)
    t_ingest = time.perf_counter()

    segmented = segment(raw_pages)
    t_segment = time.perf_counter()

    cells_per_page: list[list[Cell]] = []
    tables_pre_stitch: list[DocNode] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for _page_idx, cells, _cell_tables, nodes in _extract_per_page(pdf):
            cells_per_page.append(list(cells))
            tables_pre_stitch.extend(nodes)
    t_extract = time.perf_counter()

    tables_post_stitch = stitch_tables(tables_pre_stitch)
    t_stitch = time.perf_counter()

    tree = build_tree(segmented, tables_post_stitch)
    t_build = time.perf_counter()

    timings["ingest_ms"]         = (t_ingest  - t0)       * 1000
    timings["segment_ms"]        = (t_segment - t_ingest) * 1000
    timings["extract_tables_ms"] = (t_extract - t_segment) * 1000
    timings["stitch_pages_ms"]   = (t_stitch  - t_extract) * 1000
    timings["build_tree_ms"]     = (t_build   - t_stitch)  * 1000
    timings["total_ms"]          = (t_build   - t0)        * 1000

    return DebugBundle(
        pdf_path=pdf_path,
        raw_pages=raw_pages,
        segmented=segmented,
        cells_per_page=cells_per_page,
        tables_pre_stitch=tables_pre_stitch,
        tables_post_stitch=tables_post_stitch,
        tree=tree,
        timings_ms=timings,
    )


# --- serialization helpers -----------------------------------------------


def _to_jsonable(obj):
    """Recursively convert pipeline objects (frozen dataclasses + pydantic
    BBox/DocNode) to JSON-friendly Python primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, BBox):
        return obj.model_dump()
    if isinstance(obj, DocNode):
        # round-trip through pydantic so we get the canonical model_dump shape
        return json.loads(obj.model_dump_json())
    if dataclasses.is_dataclass(obj):
        return {
            f.name: _to_jsonable(getattr(obj, f.name))
            for f in dataclasses.fields(obj)
        }
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, set):
        return [_to_jsonable(x) for x in sorted(obj, key=repr)]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"cannot JSON-encode {type(obj).__name__}")


def _kind_counts(tree: DocNode) -> dict[str, int]:
    counter: Counter[str] = Counter()
    stack = [tree]
    while stack:
        n = stack.pop()
        counter[n.kind] += 1
        stack.extend(n.children)
    return dict(sorted(counter.items()))


def _dep_versions() -> dict[str, str]:
    """Best-effort dep version lookup; missing optionals are reported as 'not installed'."""
    out: dict[str, str] = {}
    for name in ("pdf-parser", "pdfplumber", "pypdfium2", "pydantic", "typer", "pillow"):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = "not installed"
    return out


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _manifest(bundle: DebugBundle) -> dict:
    pdf_path = bundle.pdf_path.resolve()
    pages = [
        {"page": p.index, "width": p.width, "height": p.height}
        for p in bundle.raw_pages
    ]
    return {
        "pdf": {
            "path": str(pdf_path),
            "name": pdf_path.name,
            "sha256": _file_hash(pdf_path),
            "size_bytes": pdf_path.stat().st_size,
            "page_count": len(bundle.raw_pages),
            "page_sizes": pages,
        },
        "env": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "machine": platform.machine(),
            "deps": _dep_versions(),
        },
        "timings_ms": {k: round(v, 2) for k, v in bundle.timings_ms.items()},
        "counts": _kind_counts(bundle.tree),
        "stage_summary": {
            "spans_total":   sum(len(p.spans)  for p in bundle.raw_pages),
            "images_total":  sum(len(p.images) for p in bundle.raw_pages),
            "blocks_total":  sum(len(p.blocks) for p in bundle.segmented),
            "cells_total":   sum(len(c) for c in bundle.cells_per_page),
            "tables_pre_stitch":  len(bundle.tables_pre_stitch),
            "tables_post_stitch": len(bundle.tables_post_stitch),
        },
    }


# --- annotation builders --------------------------------------------------


def _annotations_ingest(raw_pages: list[PageRaw]) -> list[Annotation]:
    out: list[Annotation] = []
    for p in raw_pages:
        for s in p.spans:
            out.append(Annotation(bbox=s.bbox, color=INGEST_COLORS["span"]))
        for img in p.images:
            out.append(Annotation(bbox=img.bbox, color=INGEST_COLORS["image"]))
    return out


def _annotations_segment(segmented: list[PageSegmented]) -> list[Annotation]:
    out: list[Annotation] = []
    for p in segmented:
        for b in p.blocks:
            color = SEGMENT_COLORS.get(b.kind_hint, SEGMENT_COLORS["unknown"])
            out.append(Annotation(bbox=b.bbox, color=color))
    return out


def _annotations_cells(cells_per_page: list[list[Cell]]) -> list[Annotation]:
    out: list[Annotation] = []
    for page_cells in cells_per_page:
        for c in page_cells:
            color = CELL_SOURCE_COLORS.get(c.source, (120, 120, 120))
            out.append(Annotation(bbox=c.bbox, color=color))
    return out


def _annotations_tables(tables: list[DocNode]) -> list[Annotation]:
    """Tables get their full table/row/cell tri-color overlay."""
    out: list[Annotation] = []
    for t in tables:
        for n in _walk(t):
            from pdf_parser.overlay import TREE_KIND_COLORS
            color = TREE_KIND_COLORS.get(n.kind)
            if color is None:
                continue
            bboxes = n.bbox if isinstance(n.bbox, list) else [n.bbox]
            for b in bboxes:
                out.append(Annotation(bbox=b, color=color))
    return out


def _walk(node: DocNode):
    yield node
    for c in node.children:
        yield from _walk(c)


# --- text dump per page ---------------------------------------------------


def _page_text_dump(tree: DocNode, page_idx: int) -> str:
    """Reading-order text dump for *page_idx* — one line per leaf node carrying text."""
    rows: list[tuple[float, str]] = []
    for n in _walk(tree):
        if not n.text:
            continue
        bbox = n.bbox if isinstance(n.bbox, BBox) else n.bbox[0]
        if bbox.page != page_idx:
            continue
        rows.append((bbox.y0, f"[{n.kind:<10}] y={bbox.y0:7.1f}-{bbox.y1:7.1f}  {n.text}"))
    rows.sort(key=lambda r: r[0])
    return "\n".join(line for _, line in rows) + ("\n" if rows else "")


# --- main entry point -----------------------------------------------------


_README_TEMPLATE = """\
# pdf-parser debug bundle

Generated for `{pdf_name}` (SHA-256 `{sha256}`).

Zip this entire folder and send it to the maintainer. Everything needed to
diagnose a misclassification or missing element is in here — no need to
share the original PDF separately.

## Stage numbering

Files in `overlays/` and `stages/` are numbered in pipeline order: low
numbers run first, high numbers run last.  Stage 06 is the final
assembled tree.

    01 ingest  →  02 segment  →  03 cells  →  04 tables  →  05 stitch  →  06 final

## What to look at first

1. **`validate.json`** — invariant errors and coverage-diff text.
2. **`overlays/06_final.pdf`** — does the final tree match the visible page?
3. If something's off, walk **backward** through the stage overlays (06 → 01)
   until you find the first stage that already shows the bug — that's the
   stage to blame:
   - `05_stitch.pdf` — were cross-page tables merged correctly?
   - `04_tables.pdf` — were tables detected with the correct rows / columns?
   - `03_cells.pdf` — was every cell detected? (green = line-bounded, yellow = gutter, magenta = text fallback)
   - `02_segment.pdf` — was each line classified correctly? (red = heading, blue = paragraph, purple = list_item)
   - `01_ingest.pdf` — did PDFium even see the text? (cyan = span, orange = image)

## Color reference

Some colors are reused across stages on purpose — to let you track a concept
visually through the pipeline.  The only stage where the meaning **changes**
is `03_cells.pdf`, where green / yellow / magenta describe the *detector
source*, not the *node kind*.  Each stage's section below is self-contained.

### `01_ingest.pdf` — what PDFium actually saw

The lowest level.  Every text glyph and every embedded image, as raw input.
If a problem is already visible here, no later stage can recover from it.

| Color    | Means                                                           | What it tells you                                                                                              |
|----------|-----------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------|
| cyan     | one text span (run of glyphs sharing font / size / style)       | Visible text with **no cyan box** → PDFium couldn't decode it. Scanned PDF or unmapped font; needs OCR or LLM. |
| orange   | embedded raster image (≥ 10 pt in both dimensions)              | Missing orange where you see a chart/photo → pdfplumber didn't see the image stream.                            |

### `02_segment.pdf` — line classification

Spans clustered into visual lines, classified by font size and bold-ness
against the page's median (the "body baseline").  Note: at this stage,
**table cell text is still blue** — segment doesn't know about tables yet.
Stage 6 (build_tree) suppresses these once the table region is known.

| Color    | Means                       | Triggered by                                                                                  |
|----------|-----------------------------|-----------------------------------------------------------------------------------------------|
| red      | line classified `heading`   | `size > 1.15× body`, or all-bold at body size. Wrong-red on body → font heuristics misfired.  |
| blue     | line classified `paragraph` | The default.                                                                                  |
| purple   | line classified `list_item` | Leading bullet glyph (`•`, `-`, `*`, `◦`, `▪`). Missing-purple → bullet not recognized.       |
| gray     | `unknown`                   | Reserved; should never appear in practice.                                                    |

### `03_cells.pdf` — cell candidates by evidence source

Three detectors run independently on every page.  Each cell box is colored
by **which detector found it** — i.e. the detector's trust level.

| Color    | Source   | Means                                                                                              |
|----------|----------|----------------------------------------------------------------------------------------------------|
| green    | `line`   | Cell bounded by visible horizontal + vertical edges. **Highest trust** — the "real" table case.    |
| yellow   | `gutter` | No edges, but persistent whitespace columns + consistent line gaps. **Medium trust** — borderless. |
| magenta  | `text`   | pdfplumber's text-strategy fallback. **Lowest trust** — only fires when nothing else found anything. |

**Diagnostic:** a magenta box in a region that looks like prose is a smell —
the parser may be hallucinating a table out of paragraph text.

### `04_tables.pdf` / `05_stitch.pdf` — table structure

After cells are clustered into tables.  The two overlays use the same
palette; `05_stitch.pdf` differs from `04_tables.pdf` only on **multi-page
tables** that were merged by stage 5 (fragments on adjacent pages collapse
into one DocNode, duplicate header row dropped).

If 04 and 05 look identical on a fixture you *expect* to span pages →
stitch didn't fire (column anchors mismatched, or header didn't match, or
the `page_height` guard rejected the merge).

| Color    | Means                                              | Diagnostic                                                                          |
|----------|----------------------------------------------------|-------------------------------------------------------------------------------------|
| green    | table boundary (top-level or nested)               | Wrong size → row clustering pulled in too much / too little.                        |
| yellow   | row boundary                                       | Number of yellow boxes should match the visible row count.                          |
| magenta  | cell boundary                                      | Magenta count per row should match column count; misaligned → column anchors wrong. |
| blue     | `paragraph` between sub-tables inside a cell       | Legitimate: between-text extraction. Appears only in nested-table fixtures.         |
| purple   | `list_item` between sub-tables inside a cell       | Same as blue, for bulleted between-text.                                            |

### `06_final.pdf` — the assembled tree

Everything that survived stages 1–5, sorted into reading order and wrapped
in `page` / `document` nodes.  Each box is colored by its `DocNode.kind`.

| Color    | DocNode kind   | Notes                                                                                       |
|----------|----------------|---------------------------------------------------------------------------------------------|
| red      | `heading`      |                                                                                             |
| blue     | `paragraph`    | Inside a cell with nested sub-tables → between-text paragraph (legitimate). Inside a cell with **no** nested sub-tables → segment-stage paragraph leaked through build_tree's suppression (bug). Disambiguate via `provenance.stage` in `tree.json`: `extract_tables` = between-text, `build_tree` = leak. |
| green    | `table`        |                                                                                             |
| yellow   | `row`          |                                                                                             |
| magenta  | `cell`         |                                                                                             |
| purple   | `list` and `list_item` | Both share the same color; `list` wraps consecutive `list_item`s.                  |
| orange   | `figure`       | Embedded image not inside any table region.                                                 |

### Color reuse across stages (quick cross-reference)

| Color    | Final (06)         | Tables (04, 05)              | Cells (03)    | Segment (02) | Ingest (01) |
|----------|--------------------|------------------------------|---------------|--------------|-------------|
| red      | heading            | —                            | —             | heading      | —           |
| blue     | paragraph          | paragraph (between sub-tables) | —           | paragraph    | —           |
| green    | table              | table                        | line-bounded  | —            | —           |
| yellow   | row                | row                          | gutter        | —            | —           |
| magenta  | cell               | cell                         | text fallback | —            | —           |
| purple   | list / list_item   | list_item (between sub-tables) | —           | list_item    | —           |
| orange   | figure             | —                            | —             | —            | image       |
| cyan     | —                  | —                            | —             | —            | text span   |

## File map

| File                       | What's in it                                                   |
|----------------------------|----------------------------------------------------------------|
| `manifest.json`            | versions, file hash, page sizes, per-stage timings, kind counts |
| `tree.json`                | final DocNode tree                                             |
| `validate.json`            | invariant errors + coverage diff                               |
| `stages/01_ingest.json`    | raw text spans + image records per page                        |
| `stages/02_segment.json`   | classified blocks per page                                     |
| `stages/03_cells.json`     | cell candidates per page with `source` and `confidence`        |
| `stages/04_tables.json`    | table DocNodes pre-stitch                                      |
| `stages/05_stitch.json`    | table DocNodes post-stitch (cross-page merges visible here)    |
| `overlays/01_ingest.pdf`   | spans (cyan) + images (orange)                                 |
| `overlays/02_segment.pdf`  | blocks colored by `kind_hint`                                  |
| `overlays/03_cells.pdf`    | cell candidates colored by evidence source                     |
| `overlays/04_tables.pdf`   | table + row + cell boxes pre-stitch                            |
| `overlays/05_stitch.pdf`   | table + row + cell boxes post-stitch                           |
| `overlays/06_final.pdf`    | final DocNode tree colored by node kind                        |
| `pages/page_NNN.txt`       | reading-order text dump per page from the final tree           |
"""


def write_bundle(bundle: DebugBundle, out_dir: Path) -> None:
    """Write every artifact in *bundle* into *out_dir* (created if missing).

    Overwrites pre-existing files of the same name; does not clean other
    contents of *out_dir* (so a user can re-run --debug into the same
    folder without losing notes they left there).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "stages").mkdir(exist_ok=True)
    (out_dir / "overlays").mkdir(exist_ok=True)
    (out_dir / "pages").mkdir(exist_ok=True)

    pdf_path = bundle.pdf_path

    # 1. manifest.json
    manifest = _manifest(bundle)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    # 2. tree.json
    (out_dir / "tree.json").write_text(bundle.tree.model_dump_json(indent=2) + "\n")

    # 3. validate.json
    report = validate(bundle.tree, pdf_path)
    (out_dir / "validate.json").write_text(json.dumps({
        "passed": report.passed,
        "errors": report.errors,
        "coverage_missing": report.coverage_missing,
        "coverage_extra": report.coverage_extra,
    }, indent=2) + "\n")

    # 4. per-stage JSON
    (out_dir / "stages" / "01_ingest.json").write_text(
        json.dumps(_to_jsonable(bundle.raw_pages), indent=2) + "\n",
    )
    (out_dir / "stages" / "02_segment.json").write_text(
        json.dumps(_to_jsonable(bundle.segmented), indent=2) + "\n",
    )
    (out_dir / "stages" / "03_cells.json").write_text(
        json.dumps(
            [{"page": i, "cells": _to_jsonable(cells)}
             for i, cells in enumerate(bundle.cells_per_page)],
            indent=2,
        ) + "\n",
    )
    (out_dir / "stages" / "04_tables.json").write_text(
        json.dumps([_to_jsonable(t) for t in bundle.tables_pre_stitch], indent=2) + "\n",
    )
    (out_dir / "stages" / "05_stitch.json").write_text(
        json.dumps([_to_jsonable(t) for t in bundle.tables_post_stitch], indent=2) + "\n",
    )

    # 5. per-stage overlays (numbered in pipeline order; final is 06).
    render_overlay_pdf(pdf_path, _annotations_ingest(bundle.raw_pages),
                       out_dir / "overlays" / "01_ingest.pdf")
    render_overlay_pdf(pdf_path, _annotations_segment(bundle.segmented),
                       out_dir / "overlays" / "02_segment.pdf")
    render_overlay_pdf(pdf_path, _annotations_cells(bundle.cells_per_page),
                       out_dir / "overlays" / "03_cells.pdf")
    render_overlay_pdf(pdf_path, _annotations_tables(bundle.tables_pre_stitch),
                       out_dir / "overlays" / "04_tables.pdf")
    render_overlay_pdf(pdf_path, _annotations_tables(bundle.tables_post_stitch),
                       out_dir / "overlays" / "05_stitch.pdf")
    render_overlay_pdf(pdf_path, annotations_from_tree(bundle.tree),
                       out_dir / "overlays" / "06_final.pdf")

    # 6. per-page text dumps
    for i in range(len(bundle.raw_pages)):
        (out_dir / "pages" / f"page_{i:03d}.txt").write_text(
            _page_text_dump(bundle.tree, i),
        )

    # 7. README.md
    (out_dir / "README.md").write_text(_README_TEMPLATE.format(
        pdf_name=pdf_path.name,
        sha256=manifest["pdf"]["sha256"],
    ))
