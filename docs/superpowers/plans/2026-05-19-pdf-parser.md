# PDF Parser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, layout-first PDF→`DocNode` tree parser in Python with validation, renderers (JSON/Markdown/HTML), chunking for RAG, and a CLI — verified by a synthetic-PDF golden corpus.

**Architecture:** Eight-stage pure-function pipeline (ingest → segment → detect_tables → extract_tables → stitch_pages → build_tree → validate → render). One polymorphic `DocNode` type with content-addressed IDs and invariants enforced at construction. Nested tables fall out of recursion in stage 4. Cross-page tables stitch in stage 5 via column-anchor + header-signature matching. A synthetic fixture generator (`reportlab`) produces deterministic test PDFs; golden trees are the regression bedrock.

**Tech Stack:** Python 3.11+, `pymupdf` (ingest), `pdfplumber` (table detection), `pydantic` (model), `reportlab` (fixture generation), `typer` (CLI), `pytest` + `hypothesis` (tests), `pillow` (visualize), `anthropic` (optional LLM fallback).

**Spec:** `docs/superpowers/specs/2026-05-19-pdf-parser-design.md`

---

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `README.md`
- Create: `pdf_parser/__init__.py`
- Create: `pdf_parser/stages/__init__.py`
- Create: `pdf_parser/validate/__init__.py`
- Create: `pdf_parser/render/__init__.py`
- Create: `pdf_parser/fallback/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/fixtures/__init__.py`
- Create: `.gitignore`

- [ ] **Step 1: Write `pyproject.toml` with pinned deps**

```toml
[project]
name = "pdf-parser"
version = "0.1.0"
description = "Deterministic layout-first PDF parser with nested-table support."
requires-python = ">=3.11"
dependencies = [
    "pymupdf==1.24.10",
    "pdfplumber==0.11.4",
    "pydantic==2.9.2",
    "typer==0.12.5",
]

[project.optional-dependencies]
dev = [
    "pytest==8.3.3",
    "hypothesis==6.115.0",
    "reportlab==4.2.5",
    "pillow==10.4.0",
]
llm = [
    "anthropic==0.39.0",
]

[project.scripts]
pdf-parser = "pdf_parser.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["pdf_parser"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create empty package init files**

Create each of the following as empty files (zero bytes):
- `pdf_parser/__init__.py`
- `pdf_parser/stages/__init__.py`
- `pdf_parser/validate/__init__.py`
- `pdf_parser/render/__init__.py`
- `pdf_parser/fallback/__init__.py`
- `tests/__init__.py`
- `tests/fixtures/__init__.py`

- [ ] **Step 3: Write `.gitignore`**

```
__pycache__/
*.pyc
.pytest_cache/
.venv/
.uv/
*.egg-info/
dist/
build/
.DS_Store
```

- [ ] **Step 4: Write `README.md`**

```markdown
# pdf-parser

Deterministic layout-first PDF parser. See `docs/superpowers/specs/2026-05-19-pdf-parser-design.md` for design.

## Quick start

    uv venv && source .venv/bin/activate
    uv pip install -e ".[dev]"
    pytest

## CLI

    pdf-parser parse <path> [--format json|markdown|html|chunks]
```

- [ ] **Step 5: Install deps and verify import**

Run: `uv venv && source .venv/bin/activate && uv pip install -e ".[dev]" && python -c "import pdf_parser; print('ok')"`
Expected: `ok` (no ImportError)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml README.md .gitignore pdf_parser/ tests/
git commit -m "feat: scaffold pdf-parser package with pinned dependencies"
```

---

## Task 2: `DocNode` data model

**Files:**
- Create: `pdf_parser/model.py`
- Create: `tests/test_model.py`

- [ ] **Step 1: Write failing tests for `DocNode`**

Create `tests/test_model.py`:

```python
import pytest
from pdf_parser.model import DocNode, BBox, MAX_DEPTH


def _para(text="hello", page=0):
    return DocNode(
        kind="paragraph",
        bbox=BBox(page=page, x0=0, y0=0, x1=10, y1=10),
        text=text,
    )


def test_create_paragraph():
    n = _para()
    assert n.kind == "paragraph"
    assert n.text == "hello"
    assert n.children == []


def test_id_is_deterministic():
    a = _para("same")
    b = _para("same")
    assert a.id == b.id
    assert len(a.id) == 12


def test_id_changes_with_text():
    assert _para("a").id != _para("b").id


def test_id_rounds_bbox():
    a = DocNode(kind="paragraph", bbox=BBox(page=0, x0=0.001, y0=0, x1=10, y1=10), text="x")
    b = DocNode(kind="paragraph", bbox=BBox(page=0, x0=0.002, y0=0, x1=10, y1=10), text="x")
    assert a.id == b.id  # rounded to 1pt


def test_table_must_contain_rows():
    cell = DocNode(kind="cell", bbox=BBox(page=0, x0=0, y0=0, x1=5, y1=5), text="c")
    with pytest.raises(ValueError, match="table children must all be row"):
        DocNode(
            kind="table",
            bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
            children=[cell],
        )


def test_row_must_contain_cells():
    para = _para()
    with pytest.raises(ValueError, match="row children must all be cell"):
        DocNode(
            kind="row",
            bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
            children=[para],
        )


def test_cell_may_contain_nested_table():
    inner_cell = DocNode(kind="cell", bbox=BBox(page=0, x0=0, y0=0, x1=5, y1=5), text="x")
    inner_row = DocNode(kind="row", bbox=BBox(page=0, x0=0, y0=0, x1=5, y1=5), children=[inner_cell])
    inner_table = DocNode(kind="table", bbox=BBox(page=0, x0=0, y0=0, x1=5, y1=5), children=[inner_row])
    outer_cell = DocNode(
        kind="cell",
        bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
        children=[inner_table],
    )
    assert outer_cell.children[0].kind == "table"


def test_depth_cap_enforced():
    # Build a chain deeper than MAX_DEPTH and assert it fails.
    leaf = _para()
    node = leaf
    for _ in range(MAX_DEPTH + 2):
        node = DocNode(
            kind="section",
            bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
            children=[node],
        )
    with pytest.raises(ValueError, match="exceeds max depth"):
        # depth is checked on assembly via the helper
        node.assert_invariants()


def test_serialization_roundtrip():
    n = _para("hello")
    data = n.model_dump()
    restored = DocNode.model_validate(data)
    assert restored.id == n.id
    assert restored.text == n.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_model.py -v`
Expected: All FAIL with `ModuleNotFoundError: No module named 'pdf_parser.model'`

- [ ] **Step 3: Implement `pdf_parser/model.py`**

```python
"""Canonical DocNode tree + Chunk record. Invariants enforced at construction."""

from __future__ import annotations

import hashlib
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_DEPTH = 3

Kind = Literal[
    "document", "page", "section", "heading", "paragraph",
    "table", "row", "cell", "list", "list_item", "figure",
]


class BBox(BaseModel):
    model_config = ConfigDict(frozen=True)
    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def rounded(self) -> tuple[int, int, int, int, int]:
        return (self.page, round(self.x0), round(self.y0), round(self.x1), round(self.y1))


class DocNode(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: Kind
    bbox: BBox | list[BBox]
    text: Optional[str] = None
    children: list["DocNode"] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)
    id: str = ""

    @model_validator(mode="after")
    def _finalize(self) -> "DocNode":
        self._check_child_kinds()
        if not self.id:
            self.id = self._compute_id()
        return self

    def _check_child_kinds(self) -> None:
        if self.kind == "table":
            if any(c.kind != "row" for c in self.children):
                raise ValueError("table children must all be row")
        elif self.kind == "row":
            if any(c.kind != "cell" for c in self.children):
                raise ValueError("row children must all be cell")

    def _compute_id(self) -> str:
        bbox = self.bbox if isinstance(self.bbox, BBox) else self.bbox[0]
        material = f"{self.kind}|{bbox.rounded()}|{self.text or ''}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]

    def assert_invariants(self, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise ValueError(f"node exceeds max depth {MAX_DEPTH}")
        for c in self.children:
            c.assert_invariants(depth + 1)


DocNode.model_rebuild()


class Chunk(BaseModel):
    text: str
    breadcrumb: list[str] = Field(default_factory=list)
    page_range: tuple[int, int]
    source_ids: list[str] = Field(default_factory=list)
    kind_summary: str
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_model.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/model.py tests/test_model.py
git commit -m "feat(model): add DocNode and Chunk with deterministic ids and structural invariants"
```

---

## Task 3: Synthetic PDF fixture — `01_simple_table`

**Files:**
- Create: `tests/fixtures/build_pdfs.py`
- Create: `tests/fixtures/test_fixtures_deterministic.py`

- [ ] **Step 1: Write the fixture generator scaffold**

Create `tests/fixtures/build_pdfs.py`:

```python
"""Deterministic synthetic-PDF generator. Same code + pinned reportlab → byte-equivalent PDFs."""

from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak,
)

# Force reproducible PDFs (reportlab embeds a /CreationDate; pin via env).
os.environ.setdefault("SOURCE_DATE_EPOCH", "1700000000")

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "synthetic"


def _styles():
    return getSampleStyleSheet()


def build_01_simple_table(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    story = [
        Paragraph("Simple Table Example", s["Heading1"]),
        Spacer(1, 12),
        Paragraph("The table below has three columns.", s["BodyText"]),
        Spacer(1, 12),
        Table(
            [["Name", "Quantity", "Price"],
             ["Apple", "3", "$1.00"],
             ["Banana", "6", "$0.50"]],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]),
        ),
    ]
    doc.build(story)


BUILDERS = {
    "01_simple_table": build_01_simple_table,
}


def build_all() -> None:
    for name, builder in BUILDERS.items():
        out_dir = GOLDEN_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        builder(out_dir / "source.pdf")


if __name__ == "__main__":
    build_all()
```

- [ ] **Step 2: Generate the fixture**

Run: `python -m tests.fixtures.build_pdfs`
Expected: creates `tests/golden/synthetic/01_simple_table/source.pdf` (no errors).

- [ ] **Step 3: Write a determinism test**

Create `tests/fixtures/test_fixtures_deterministic.py`:

```python
import hashlib
from pathlib import Path

from tests.fixtures.build_pdfs import BUILDERS, GOLDEN_DIR


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_simple_table_pdf_is_byte_stable(tmp_path):
    out1 = tmp_path / "a.pdf"
    out2 = tmp_path / "b.pdf"
    BUILDERS["01_simple_table"](out1)
    BUILDERS["01_simple_table"](out2)
    assert _digest(out1) == _digest(out2)


def test_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "01_simple_table" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["01_simple_table"](regen)
    assert _digest(committed) == _digest(regen)
```

- [ ] **Step 4: Run the determinism tests**

Run: `pytest tests/fixtures/test_fixtures_deterministic.py -v`
Expected: PASS. (If `test_committed_fixture_matches_regeneration` fails, run the generator from step 2 and retry.)

- [ ] **Step 5: Commit**

```bash
git add tests/fixtures/build_pdfs.py tests/fixtures/test_fixtures_deterministic.py tests/golden/synthetic/01_simple_table/source.pdf
git commit -m "test(fixtures): add deterministic synthetic PDF generator with 01_simple_table"
```

---

## Task 4: Stage 1 — `ingest` (pymupdf raw spans)

**Files:**
- Create: `pdf_parser/stages/ingest.py`
- Create: `tests/stages/__init__.py`
- Create: `tests/stages/test_ingest.py`

- [ ] **Step 1: Write failing tests**

Create `tests/stages/test_ingest.py`:

```python
from pathlib import Path

from pdf_parser.stages.ingest import ingest

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"


def test_ingest_returns_one_page():
    pages = ingest(FIXTURE)
    assert len(pages) == 1


def test_ingest_extracts_heading_text():
    pages = ingest(FIXTURE)
    texts = [s.text for s in pages[0].spans]
    assert any("Simple Table Example" in t for t in texts)


def test_ingest_spans_have_bbox_and_font():
    pages = ingest(FIXTURE)
    span = pages[0].spans[0]
    assert span.bbox.x1 > span.bbox.x0
    assert span.bbox.y1 > span.bbox.y0
    assert span.font_size > 0
    assert isinstance(span.font_name, str) and span.font_name


def test_ingest_captures_page_size():
    pages = ingest(FIXTURE)
    p = pages[0]
    # US Letter is 612x792 points; pymupdf returns floats.
    assert round(p.width) == 612
    assert round(p.height) == 792
```

Also create empty `tests/stages/__init__.py`.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/stages/test_ingest.py -v`
Expected: ImportError on `pdf_parser.stages.ingest`.

- [ ] **Step 3: Implement `pdf_parser/stages/ingest.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/stages/test_ingest.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/ingest.py tests/stages/test_ingest.py tests/stages/__init__.py
git commit -m "feat(ingest): extract page spans, drawings, images via pymupdf"
```

---

## Task 5: Stage 2 — `segment` (blocks + heading/paragraph/list candidates)

**Files:**
- Create: `pdf_parser/stages/segment.py`
- Create: `tests/stages/test_segment.py`

- [ ] **Step 1: Write failing tests**

Create `tests/stages/test_segment.py`:

```python
from pathlib import Path

from pdf_parser.stages.ingest import ingest
from pdf_parser.stages.segment import segment

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"


def test_segment_produces_blocks():
    pages = ingest(FIXTURE)
    segs = segment(pages)
    assert len(segs) == 1
    assert len(segs[0].blocks) >= 2  # heading + body


def test_first_block_is_heading():
    pages = ingest(FIXTURE)
    segs = segment(pages)
    first = segs[0].blocks[0]
    assert first.kind_hint == "heading"
    assert "Simple Table Example" in first.text


def test_paragraph_block_detected():
    pages = ingest(FIXTURE)
    segs = segment(pages)
    paras = [b for b in segs[0].blocks if b.kind_hint == "paragraph"]
    assert any("three columns" in b.text for b in paras)


def test_blocks_in_reading_order():
    pages = ingest(FIXTURE)
    segs = segment(pages)
    ys = [b.bbox.y0 for b in segs[0].blocks]
    assert ys == sorted(ys)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/stages/test_segment.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/stages/segment.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/stages/test_segment.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/segment.py tests/stages/test_segment.py
git commit -m "feat(segment): cluster spans into heading/paragraph/list blocks by font and indent"
```

---

## Task 6: Stage 3 — `detect_tables` (pdfplumber)

**Files:**
- Create: `pdf_parser/stages/detect_tables.py`
- Create: `tests/stages/test_detect_tables.py`

- [ ] **Step 1: Write failing tests**

Create `tests/stages/test_detect_tables.py`:

```python
from pathlib import Path

from pdf_parser.stages.detect_tables import detect_tables

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"


def test_detects_one_table_on_first_page():
    regions = detect_tables(FIXTURE)
    assert len(regions) == 1
    region = regions[0]
    assert region.page_index == 0


def test_table_has_three_rows_three_columns():
    regions = detect_tables(FIXTURE)
    grid = regions[0].grid
    assert len(grid) == 3
    assert all(len(row) == 3 for row in grid)


def test_header_row_extracted():
    regions = detect_tables(FIXTURE)
    grid = regions[0].grid
    assert grid[0] == ["Name", "Quantity", "Price"]


def test_table_bbox_has_positive_area():
    regions = detect_tables(FIXTURE)
    b = regions[0].bbox
    assert b.x1 > b.x0 and b.y1 > b.y0
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/stages/test_detect_tables.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/stages/detect_tables.py`**

```python
"""Stage 3: pdfplumber-based table detection. Returns TableRegion list with cell grid."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pdfplumber

from pdf_parser.model import BBox

DEFAULT_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "edge_min_length": 3,
    "min_words_vertical": 2,
    "min_words_horizontal": 1,
}

MIN_TABLE_AREA = 100.0  # sq points; rejects two-span false-positives


@dataclass
class TableRegion:
    page_index: int
    bbox: BBox
    grid: list[list[str]]               # row-major text
    cell_bboxes: list[list[BBox]]       # parallel to grid


def _cell_text(cells: list[list]) -> list[list[str]]:
    return [[(c if c is not None else "") for c in row] for row in cells]


def _extract_region(plumber_page, table, page_index: int) -> Optional[TableRegion]:
    rows = table.extract()
    if not rows or len(rows) < 1:
        return None
    grid = _cell_text(rows)
    cell_bboxes: list[list[BBox]] = []
    for row_cells in table.cells_by_row() if hasattr(table, "cells_by_row") else _rows_from_cells(table):
        cell_bboxes.append([
            BBox(page=page_index, x0=c[0], y0=c[1], x1=c[2], y1=c[3]) if c else
            BBox(page=page_index, x0=0, y0=0, x1=0, y1=0)
            for c in row_cells
        ])
    x0, y0, x1, y1 = table.bbox
    area = (x1 - x0) * (y1 - y0)
    if area < MIN_TABLE_AREA:
        return None
    return TableRegion(
        page_index=page_index,
        bbox=BBox(page=page_index, x0=x0, y0=y0, x1=x1, y1=y1),
        grid=grid,
        cell_bboxes=cell_bboxes,
    )


def _rows_from_cells(table) -> list[list]:
    # pdfplumber's Table.cells is a flat list; reshape by row using y0.
    by_row: dict[float, list] = {}
    for c in table.cells:
        if c is None:
            continue
        by_row.setdefault(round(c[1], 1), []).append(c)
    return [sorted(row, key=lambda c: c[0]) for _, row in sorted(by_row.items())]


def detect_tables(
    pdf_path: Path,
    region_bbox: Optional[BBox] = None,
    settings: Optional[dict] = None,
) -> list[TableRegion]:
    settings = {**DEFAULT_TABLE_SETTINGS, **(settings or {})}
    out: list[TableRegion] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = pdf.pages if region_bbox is None else [pdf.pages[region_bbox.page]]
        for page in pages:
            target = page
            if region_bbox is not None:
                target = page.crop((region_bbox.x0, region_bbox.y0, region_bbox.x1, region_bbox.y1))
            for t in target.find_tables(table_settings=settings):
                region = _extract_region(target, t, page.page_number - 1)
                if region is not None:
                    out.append(region)
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/stages/test_detect_tables.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/detect_tables.py tests/stages/test_detect_tables.py
git commit -m "feat(detect_tables): pdfplumber-based table region detection with bbox cropping"
```

---

## Task 7: Add `02_nested_table` fixture

**Files:**
- Modify: `tests/fixtures/build_pdfs.py`

- [ ] **Step 1: Add nested-table builder**

Append to `tests/fixtures/build_pdfs.py`, inside the same file (above `BUILDERS = {...}`):

```python
def build_02_nested_table(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    inner = Table(
        [["sub-A", "sub-B"], ["1", "2"], ["3", "4"]],
        style=TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]),
        colWidths=[40, 40],
    )
    outer = Table(
        [
            ["Outer-Col-1", "Outer-Col-2", "Outer-Col-3"],
            ["row-1-a", inner, "row-1-c"],
            ["row-2-a", "row-2-b", "row-2-c"],
        ],
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
        colWidths=[100, 100, 100],
    )
    story = [
        Paragraph("Nested Table Example", s["Heading1"]),
        Spacer(1, 12),
        outer,
    ]
    doc.build(story)
```

Then update the `BUILDERS` dict:

```python
BUILDERS = {
    "01_simple_table": build_01_simple_table,
    "02_nested_table": build_02_nested_table,
}
```

- [ ] **Step 2: Generate and verify**

Run: `python -m tests.fixtures.build_pdfs && ls tests/golden/synthetic/02_nested_table/source.pdf`
Expected: file exists.

- [ ] **Step 3: Run all fixture determinism tests against the new case**

Append to `tests/fixtures/test_fixtures_deterministic.py`:

```python
def test_nested_table_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    BUILDERS["02_nested_table"](a)
    BUILDERS["02_nested_table"](b)
    assert _digest(a) == _digest(b)
```

Run: `pytest tests/fixtures/test_fixtures_deterministic.py -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/build_pdfs.py tests/fixtures/test_fixtures_deterministic.py tests/golden/synthetic/02_nested_table/source.pdf
git commit -m "test(fixtures): add 02_nested_table fixture"
```

---

## Task 8: Stage 4 — `extract_tables` (recursive nesting)

**Files:**
- Create: `pdf_parser/stages/extract_tables.py`
- Create: `tests/stages/test_extract_tables.py`

- [ ] **Step 1: Write failing tests**

Create `tests/stages/test_extract_tables.py`:

```python
from pathlib import Path

from pdf_parser.stages.extract_tables import extract_tables

SIMPLE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"
NESTED = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "02_nested_table" / "source.pdf"


def test_simple_table_no_nesting():
    tables = extract_tables(SIMPLE)
    assert len(tables) == 1
    t = tables[0]
    # 3 rows × 3 cells, no nested tables
    assert all(cell.children == [] or all(c.kind != "table" for c in cell.children)
               for row in t.children for cell in row.children)


def test_nested_table_detected_inside_cell():
    tables = extract_tables(NESTED)
    # Outer table should be present
    assert len(tables) >= 1
    outer = tables[0]
    nested_tables = [
        c for row in outer.children for cell in row.children for c in cell.children if c.kind == "table"
    ]
    assert len(nested_tables) == 1, f"expected 1 nested table, got {len(nested_tables)}"
    inner = nested_tables[0]
    # Inner table is 3×2
    assert len(inner.children) == 3
    assert all(len(row.children) == 2 for row in inner.children)


def test_nested_table_text_preserved():
    tables = extract_tables(NESTED)
    outer = tables[0]
    nested = [
        c for row in outer.children for cell in row.children for c in cell.children if c.kind == "table"
    ][0]
    cells_text = [cell.text for row in nested.children for cell in row.children]
    assert "sub-A" in cells_text
    assert "sub-B" in cells_text
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/stages/test_extract_tables.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/stages/extract_tables.py`**

```python
"""Stage 4: build DocNode subtree per TableRegion; recurse into cells for nested tables."""

from __future__ import annotations

from pathlib import Path

from pdf_parser.model import BBox, MAX_DEPTH, DocNode
from pdf_parser.stages.detect_tables import TableRegion, detect_tables


def _build_cell(text: str, bbox: BBox, pdf_path: Path, depth: int) -> DocNode:
    children: list[DocNode] = []
    if depth + 1 < MAX_DEPTH:
        nested = detect_tables(pdf_path, region_bbox=bbox)
        for region in nested:
            # Skip the parent cell's own bbox if the detector echoed it.
            if abs(region.bbox.x1 - region.bbox.x0) >= abs(bbox.x1 - bbox.x0) - 1:
                continue
            children.append(_build_table(region, pdf_path, depth + 1))
    return DocNode(
        kind="cell",
        bbox=bbox,
        text=text if not children else None,
        children=children,
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def _build_table(region: TableRegion, pdf_path: Path, depth: int) -> DocNode:
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(region.grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (
                region.cell_bboxes[r_idx][c_idx]
                if r_idx < len(region.cell_bboxes) and c_idx < len(region.cell_bboxes[r_idx])
                else region.bbox
            )
            cells.append(_build_cell(text, cbox, pdf_path, depth))
        rows.append(DocNode(
            kind="row",
            bbox=region.bbox,
            children=cells,
            attrs={"page": region.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=region.bbox,
        children=rows,
        attrs={
            "n_rows": len(region.grid),
            "n_cols": len(region.grid[0]) if region.grid else 0,
            "header_signature": tuple(region.grid[0]) if region.grid else (),
            "page": region.page_index,
        },
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )


def extract_tables(pdf_path: Path) -> list[DocNode]:
    regions = detect_tables(pdf_path)
    return [_build_table(r, pdf_path, depth=0) for r in regions]
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/stages/test_extract_tables.py -v`
Expected: All PASS.

If the nested-table test fails because the detector overshoots, narrow the cell bbox by 1pt margin before recursion:

```python
shrunk = BBox(page=bbox.page, x0=bbox.x0 + 1, y0=bbox.y0 + 1, x1=bbox.x1 - 1, y1=bbox.y1 - 1)
nested = detect_tables(pdf_path, region_bbox=shrunk)
```

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/extract_tables.py tests/stages/test_extract_tables.py
git commit -m "feat(extract_tables): build table subtree with recursive nested-table detection"
```

---

## Task 9: Add `03_page_spanning` fixture

**Files:**
- Modify: `tests/fixtures/build_pdfs.py`
- Modify: `tests/fixtures/test_fixtures_deterministic.py`

- [ ] **Step 1: Add the page-spanning builder**

Append to `tests/fixtures/build_pdfs.py`:

```python
def build_03_page_spanning(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
    s = _styles()
    header = ["ID", "Description", "Value"]
    rows = [header] + [[str(i), f"Item number {i}", f"${i * 1.5:.2f}"] for i in range(1, 51)]
    t = Table(
        rows,
        repeatRows=1,
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]),
        colWidths=[60, 280, 80],
    )
    story = [Paragraph("Page-Spanning Table", s["Heading1"]), Spacer(1, 12), t]
    doc.build(story)
```

Update `BUILDERS`:

```python
BUILDERS = {
    "01_simple_table": build_01_simple_table,
    "02_nested_table": build_02_nested_table,
    "03_page_spanning": build_03_page_spanning,
}
```

- [ ] **Step 2: Generate**

Run: `python -m tests.fixtures.build_pdfs`
Expected: `tests/golden/synthetic/03_page_spanning/source.pdf` created, multi-page.

Verify page count: `python -c "import pymupdf; d=pymupdf.open('tests/golden/synthetic/03_page_spanning/source.pdf'); print(len(d))"`
Expected: `>= 2`.

- [ ] **Step 3: Add determinism test**

Append to `tests/fixtures/test_fixtures_deterministic.py`:

```python
def test_page_spanning_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    BUILDERS["03_page_spanning"](a)
    BUILDERS["03_page_spanning"](b)
    assert _digest(a) == _digest(b)
```

Run: `pytest tests/fixtures/test_fixtures_deterministic.py -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/build_pdfs.py tests/fixtures/test_fixtures_deterministic.py tests/golden/synthetic/03_page_spanning/source.pdf
git commit -m "test(fixtures): add 03_page_spanning multi-page table fixture"
```

---

## Task 10: Stage 5 — `stitch_pages` (cross-page table merge)

**Files:**
- Create: `pdf_parser/stages/stitch_pages.py`
- Create: `tests/stages/test_stitch_pages.py`

- [ ] **Step 1: Write failing tests**

Create `tests/stages/test_stitch_pages.py`:

```python
from pathlib import Path

from pdf_parser.stages.extract_tables import extract_tables
from pdf_parser.stages.stitch_pages import stitch_tables

SPAN = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "03_page_spanning" / "source.pdf"


def test_per_page_tables_get_merged_to_one():
    pre = extract_tables(SPAN)
    assert len(pre) >= 2, f"expected ≥2 per-page tables before stitching, got {len(pre)}"
    merged = stitch_tables(pre)
    assert len(merged) == 1, f"expected 1 merged table, got {len(merged)}"


def test_merged_table_row_count_is_sum_minus_duplicate_headers():
    pre = extract_tables(SPAN)
    merged = stitch_tables(pre)
    pre_rows = sum(len(t.children) for t in pre)
    merged_rows = len(merged[0].children)
    # Header repeats on each page after the first should drop.
    assert merged_rows == pre_rows - (len(pre) - 1)


def test_merged_bbox_is_list_per_page():
    pre = extract_tables(SPAN)
    merged = stitch_tables(pre)
    assert isinstance(merged[0].bbox, list)
    assert len(merged[0].bbox) == len(pre)


def test_rows_retain_source_page():
    pre = extract_tables(SPAN)
    merged = stitch_tables(pre)
    pages = {row.attrs.get("page") for row in merged[0].children}
    assert pages == {t.attrs["page"] for t in pre}
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/stages/test_stitch_pages.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/stages/stitch_pages.py`**

```python
"""Stage 5: merge tables that continue across pages via column-anchor + header match."""

from __future__ import annotations

from pdf_parser.model import BBox, DocNode

COLUMN_ANCHOR_TOL = 4.0   # points
BOTTOM_MARGIN_FRAC = 0.05  # within 5% of page height counts as "near bottom"
TOP_MARGIN_FRAC = 0.10     # within 10% of page height counts as "near top"


def _col_anchors(table: DocNode) -> list[tuple[float, float]]:
    if not table.children or not table.children[0].children:
        return []
    first_row = table.children[0]
    return [(cell.bbox.x0, cell.bbox.x1) for cell in first_row.children]


def _anchors_match(a: list[tuple[float, float]], b: list[tuple[float, float]]) -> bool:
    if len(a) != len(b):
        return False
    return all(abs(a[i][0] - b[i][0]) <= COLUMN_ANCHOR_TOL and abs(a[i][1] - b[i][1]) <= COLUMN_ANCHOR_TOL
               for i in range(len(a)))


def _header_signature(table: DocNode) -> tuple[str, ...]:
    sig = table.attrs.get("header_signature")
    return tuple(sig) if sig else ()


def _first_bbox(table: DocNode) -> BBox:
    return table.bbox if isinstance(table.bbox, BBox) else table.bbox[0]


def _can_merge(prev: DocNode, nxt: DocNode) -> bool:
    if not _anchors_match(_col_anchors(prev), _col_anchors(nxt)):
        return False
    p_page = _first_bbox(prev).page
    n_page = _first_bbox(nxt).page
    if n_page != p_page + 1:
        return False
    return True


def _merge_two(prev: DocNode, nxt: DocNode) -> DocNode:
    prev_bboxes = prev.bbox if isinstance(prev.bbox, list) else [prev.bbox]
    next_bbox = _first_bbox(nxt)
    rows_next = list(nxt.children)
    if _header_signature(prev) and _header_signature(nxt) == _header_signature(prev):
        rows_next = rows_next[1:]  # drop duplicate header
    # Reindex row indices and ensure each row's attrs.page is set.
    rebuilt_rows: list[DocNode] = []
    base = len(prev.children)
    for row in list(prev.children) + rows_next:
        new_attrs = dict(row.attrs)
        new_attrs["row_index"] = len(rebuilt_rows)
        new_attrs.setdefault("page", _first_bbox(row).page if isinstance(row.bbox, BBox) else _first_bbox(row).page)
        rebuilt_rows.append(DocNode(
            kind=row.kind, bbox=row.bbox, children=row.children, attrs=new_attrs,
            text=row.text, provenance=row.provenance,
        ))
    merged_attrs = dict(prev.attrs)
    merged_attrs["n_rows"] = len(rebuilt_rows)
    merged_attrs["spans_pages"] = sorted({_first_bbox(r).page for r in rebuilt_rows})
    return DocNode(
        kind="table",
        bbox=prev_bboxes + [next_bbox],
        children=rebuilt_rows,
        attrs=merged_attrs,
        provenance={"extractor": "pdfplumber+stitch", "stage": "stitch_pages"},
    )


def stitch_tables(tables: list[DocNode]) -> list[DocNode]:
    if not tables:
        return []
    out: list[DocNode] = [tables[0]]
    for t in tables[1:]:
        if _can_merge(out[-1], t):
            out[-1] = _merge_two(out[-1], t)
        else:
            out.append(t)
    return out
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/stages/test_stitch_pages.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/stitch_pages.py tests/stages/test_stitch_pages.py
git commit -m "feat(stitch_pages): merge cross-page tables via column-anchor + header signature"
```

---

## Task 11: Stage 6 — `build_tree`

**Files:**
- Create: `pdf_parser/stages/build_tree.py`
- Create: `tests/stages/test_build_tree.py`

- [ ] **Step 1: Write failing tests**

Create `tests/stages/test_build_tree.py`:

```python
from pathlib import Path

from pdf_parser.stages.build_tree import build_tree
from pdf_parser.stages.extract_tables import extract_tables
from pdf_parser.stages.ingest import ingest
from pdf_parser.stages.segment import segment
from pdf_parser.stages.stitch_pages import stitch_tables

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"


def _run(pdf_path):
    pages = ingest(pdf_path)
    segs = segment(pages)
    tables = stitch_tables(extract_tables(pdf_path))
    return build_tree(segs, tables)


def test_root_is_document():
    tree = _run(FIXTURE)
    assert tree.kind == "document"
    assert len(tree.children) == 1  # one page in 01_simple_table


def test_page_has_heading_and_table():
    tree = _run(FIXTURE)
    page = tree.children[0]
    assert page.kind == "page"
    kinds = [c.kind for c in page.children]
    assert "heading" in kinds
    assert "table" in kinds


def test_reading_order_top_to_bottom():
    tree = _run(FIXTURE)
    page = tree.children[0]
    ys = []
    for c in page.children:
        bbox = c.bbox if hasattr(c.bbox, "y0") else c.bbox[0]
        ys.append(bbox.y0)
    assert ys == sorted(ys)


def test_ids_unique():
    tree = _run(FIXTURE)

    def walk(n):
        yield n
        for c in n.children:
            yield from walk(c)

    ids = [n.id for n in walk(tree)]
    assert len(ids) == len(set(ids))
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/stages/test_build_tree.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/stages/build_tree.py`**

```python
"""Stage 6: assemble the final DocNode tree from segmented blocks + stitched tables."""

from __future__ import annotations

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.segment import Block, PageSegmented


def _bbox_top(node_or_bbox) -> float:
    bbox = node_or_bbox.bbox if hasattr(node_or_bbox, "bbox") else node_or_bbox
    if isinstance(bbox, list):
        bbox = bbox[0]
    return bbox.y0


def _bbox_page(node_or_bbox) -> int:
    bbox = node_or_bbox.bbox if hasattr(node_or_bbox, "bbox") else node_or_bbox
    if isinstance(bbox, list):
        bbox = bbox[0]
    return bbox.page


def _bbox_overlaps_table(block: Block, table: DocNode) -> bool:
    tbox = table.bbox[0] if isinstance(table.bbox, list) else table.bbox
    if block.bbox.page != tbox.page:
        return False
    return not (block.bbox.y1 < tbox.y0 or block.bbox.y0 > tbox.y1)


def _block_to_node(block: Block) -> DocNode:
    if block.kind_hint == "heading":
        return DocNode(
            kind="heading",
            bbox=block.bbox,
            text=block.text,
            attrs={"level": block.level},
            provenance={"extractor": "segment", "stage": "build_tree"},
        )
    if block.kind_hint == "list_item":
        return DocNode(
            kind="list_item",
            bbox=block.bbox,
            text=block.text,
            provenance={"extractor": "segment", "stage": "build_tree"},
        )
    return DocNode(
        kind="paragraph",
        bbox=block.bbox,
        text=block.text,
        provenance={"extractor": "segment", "stage": "build_tree"},
    )


def _group_list_items(nodes: list[DocNode]) -> list[DocNode]:
    out: list[DocNode] = []
    buf: list[DocNode] = []
    for n in nodes:
        if n.kind == "list_item":
            buf.append(n)
        else:
            if buf:
                out.append(DocNode(kind="list", bbox=buf[0].bbox, children=buf))
                buf = []
            out.append(n)
    if buf:
        out.append(DocNode(kind="list", bbox=buf[0].bbox, children=buf))
    return out


def _build_page(seg: PageSegmented, tables_on_page: list[DocNode]) -> DocNode:
    # Drop blocks whose bbox overlaps any table region (avoid double-counting cell text).
    free_blocks = [b for b in seg.blocks if not any(_bbox_overlaps_table(b, t) for t in tables_on_page)]
    nodes: list[DocNode] = [_block_to_node(b) for b in free_blocks] + list(tables_on_page)
    nodes.sort(key=_bbox_top)
    nodes = _group_list_items(nodes)
    return DocNode(
        kind="page",
        bbox=BBox(page=seg.index, x0=0, y0=0, x1=seg.width, y1=seg.height),
        children=nodes,
        attrs={"page_index": seg.index},
    )


def _attach_tables_to_pages(tables: list[DocNode]) -> dict[int, list[DocNode]]:
    by_page: dict[int, list[DocNode]] = {}
    for t in tables:
        p = _bbox_page(t)
        by_page.setdefault(p, []).append(t)
    return by_page


def build_tree(segments: list[PageSegmented], tables: list[DocNode]) -> DocNode:
    by_page = _attach_tables_to_pages(tables)
    pages: list[DocNode] = []
    for seg in segments:
        pages.append(_build_page(seg, by_page.get(seg.index, [])))
    root = DocNode(
        kind="document",
        bbox=BBox(page=0, x0=0, y0=0, x1=0, y1=0),
        children=pages,
    )
    root.assert_invariants()
    return root
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/stages/test_build_tree.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/build_tree.py tests/stages/test_build_tree.py
git commit -m "feat(build_tree): assemble final DocNode tree, drop block-text inside table regions"
```

---

## Task 12: Pipeline orchestrator + Stage 7 hooks

**Files:**
- Create: `pdf_parser/pipeline.py`
- Create: `tests/test_pipeline.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_pipeline.py`:

```python
from pathlib import Path

from pdf_parser.pipeline import parse

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")
SPAN = Path("tests/golden/synthetic/03_page_spanning/source.pdf")


def test_parse_simple_returns_document():
    tree = parse(SIMPLE)
    assert tree.kind == "document"


def test_parse_nested_preserves_nesting():
    tree = parse(NESTED)
    tables = [n for n in _walk(tree) if n.kind == "table"]
    nested = [n for n in tables if any(
        c.kind == "table" for row in n.children for cell in row.children for c in cell.children
    )]
    assert len(nested) >= 1


def test_parse_span_produces_single_table():
    tree = parse(SPAN)
    tables = [n for n in _walk(tree) if n.kind == "table"]
    assert len(tables) == 1


def test_parse_deterministic_same_id():
    a = parse(SIMPLE)
    b = parse(SIMPLE)
    assert a.id == b.id
    assert [n.id for n in _walk(a)] == [n.id for n in _walk(b)]


def _walk(n):
    yield n
    for c in n.children:
        yield from _walk(c)
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_pipeline.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/pipeline.py`**

```python
"""Pipeline orchestrator: PDF path → DocNode tree.

Stages 1–6 are pure and deterministic. Stage 7 (validate) is run separately by
the caller via `pdf_parser.validate`. Stage 8 (render) is per-format.
"""

from __future__ import annotations

from pathlib import Path

from pdf_parser.model import DocNode
from pdf_parser.stages.build_tree import build_tree
from pdf_parser.stages.extract_tables import extract_tables
from pdf_parser.stages.ingest import ingest
from pdf_parser.stages.segment import segment
from pdf_parser.stages.stitch_pages import stitch_tables


def parse(pdf_path: Path | str) -> DocNode:
    pdf_path = Path(pdf_path)
    raw_pages = ingest(pdf_path)
    segments = segment(raw_pages)
    tables = stitch_tables(extract_tables(pdf_path))
    return build_tree(segments, tables)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/pipeline.py tests/test_pipeline.py
git commit -m "feat(pipeline): wire ingest→segment→detect→extract→stitch→build_tree"
```

---

## Task 13: Validate — coverage check (Layer 1)

**Files:**
- Create: `pdf_parser/validate/coverage.py`
- Create: `tests/validate/__init__.py`
- Create: `tests/validate/test_coverage.py`

- [ ] **Step 1: Write failing tests**

Create `tests/validate/test_coverage.py`:

```python
from pathlib import Path

import pymupdf

from pdf_parser.pipeline import parse
from pdf_parser.validate.coverage import coverage_diff, coverage_ok

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def _raw_text(pdf_path: Path) -> str:
    doc = pymupdf.open(str(pdf_path))
    try:
        return "".join(page.get_text() for page in doc)
    finally:
        doc.close()


def test_coverage_passes_on_simple_table():
    tree = parse(SIMPLE)
    raw = _raw_text(SIMPLE)
    assert coverage_ok(tree, raw)


def test_coverage_returns_no_missing_chars():
    tree = parse(SIMPLE)
    raw = _raw_text(SIMPLE)
    diff = coverage_diff(tree, raw)
    # missing should be empty (or only contain whitelist tokens). extra may be empty.
    assert diff.missing == "", f"missing leaf text: {diff.missing!r}"
```

Also create empty `tests/validate/__init__.py`.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/validate/test_coverage.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/validate/coverage.py`**

```python
"""Layer 1 coverage check: leaf-text multiset == page-text multiset (mod whitespace + whitelist)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from pdf_parser.model import DocNode

# Whitelist of patterns we expect to drop from extracted text (page numbers, repeated headers).
DROP_PATTERNS = [
    re.compile(r"^Page \d+( of \d+)?$"),
    re.compile(r"^\d+$"),  # bare page numbers
]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _leaf_text(tree: DocNode) -> str:
    parts: list[str] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.text:
            parts.append(node.text)
        stack.extend(reversed(node.children))
    return " ".join(parts)


def _filter_drops(s: str) -> str:
    keep_lines: list[str] = []
    for line in s.splitlines():
        line_stripped = line.strip()
        if any(p.match(line_stripped) for p in DROP_PATTERNS):
            continue
        keep_lines.append(line)
    return "\n".join(keep_lines)


@dataclass
class CoverageDiff:
    missing: str  # chars present in raw but not in tree
    extra: str    # chars present in tree but not in raw


def coverage_diff(tree: DocNode, raw_text: str) -> CoverageDiff:
    raw = _normalize(_filter_drops(raw_text))
    leaf = _normalize(_leaf_text(tree))
    raw_counts = Counter(raw)
    leaf_counts = Counter(leaf)
    missing = raw_counts - leaf_counts
    extra = leaf_counts - raw_counts
    return CoverageDiff(
        missing="".join(sorted(missing.elements())),
        extra="".join(sorted(extra.elements())),
    )


def coverage_ok(tree: DocNode, raw_text: str) -> bool:
    d = coverage_diff(tree, raw_text)
    return d.missing == ""
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/validate/test_coverage.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/validate/coverage.py tests/validate/__init__.py tests/validate/test_coverage.py
git commit -m "feat(validate): leaf-text coverage check vs raw page text"
```

---

## Task 14: Validate — structural invariants & report

**Files:**
- Create: `pdf_parser/validate/invariants.py`
- Create: `pdf_parser/validate/report.py`
- Create: `tests/validate/test_invariants.py`

- [ ] **Step 1: Write failing tests**

Create `tests/validate/test_invariants.py`:

```python
from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.validate.invariants import (
    check_cross_page_integrity, check_reading_order,
    check_table_shape, check_well_formedness,
)
from pdf_parser.validate.report import ValidationReport, validate

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")
SPAN = Path("tests/golden/synthetic/03_page_spanning/source.pdf")


def test_well_formedness_simple():
    tree = parse(SIMPLE)
    assert check_well_formedness(tree) == []


def test_table_shape_passes_for_uniform_table():
    tree = parse(SIMPLE)
    assert check_table_shape(tree) == []


def test_reading_order_monotonic():
    tree = parse(SIMPLE)
    assert check_reading_order(tree) == []


def test_cross_page_integrity_for_spanned_table():
    tree = parse(SPAN)
    assert check_cross_page_integrity(tree) == []


def test_validate_returns_report():
    tree = parse(NESTED)
    report: ValidationReport = validate(tree, SIMPLE)
    assert report.passed
    assert report.errors == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/validate/test_invariants.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/validate/invariants.py`**

```python
"""Layer 1 structural invariants. Each check returns list of human-readable error strings."""

from __future__ import annotations

from pdf_parser.model import MAX_DEPTH, BBox, DocNode


def _walk(n: DocNode, depth=0):
    yield depth, n
    for c in n.children:
        yield from _walk(c, depth + 1)


def _first_bbox(n: DocNode) -> BBox:
    return n.bbox if isinstance(n.bbox, BBox) else n.bbox[0]


def check_well_formedness(tree: DocNode) -> list[str]:
    errs: list[str] = []
    seen_ids: set[str] = set()
    for depth, n in _walk(tree):
        if depth > MAX_DEPTH + 2:  # +2 for document/page wrappers
            errs.append(f"node {n.id} ({n.kind}) exceeds depth {MAX_DEPTH}")
        if n.id in seen_ids:
            errs.append(f"duplicate id {n.id} on {n.kind}")
        seen_ids.add(n.id)
        if n.kind == "table" and any(c.kind != "row" for c in n.children):
            errs.append(f"table {n.id} has non-row child")
        if n.kind == "row" and any(c.kind != "cell" for c in n.children):
            errs.append(f"row {n.id} has non-cell child")
    return errs


def check_table_shape(tree: DocNode) -> list[str]:
    errs: list[str] = []
    for _, n in _walk(tree):
        if n.kind != "table" or not n.children:
            continue
        row_widths = [len(row.children) for row in n.children]
        # Allow rows with colspan declared in attrs.
        unique = {w for w in row_widths
                  if not any(c.attrs.get("colspan") for c in n.children[row_widths.index(w)].children)}
        if len(unique) > 1:
            errs.append(f"table {n.id} has inconsistent row widths {row_widths}")
    return errs


def check_reading_order(tree: DocNode) -> list[str]:
    errs: list[str] = []
    for _, n in _walk(tree):
        if n.kind != "page":
            continue
        ys = [_first_bbox(c).y0 for c in n.children]
        if ys != sorted(ys):
            errs.append(f"page {n.attrs.get('page_index')} children not in reading order")
    return errs


def check_cross_page_integrity(tree: DocNode) -> list[str]:
    errs: list[str] = []
    for _, n in _walk(tree):
        if n.kind != "table" or not isinstance(n.bbox, list):
            continue
        pages = [b.page for b in n.bbox]
        if pages != sorted(pages):
            errs.append(f"table {n.id} bboxes not in page order: {pages}")
        row_indices = [c.attrs.get("row_index") for c in n.children]
        if row_indices != list(range(len(row_indices))):
            errs.append(f"table {n.id} rows not continuously indexed")
    return errs
```

- [ ] **Step 4: Implement `pdf_parser/validate/report.py`**

```python
"""Aggregator: runs all Layer 1 checks; produces a ValidationReport."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pdf_parser.model import DocNode
from pdf_parser.validate.coverage import coverage_diff
from pdf_parser.validate.invariants import (
    check_cross_page_integrity, check_reading_order,
    check_table_shape, check_well_formedness,
)


@dataclass
class ValidationReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
    coverage_missing: str = ""
    coverage_extra: str = ""


def _raw_text(pdf_path: Path) -> str:
    doc = pymupdf.open(str(pdf_path))
    try:
        return "".join(page.get_text() for page in doc)
    finally:
        doc.close()


def validate(tree: DocNode, pdf_path: Path | str) -> ValidationReport:
    errors: list[str] = []
    errors += check_well_formedness(tree)
    errors += check_table_shape(tree)
    errors += check_reading_order(tree)
    errors += check_cross_page_integrity(tree)

    diff = coverage_diff(tree, _raw_text(Path(pdf_path)))
    if diff.missing:
        errors.append(f"coverage missing: {diff.missing[:80]!r}")

    return ValidationReport(
        passed=not errors,
        errors=errors,
        coverage_missing=diff.missing,
        coverage_extra=diff.extra,
    )
```

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/validate/test_invariants.py -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add pdf_parser/validate/invariants.py pdf_parser/validate/report.py tests/validate/test_invariants.py
git commit -m "feat(validate): structural invariants + aggregated ValidationReport"
```

---

## Task 15: JSON renderer

**Files:**
- Create: `pdf_parser/render/json_.py`
- Create: `tests/render/__init__.py`
- Create: `tests/render/test_json.py`

- [ ] **Step 1: Write failing tests**

Create `tests/render/test_json.py`:

```python
import json
from pathlib import Path

from pdf_parser.model import DocNode
from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_json_is_valid_json():
    tree = parse(SIMPLE)
    s = to_json(tree)
    json.loads(s)  # no error


def test_json_round_trip_preserves_ids():
    tree = parse(SIMPLE)
    s = to_json(tree)
    restored = DocNode.model_validate_json(s)
    assert restored.id == tree.id


def test_json_pretty_is_stable():
    tree = parse(SIMPLE)
    a = to_json(tree, indent=2)
    b = to_json(tree, indent=2)
    assert a == b
```

Also create empty `tests/render/__init__.py`.

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/render/test_json.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/render/json_.py`**

```python
"""JSON renderer — pydantic serialization with stable key order."""

from __future__ import annotations

from pdf_parser.model import DocNode


def to_json(tree: DocNode, indent: int | None = None) -> str:
    return tree.model_dump_json(indent=indent)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/render/test_json.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/render/json_.py tests/render/__init__.py tests/render/test_json.py
git commit -m "feat(render): JSON renderer via pydantic serialization"
```

---

## Task 16: Markdown renderer (with inline-HTML fallback for nested tables)

**Files:**
- Create: `pdf_parser/render/markdown.py`
- Create: `tests/render/test_markdown.py`

- [ ] **Step 1: Write failing tests**

Create `tests/render/test_markdown.py`:

```python
from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.render.markdown import to_markdown

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")


def test_simple_markdown_has_heading_and_pipe_table():
    md = to_markdown(parse(SIMPLE))
    assert "# Simple Table Example" in md
    assert "| Name | Quantity | Price |" in md
    assert "| --- | --- | --- |" in md


def test_simple_markdown_has_no_inline_html():
    md = to_markdown(parse(SIMPLE))
    assert "<table>" not in md


def test_nested_table_falls_back_to_inline_html():
    md = to_markdown(parse(NESTED))
    # Nested → at least one cell rendered as inline HTML <table>
    assert "<table>" in md
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/render/test_markdown.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/render/markdown.py`**

```python
"""Markdown renderer. GFM pipe tables; cells containing tables fall back to inline HTML."""

from __future__ import annotations

from pdf_parser.model import DocNode


def _escape_cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def _has_nested_table(table: DocNode) -> bool:
    for row in table.children:
        for cell in row.children:
            for c in cell.children:
                if c.kind == "table":
                    return True
    return False


def _render_html_cell(cell: DocNode) -> str:
    parts: list[str] = []
    if cell.text:
        parts.append(cell.text)
    for c in cell.children:
        if c.kind == "table":
            parts.append(_render_html_table(c))
        elif c.text:
            parts.append(c.text)
    return "".join(parts)


def _render_html_table(table: DocNode) -> str:
    out = ["<table>"]
    for row in table.children:
        out.append("<tr>")
        for cell in row.children:
            out.append(f"<td>{_render_html_cell(cell)}</td>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _render_gfm_table(table: DocNode) -> str:
    if not table.children:
        return ""
    header = table.children[0]
    body = table.children[1:]
    header_cells = [_escape_cell(c.text or "") for c in header.children]
    lines = [
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join(["---"] * len(header_cells)) + " |",
    ]
    for row in body:
        cells = [_escape_cell(c.text or "") for c in row.children]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _render_node(node: DocNode) -> str:
    if node.kind in ("document", "page", "section"):
        return "\n\n".join(_render_node(c) for c in node.children if _render_node(c))
    if node.kind == "heading":
        level = max(1, min(6, node.attrs.get("level", 1)))
        return f"{'#' * level} {node.text or ''}"
    if node.kind == "paragraph":
        return node.text or ""
    if node.kind == "list":
        return "\n".join(f"- {c.text or ''}" for c in node.children)
    if node.kind == "list_item":
        return f"- {node.text or ''}"
    if node.kind == "table":
        return _render_html_table(node) if _has_nested_table(node) else _render_gfm_table(node)
    if node.kind == "figure":
        path = node.attrs.get("path", "")
        return f"![{node.text or ''}]({path})"
    return node.text or ""


def to_markdown(tree: DocNode) -> str:
    return _render_node(tree).strip() + "\n"
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/render/test_markdown.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/render/markdown.py tests/render/test_markdown.py
git commit -m "feat(render): Markdown renderer with inline-HTML fallback for nested tables"
```

---

## Task 17: HTML renderer

**Files:**
- Create: `pdf_parser/render/html.py`
- Create: `tests/render/test_html.py`

- [ ] **Step 1: Write failing tests**

Create `tests/render/test_html.py`:

```python
from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.render.html import to_html

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")


def test_html_has_article_root():
    html = to_html(parse(SIMPLE))
    assert html.startswith("<article")


def test_simple_table_renders_as_native_table():
    html = to_html(parse(SIMPLE))
    assert "<table>" in html
    assert "<th>Name</th>" in html or "<td>Name</td>" in html


def test_nested_html_table_is_native():
    html = to_html(parse(NESTED))
    # Find an inner <table> nested inside a <td>
    assert "<td><table>" in html.replace(" ", "")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/render/test_html.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/render/html.py`**

```python
"""HTML renderer — native nested tables. Escapes text."""

from __future__ import annotations

import html as _h

from pdf_parser.model import DocNode


def _esc(s: str | None) -> str:
    return _h.escape(s or "", quote=False)


def _render_table(t: DocNode) -> str:
    out = ["<table>"]
    for i, row in enumerate(t.children):
        out.append("<tr>")
        tag = "th" if i == 0 else "td"
        for cell in row.children:
            out.append(f"<{tag}>{_render_cell(cell)}</{tag}>")
        out.append("</tr>")
    out.append("</table>")
    return "".join(out)


def _render_cell(cell: DocNode) -> str:
    parts: list[str] = []
    if cell.text:
        parts.append(_esc(cell.text))
    for c in cell.children:
        if c.kind == "table":
            parts.append(_render_table(c))
        else:
            parts.append(_render(c))
    return "".join(parts)


def _render(node: DocNode) -> str:
    if node.kind == "document":
        return "<article>" + "".join(_render(c) for c in node.children) + "</article>"
    if node.kind == "page":
        return "<section data-page=\"" + str(node.attrs.get("page_index", 0)) + "\">" + \
               "".join(_render(c) for c in node.children) + "</section>"
    if node.kind == "section":
        return "<section>" + "".join(_render(c) for c in node.children) + "</section>"
    if node.kind == "heading":
        level = max(1, min(6, node.attrs.get("level", 1)))
        return f"<h{level}>{_esc(node.text)}</h{level}>"
    if node.kind == "paragraph":
        return f"<p>{_esc(node.text)}</p>"
    if node.kind == "list":
        return "<ul>" + "".join(f"<li>{_esc(c.text)}</li>" for c in node.children) + "</ul>"
    if node.kind == "list_item":
        return f"<li>{_esc(node.text)}</li>"
    if node.kind == "table":
        return _render_table(node)
    if node.kind == "figure":
        path = node.attrs.get("path", "")
        return f'<figure><img src="{_esc(path)}" alt="{_esc(node.text)}"></figure>'
    return _esc(node.text)


def to_html(tree: DocNode) -> str:
    return _render(tree)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/render/test_html.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/render/html.py tests/render/test_html.py
git commit -m "feat(render): HTML renderer with native nested tables"
```

---

## Task 18: Renderer property test (parse → render → re-parse idempotence)

**Files:**
- Create: `tests/render/test_renderer_properties.py`

- [ ] **Step 1: Write the property test**

Create `tests/render/test_renderer_properties.py`:

```python
"""Hypothesis property test: tree_skeleton(parse(SIMPLE)) is stable across re-parses."""

from pathlib import Path

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")
SPAN = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
FIXTURES = [SIMPLE, NESTED, SPAN]


@given(st.integers(min_value=0, max_value=2))
@settings(max_examples=6, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_repeated_parses_produce_identical_json(idx):
    pdf = FIXTURES[idx]
    j1 = to_json(parse(pdf))
    j2 = to_json(parse(pdf))
    assert j1 == j2
```

- [ ] **Step 2: Run the test**

Run: `pytest tests/render/test_renderer_properties.py -v`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/render/test_renderer_properties.py
git commit -m "test(render): hypothesis property — repeated parses produce identical JSON"
```

---

## Task 19: Chunking module (`chunk.py`)

**Files:**
- Create: `pdf_parser/chunk.py`
- Create: `tests/test_chunk.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_chunk.py`:

```python
from pathlib import Path

from pdf_parser.chunk import chunk_tree
from pdf_parser.pipeline import parse

SPAN = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_chunks_have_breadcrumb_and_page_range():
    chunks = chunk_tree(parse(SIMPLE), max_tokens=400)
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert isinstance(c.breadcrumb, list)
        assert c.page_range[0] <= c.page_range[1]
        assert c.source_ids


def test_paragraph_chunks_get_overlap():
    # Make a small max_tokens so a paragraph splits
    chunks = chunk_tree(parse(SIMPLE), max_tokens=8, overlap=2)
    para_chunks = [c for c in chunks if c.kind_summary == "paragraph"]
    if len(para_chunks) >= 2:
        # Tail of one is prefix of next (token overlap)
        a, b = para_chunks[0], para_chunks[1]
        a_tail = a.text.split()[-2:]
        b_head = b.text.split()[:2]
        assert a_tail == b_head


def test_table_chunk_summary_has_shape():
    chunks = chunk_tree(parse(SIMPLE), max_tokens=400)
    tbl = [c for c in chunks if c.kind_summary.startswith("table:")]
    assert tbl, "expected a table chunk"


def test_big_table_splits_with_repeated_header():
    chunks = chunk_tree(parse(SPAN), max_tokens=200)
    tbl = [c for c in chunks if c.kind_summary.startswith("table:")]
    assert len(tbl) >= 2, f"expected ≥2 table chunks for big spanned table, got {len(tbl)}"
    # Every table chunk should contain the header row's first cell label.
    for c in tbl:
        assert "ID" in c.text  # header was "ID | Description | Value"


def test_no_chunk_splits_a_row():
    chunks = chunk_tree(parse(SPAN), max_tokens=200)
    for c in chunks:
        # row delimiters in text are newlines; each row begins with "ID" header OR a digit
        for line in c.text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            # Either header line or a complete row (3 pipe-separated fields)
            if stripped.startswith("ID"):
                continue
            assert stripped.count("|") >= 2 or stripped == c.text.strip()
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_chunk.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/chunk.py`**

```python
"""Chunk RAG-ready records from a DocNode tree.

Rules:
- Never split a row or a cell.
- Heading ancestors flow into breadcrumb (not text).
- Big tables split on row-group boundaries; header row is repeated.
- Paragraph chunks get token overlap; table chunks do not.
"""

from __future__ import annotations

from pdf_parser.model import BBox, Chunk, DocNode


def _est_tokens(s: str) -> int:
    # Rough: 1 token ≈ 0.75 words. Words = whitespace split.
    return max(1, int(len(s.split()) / 0.75))


def _bbox_pages(node: DocNode) -> tuple[int, int]:
    bboxes = node.bbox if isinstance(node.bbox, list) else [node.bbox]
    pages = [b.page for b in bboxes]
    return (min(pages), max(pages))


def _row_to_md(row: DocNode) -> str:
    return "| " + " | ".join((c.text or "").replace("|", "\\|") for c in row.children) + " |"


def _split_table(table: DocNode, max_tokens: int) -> list[Chunk]:
    rows = table.children
    if not rows:
        return []
    header = rows[0]
    body = rows[1:]
    header_md = _row_to_md(header)
    n_cols = len(header.children)

    chunks: list[Chunk] = []
    buf: list[DocNode] = []
    buf_tokens = _est_tokens(header_md)
    for row in body:
        line = _row_to_md(row)
        if buf_tokens + _est_tokens(line) > max_tokens and buf:
            chunks.append(_table_chunk(table, header_md, buf, n_cols))
            buf = []
            buf_tokens = _est_tokens(header_md)
        buf.append(row)
        buf_tokens += _est_tokens(line)
    if buf:
        chunks.append(_table_chunk(table, header_md, buf, n_cols))
    return chunks


def _table_chunk(table: DocNode, header_md: str, rows: list[DocNode], n_cols: int) -> Chunk:
    body_md = "\n".join(_row_to_md(r) for r in rows)
    text = header_md + "\n" + body_md
    pages = sorted({_bbox_pages(r)[0] for r in rows})
    return Chunk(
        text=text,
        breadcrumb=[],  # filled in by caller using ancestor stack
        page_range=(min(pages), max(pages)),
        source_ids=[table.id] + [r.id for r in rows],
        kind_summary=f"table:{table.attrs.get('n_rows', '?')}x{n_cols}",
    )


def _split_paragraph(node: DocNode, max_tokens: int, overlap: int) -> list[Chunk]:
    words = (node.text or "").split()
    if not words:
        return []
    chunks: list[Chunk] = []
    start = 0
    page_range = _bbox_pages(node)
    while start < len(words):
        end = start + max(1, int(max_tokens * 0.75))
        text = " ".join(words[start:end])
        chunks.append(Chunk(
            text=text,
            breadcrumb=[],
            page_range=page_range,
            source_ids=[node.id],
            kind_summary="paragraph",
        ))
        if end >= len(words):
            break
        start = end - max(0, int(overlap * 0.75))
    return chunks


def _walk_with_breadcrumb(node: DocNode, crumbs: list[str], max_tokens: int, overlap: int) -> list[Chunk]:
    out: list[Chunk] = []
    new_crumbs = crumbs
    if node.kind == "heading" and node.text:
        new_crumbs = crumbs + [node.text]
        return out  # heading text itself isn't a chunk; it becomes breadcrumb
    if node.kind == "paragraph":
        ch = _split_paragraph(node, max_tokens, overlap)
        for c in ch:
            c.breadcrumb = list(crumbs)
        return ch
    if node.kind == "list":
        text = "\n".join(f"- {(c.text or '').strip()}" for c in node.children)
        if not text:
            return []
        return [Chunk(
            text=text, breadcrumb=list(crumbs),
            page_range=_bbox_pages(node),
            source_ids=[node.id] + [c.id for c in node.children],
            kind_summary="list",
        )]
    if node.kind == "table":
        ch = _split_table(node, max_tokens)
        for c in ch:
            c.breadcrumb = list(crumbs)
        return ch

    # Recurse for containers (document, page, section, cell).
    for child in node.children:
        out.extend(_walk_with_breadcrumb(child, new_crumbs, max_tokens, overlap))
    return out


def chunk_tree(tree: DocNode, max_tokens: int = 800, overlap: int = 100) -> list[Chunk]:
    return _walk_with_breadcrumb(tree, crumbs=[], max_tokens=max_tokens, overlap=overlap)
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_chunk.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/chunk.py tests/test_chunk.py
git commit -m "feat(chunk): RAG chunker with breadcrumb, table row-group splits, paragraph overlap"
```

---

## Task 20: Add fixtures `04_multi_column` and `05_sections_lists`

**Files:**
- Modify: `tests/fixtures/build_pdfs.py`
- Modify: `tests/fixtures/test_fixtures_deterministic.py`

- [ ] **Step 1: Add the two builders**

Append to `tests/fixtures/build_pdfs.py`:

```python
from reportlab.platypus import KeepInFrame
from reportlab.lib.pagesizes import LETTER
from reportlab.platypus.flowables import BalancedColumns


def build_04_multi_column(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    long_text = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 8
    flow = [Paragraph(long_text, s["BodyText"]) for _ in range(4)]
    story = [
        Paragraph("Two-Column Layout", s["Heading1"]),
        Spacer(1, 12),
        BalancedColumns(flow, nCols=2),
    ]
    doc.build(story)


def build_05_sections_lists(out: Path) -> None:
    from reportlab.platypus import ListFlowable, ListItem
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    story = [
        Paragraph("Sections And Lists", s["Heading1"]),
        Spacer(1, 8),
        Paragraph("1. Background", s["Heading2"]),
        Paragraph("This is the background paragraph.", s["BodyText"]),
        Spacer(1, 6),
        Paragraph("2. Findings", s["Heading2"]),
        ListFlowable(
            [ListItem(Paragraph(t, s["BodyText"])) for t in ("First finding.", "Second finding.", "Third finding.")],
            bulletType="bullet",
        ),
        Spacer(1, 8),
        Paragraph("2.1 Detail Table", s["Heading3"]),
        Table(
            [["A", "B"], ["1", "2"], ["3", "4"]],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]),
        ),
    ]
    doc.build(story)
```

Update `BUILDERS`:

```python
BUILDERS = {
    "01_simple_table": build_01_simple_table,
    "02_nested_table": build_02_nested_table,
    "03_page_spanning": build_03_page_spanning,
    "04_multi_column": build_04_multi_column,
    "05_sections_lists": build_05_sections_lists,
}
```

- [ ] **Step 2: Generate**

Run: `python -m tests.fixtures.build_pdfs`
Expected: both `tests/golden/synthetic/04_multi_column/source.pdf` and `tests/golden/synthetic/05_sections_lists/source.pdf` exist.

- [ ] **Step 3: Add determinism tests**

Append to `tests/fixtures/test_fixtures_deterministic.py`:

```python
def test_multi_column_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["04_multi_column"](a)
    BUILDERS["04_multi_column"](b)
    assert _digest(a) == _digest(b)


def test_sections_lists_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["05_sections_lists"](a)
    BUILDERS["05_sections_lists"](b)
    assert _digest(a) == _digest(b)
```

Run: `pytest tests/fixtures/test_fixtures_deterministic.py -v`
Expected: All PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/build_pdfs.py tests/fixtures/test_fixtures_deterministic.py tests/golden/synthetic/04_multi_column/source.pdf tests/golden/synthetic/05_sections_lists/source.pdf
git commit -m "test(fixtures): add 04_multi_column and 05_sections_lists fixtures"
```

---

## Task 21: Golden tree fixtures + Layer 2 regression test

**Files:**
- Create: `scripts/update_goldens.py`
- Create: `tests/test_golden.py`
- Create: `tests/golden/synthetic/01_simple_table/expected_tree.json` (generated)
- Create: `tests/golden/synthetic/02_nested_table/expected_tree.json` (generated)
- Create: `tests/golden/synthetic/03_page_spanning/expected_tree.json` (generated)
- Create: `tests/golden/synthetic/04_multi_column/expected_tree.json` (generated)
- Create: `tests/golden/synthetic/05_sections_lists/expected_tree.json` (generated)

- [ ] **Step 1: Implement `scripts/update_goldens.py`**

Create `scripts/update_goldens.py`:

```python
"""Regenerate expected_tree.json and expected_skeleton.json for golden cases.

Usage:
    python scripts/update_goldens.py [--case <name>] [--all]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json

ROOT = Path(__file__).resolve().parents[1]
SYNTH = ROOT / "tests" / "golden" / "synthetic"


def _skeleton(node):
    out = {"kind": node["kind"]}
    if node.get("text"):
        out["text"] = node["text"]
    children = node.get("children") or []
    if children:
        out["children"] = [_skeleton(c) for c in children]
    return out


def _strip_bbox_noise(obj):
    """Round bbox floats to 1pt for stable diffs."""
    if isinstance(obj, dict):
        if set(obj.keys()) >= {"page", "x0", "y0", "x1", "y1"}:
            return {**obj,
                    "x0": round(obj["x0"]),
                    "y0": round(obj["y0"]),
                    "x1": round(obj["x1"]),
                    "y1": round(obj["y1"])}
        return {k: _strip_bbox_noise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_bbox_noise(v) for v in obj]
    return obj


def update_case(case_dir: Path) -> None:
    pdf = case_dir / "source.pdf"
    if not pdf.exists():
        raise SystemExit(f"missing {pdf}")
    tree = parse(pdf)
    full = json.loads(to_json(tree))
    full = _strip_bbox_noise(full)
    (case_dir / "expected_tree.json").write_text(json.dumps(full, indent=2, sort_keys=True) + "\n")
    (case_dir / "expected_skeleton.json").write_text(json.dumps(_skeleton(full), indent=2, sort_keys=True) + "\n")
    print(f"updated {case_dir.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="case name to update")
    ap.add_argument("--all", action="store_true", help="update all cases")
    args = ap.parse_args()
    cases = sorted(SYNTH.iterdir()) if args.all else [SYNTH / args.case] if args.case else []
    if not cases:
        ap.error("specify --case <name> or --all")
    for c in cases:
        update_case(c)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate goldens for all cases**

Run: `python scripts/update_goldens.py --all`
Expected: prints `updated 01_simple_table`, etc.; creates `expected_tree.json` and `expected_skeleton.json` in each case dir.

- [ ] **Step 3: Inspect the generated trees**

Run: `head -40 tests/golden/synthetic/01_simple_table/expected_skeleton.json`
Expected: a JSON skeleton with `document → page → heading + table` structure.

Spot-check `02_nested_table`'s skeleton shows a `table → row → cell → table` chain:
Run: `python -c "import json; d=json.load(open('tests/golden/synthetic/02_nested_table/expected_skeleton.json')); import pprint; pprint.pp(d)" | head -60`
Expected: see at least one `cell` whose `children` includes a `table`.

- [ ] **Step 4: Write the Layer 2 regression test**

Create `tests/test_golden.py`:

```python
"""Layer 2: parse each golden PDF, assert tree equals committed expected_tree.json."""

import json
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json
from scripts.update_goldens import _strip_bbox_noise

CASES_DIR = Path("tests/golden/synthetic")
CASES = sorted(p.name for p in CASES_DIR.iterdir() if (p / "source.pdf").exists())


@pytest.mark.parametrize("case", CASES)
def test_tree_matches_expected(case):
    case_dir = CASES_DIR / case
    pdf = case_dir / "source.pdf"
    expected = json.loads((case_dir / "expected_tree.json").read_text())
    got = _strip_bbox_noise(json.loads(to_json(parse(pdf))))
    assert got == expected, (
        f"Tree drift in {case}. "
        f"Run `python scripts/update_goldens.py --case {case}` and review the diff."
    )
```

- [ ] **Step 5: Run the golden tests**

Run: `pytest tests/test_golden.py -v`
Expected: All PASS (since we just generated the goldens from the same parser).

- [ ] **Step 6: Commit**

```bash
git add scripts/update_goldens.py tests/test_golden.py tests/golden/synthetic/*/expected_tree.json tests/golden/synthetic/*/expected_skeleton.json
git commit -m "feat(tests): Layer 2 golden corpus regression test + update_goldens.py"
```

---

## Task 22: Layer 3 hierarchy-equivalence test

**Files:**
- Create: `tests/test_hierarchy.py`

- [ ] **Step 1: Write the Layer 3 test**

Create `tests/test_hierarchy.py`:

```python
"""Layer 3: tree_skeleton(parse(pdf)) == expected_skeleton.json.

Drops bboxes, ids, attrs, provenance. Only kind/text/children remain.
This is the laser-focused check for 'tables-within-tables stayed nested.'
"""

import json
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json
from scripts.update_goldens import _skeleton

CASES_DIR = Path("tests/golden/synthetic")
CASES = sorted(p.name for p in CASES_DIR.iterdir() if (p / "source.pdf").exists())


def _tree_skeleton(tree):
    return _skeleton(json.loads(to_json(tree)))


@pytest.mark.parametrize("case", CASES)
def test_skeleton_matches_expected(case):
    case_dir = CASES_DIR / case
    pdf = case_dir / "source.pdf"
    expected = json.loads((case_dir / "expected_skeleton.json").read_text())
    got = _tree_skeleton(parse(pdf))
    assert got == expected, (
        f"Hierarchy drift in {case}. "
        f"Inspect tests/golden/synthetic/{case}/expected_skeleton.json vs current parse output."
    )


def test_nested_table_is_in_skeleton():
    """Sanity: 02_nested_table's skeleton actually has a table inside a cell."""
    sk = json.loads((CASES_DIR / "02_nested_table" / "expected_skeleton.json").read_text())

    def has_nested_table(node):
        if node.get("kind") == "cell":
            if any(c.get("kind") == "table" for c in node.get("children", [])):
                return True
        for c in node.get("children", []):
            if has_nested_table(c):
                return True
        return False

    assert has_nested_table(sk), "02_nested_table skeleton lost its nested table"
```

- [ ] **Step 2: Run the hierarchy tests**

Run: `pytest tests/test_hierarchy.py -v`
Expected: All PASS, including `test_nested_table_is_in_skeleton`.

- [ ] **Step 3: Commit**

```bash
git add tests/test_hierarchy.py
git commit -m "test(hierarchy): Layer 3 skeleton equivalence + nested-table sanity guard"
```

---

## Task 23: CLI

**Files:**
- Create: `pdf_parser/cli.py`
- Create: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli.py`:

```python
import json
from pathlib import Path

from typer.testing import CliRunner

from pdf_parser.cli import app

runner = CliRunner()
SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_parse_json_default():
    result = runner.invoke(app, ["parse", str(SIMPLE)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["kind"] == "document"


def test_parse_markdown():
    result = runner.invoke(app, ["parse", str(SIMPLE), "--format", "markdown"])
    assert result.exit_code == 0
    assert "# Simple Table Example" in result.stdout


def test_parse_html():
    result = runner.invoke(app, ["parse", str(SIMPLE), "--format", "html"])
    assert result.exit_code == 0
    assert "<article>" in result.stdout


def test_parse_chunks():
    result = runner.invoke(app, ["parse", str(SIMPLE), "--format", "chunks"])
    assert result.exit_code == 0
    chunks = json.loads(result.stdout)
    assert isinstance(chunks, list) and chunks


def test_validate_only_exits_zero_on_good_pdf():
    result = runner.invoke(app, ["parse", str(SIMPLE), "--validate-only"])
    assert result.exit_code == 0
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_cli.py -v`
Expected: ImportError on `pdf_parser.cli`.

- [ ] **Step 3: Implement `pdf_parser/cli.py`**

```python
"""CLI: pdf-parser parse <path> [--format ...]."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import typer

from pdf_parser.chunk import chunk_tree
from pdf_parser.pipeline import parse as parse_pdf
from pdf_parser.render.html import to_html
from pdf_parser.render.json_ import to_json
from pdf_parser.render.markdown import to_markdown
from pdf_parser.validate.report import validate

app = typer.Typer(add_completion=False, help="Deterministic PDF parser.")


@app.command()
def parse(
    path: Path,
    format: str = typer.Option("json", "--format", "-f",
                               help="json | markdown | html | chunks"),
    validate_only: bool = typer.Option(False, "--validate-only"),
    enable_llm_fallback: bool = typer.Option(False, "--enable-llm-fallback"),
    visualize: Optional[Path] = typer.Option(None, "--visualize"),
) -> None:
    tree = parse_pdf(path)

    if enable_llm_fallback:
        # Wired in Task 25; for now warn that this is a no-op.
        typer.echo("warning: --enable-llm-fallback set but fallback module not invoked in v1",
                   err=True)

    if validate_only:
        report = validate(tree, path)
        for e in report.errors:
            typer.echo(e, err=True)
        raise typer.Exit(code=0 if report.passed else 1)

    if visualize is not None:
        from scripts.visualize import render_overlays
        render_overlays(path, tree, visualize)

    if format == "json":
        typer.echo(to_json(tree, indent=2))
    elif format == "markdown":
        typer.echo(to_markdown(tree))
    elif format == "html":
        typer.echo(to_html(tree))
    elif format == "chunks":
        chunks = chunk_tree(tree)
        typer.echo(json.dumps([c.model_dump() for c in chunks], indent=2))
    else:
        typer.echo(f"unknown format: {format}", err=True)
        raise typer.Exit(code=2)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_cli.py -v`
Expected: All PASS.

The `--visualize` import is forward-referenced (Task 24) but is in a code path only triggered by the flag; tests don't exercise it.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/cli.py tests/test_cli.py
git commit -m "feat(cli): pdf-parser parse with json/markdown/html/chunks + validate-only"
```

---

## Task 24: `scripts/visualize.py`

**Files:**
- Create: `scripts/__init__.py`
- Create: `scripts/visualize.py`
- Create: `tests/test_visualize.py`

- [ ] **Step 1: Create empty `scripts/__init__.py`**

Create empty file `scripts/__init__.py`.

- [ ] **Step 2: Write failing tests**

Create `tests/test_visualize.py`:

```python
from pathlib import Path

from pdf_parser.pipeline import parse
from scripts.visualize import render_overlays

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_visualize_writes_one_png_per_page(tmp_path):
    tree = parse(SIMPLE)
    render_overlays(SIMPLE, tree, tmp_path)
    pngs = sorted(tmp_path.glob("page_*.png"))
    assert len(pngs) == 1
    assert pngs[0].stat().st_size > 0
```

- [ ] **Step 3: Run tests to verify failure**

Run: `pytest tests/test_visualize.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement `scripts/visualize.py`**

```python
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
```

- [ ] **Step 5: Run tests to verify pass**

Run: `pytest tests/test_visualize.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/__init__.py scripts/visualize.py tests/test_visualize.py
git commit -m "feat(visualize): render bbox overlays per page for human spot-checking"
```

---

## Task 25: LLM fallback skeleton (opt-in)

**Files:**
- Create: `pdf_parser/fallback/llm.py`
- Create: `tests/test_fallback.py`
- Modify: `pdf_parser/pipeline.py` (add a `with_fallback` variant)

- [ ] **Step 1: Write failing tests**

Create `tests/test_fallback.py`:

```python
"""LLM fallback is opt-in. By default it is never invoked.

These tests check the wiring without making real network calls: a fake LLM
client is injected; we assert the fallback is called only when validation fails
and only for the failing region.
"""

from pathlib import Path
from unittest.mock import MagicMock

from pdf_parser.fallback.llm import LLMFallback, fallback_for_region
from pdf_parser.model import BBox, DocNode

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_fallback_disabled_by_default():
    fb = LLMFallback()
    assert fb.enabled is False


def test_fallback_calls_client_when_invoked():
    client = MagicMock()
    client.parse_region.return_value = DocNode(
        kind="paragraph",
        bbox=BBox(page=0, x0=0, y0=0, x1=10, y1=10),
        text="fallback text",
    )
    fb = LLMFallback(client=client, enabled=True)
    region = BBox(page=0, x0=10, y0=10, x1=100, y1=100)
    out = fallback_for_region(fb, SIMPLE, region)
    assert out.text == "fallback text"
    assert client.parse_region.called


def test_fallback_returns_none_when_disabled():
    fb = LLMFallback(enabled=False)
    region = BBox(page=0, x0=10, y0=10, x1=100, y1=100)
    assert fallback_for_region(fb, SIMPLE, region) is None
```

- [ ] **Step 2: Run tests to verify failure**

Run: `pytest tests/test_fallback.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement `pdf_parser/fallback/llm.py`**

```python
"""Per-page LLM fallback. Off by default. Anthropic SDK loaded lazily."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from pdf_parser.model import BBox, DocNode


class LLMClient(Protocol):
    def parse_region(self, pdf_path: Path, region: BBox) -> DocNode: ...


@dataclass
class LLMFallback:
    enabled: bool = False
    client: Optional[LLMClient] = None
    audit_log: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.audit_log is None:
            self.audit_log = []


def fallback_for_region(fb: LLMFallback, pdf_path: Path, region: BBox) -> Optional[DocNode]:
    if not fb.enabled or fb.client is None:
        return None
    node = fb.client.parse_region(pdf_path, region)
    fb.audit_log.append({"page": region.page, "bbox": region.model_dump(), "result_id": node.id})
    return node
```

- [ ] **Step 4: Run tests to verify pass**

Run: `pytest tests/test_fallback.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/fallback/llm.py tests/test_fallback.py
git commit -m "feat(fallback): opt-in LLM fallback skeleton with audit log; off by default"
```

---

## Task 26: Final integration sweep + README polish

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Run the full test suite**

Run: `pytest -v`
Expected: All tests PASS across `tests/test_model.py`, `tests/fixtures/`, `tests/stages/`, `tests/validate/`, `tests/render/`, `tests/test_pipeline.py`, `tests/test_chunk.py`, `tests/test_golden.py`, `tests/test_hierarchy.py`, `tests/test_cli.py`, `tests/test_visualize.py`, `tests/test_fallback.py`.

- [ ] **Step 2: Sanity-check CLI end-to-end on each fixture**

Run each, expect non-empty output and exit code 0:

```bash
pdf-parser parse tests/golden/synthetic/01_simple_table/source.pdf --format json | head -20
pdf-parser parse tests/golden/synthetic/02_nested_table/source.pdf --format markdown | head -20
pdf-parser parse tests/golden/synthetic/03_page_spanning/source.pdf --format chunks | head -20
pdf-parser parse tests/golden/synthetic/04_multi_column/source.pdf --format html | head -20
pdf-parser parse tests/golden/synthetic/05_sections_lists/source.pdf --validate-only
```

- [ ] **Step 3: Expand `README.md`**

Replace `README.md` content:

```markdown
# pdf-parser

Deterministic, layout-first PDF parser. Produces a hierarchical `DocNode` tree
that preserves nested tables and tables that overflow across pages. Renders to
JSON / Markdown / HTML and emits RAG chunks.

See `docs/superpowers/specs/2026-05-19-pdf-parser-design.md` for the design.

## Setup

    uv venv && source .venv/bin/activate
    uv pip install -e ".[dev]"

(Add `[llm]` if you want to opt into the per-page LLM fallback.)

## CLI

    pdf-parser parse <path>                            # json (default)
    pdf-parser parse <path> --format markdown|html|chunks
    pdf-parser parse <path> --validate-only            # exit non-zero on invariant failures
    pdf-parser parse <path> --enable-llm-fallback      # opt-in per doc
    pdf-parser parse <path> --visualize <out-dir>      # write bbox overlay PNGs

## Tests

    pytest                                  # everything
    pytest tests/test_golden.py             # Layer 2 regression
    pytest tests/test_hierarchy.py          # Layer 3 hierarchy

When a parse change shifts a golden tree intentionally:

    python scripts/update_goldens.py --case <name>
    # review the git diff — that IS the review

## Fixtures

`tests/fixtures/build_pdfs.py` generates the synthetic corpus deterministically
(pinned `reportlab`). To regenerate:

    python -m tests.fixtures.build_pdfs
```

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -m "docs: expand README with setup, CLI, test, and fixture-regen instructions"
```

---

## Self-Review

After every task above is complete, run this checklist:

**Spec coverage:**
- §3 Data model → Task 2 ✓
- §4 Pipeline stages 1–6 → Tasks 4–11 ✓
- §4 Stage 7 validate → Tasks 13–14 ✓
- §4 Stage 8 render → Tasks 15–17 ✓
- §5.1 Cross-page stitching → Task 10 ✓
- §5.2 Nested-table detection → Task 8 ✓
- §6 Layer 1 invariants + coverage → Tasks 13–14 ✓
- §6 Layer 2 golden corpus → Tasks 3, 7, 9, 20, 21 ✓
- §6 Layer 3 hierarchy test → Task 22 ✓
- §6 LLM fallback in validation → Task 25 (skeleton; full retry-on-invariant-failure loop deferred — v1 ships with manual review path) ✓
- §6 Tooling (`pytest`, `hypothesis`, `visualize.py`, `update_goldens.py`) → Tasks 18, 21, 24 ✓
- §7 Project layout → Tasks 1, 2, 4–17, 19, 21, 23–25 ✓
- §8 Dependencies → Task 1 ✓
- §9 Determinism → Task 12 test `test_parse_deterministic_same_id`, Task 3 byte-stable fixtures ✓
- §10 CLI → Task 23 ✓
- RAG chunking (§3) → Task 19 ✓

**Known carry-over (acceptable for v1):**
- LLM fallback's full *automatic retry on Layer 1 failure* is not wired into `pipeline.parse`. The skeleton (Task 25) and the `--enable-llm-fallback` flag exist, but the validator-driven invocation loop is deferred; the CLI prints a warning when the flag is set. Add this once a real corpus surfaces a page that needs it.
- `tests/golden/real/` is created lazily — no real PDFs committed in v1. Real cases get added when production surfaces them.

**Placeholder scan:** No "TBD" or "implement later" steps; every code step has runnable code.

**Type consistency:** `DocNode`, `BBox`, `Chunk` names match across tasks. `parse(path) → DocNode` is the single entry point. `to_json`, `to_markdown`, `to_html` follow consistent signatures.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-19-pdf-parser.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using `superpowers:executing-plans`, batch execution with checkpoints.

Which approach?
