# Bottom-Up Cell Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 5-detector table-detection cascade (`detect_tables.py` ~1008 LoC + `detect_tables_anchor.py` ~506 LoC + `extract_tables.py` ~881 LoC) with a single bottom-up pipeline: one `detect_cells` primitive (line / gutter / text evidence) feeds one `aggregate_tables` clusterer that emits the same `DocNode(kind='table')` tree.

**Architecture:** New stages `detect_cells.py` → `aggregate_tables.py` → `extract_tables_v2.py` produce per-page tables whose contract (`bbox`, `header_signature`, `page_height`, `covered`) is byte-identical to today's `extract_tables.py`, so `stitch_pages.py` and `build_tree.py` are reused unchanged. Built behind a `use_bottom_up=False` flag; flipped to default only after all 27 golden fixtures pass an id-set parity test against the legacy path. The cascade is deleted only in the final phase.

**Tech Stack:** Python 3.11+, `pdfplumber==0.11.4` (lines + words), `pypdfium2` (already vendored), `pydantic==2.9.2` (`DocNode`/`BBox`), `pytest==8.3.3` (parametrized + xfail), `uv` for env.

---

## File Map

**Create:**
- `pdf_parser/stages/detect_cells.py` — `Cell` dataclass + three sub-detectors (line / gutter / text) + `detect_cells(page, page_index) -> list[Cell]`.
- `pdf_parser/stages/aggregate_tables.py` — `CellTable` dataclass + `aggregate(cells, page_height) -> list[CellTable]` (rowing, columning, table clustering, recursive containment for nesting).
- `pdf_parser/stages/extract_tables_v2.py` — `extract_tables(pdf_path, *, pdf=None) -> list[DocNode]`, wiring `detect_cells` + `aggregate_tables` and emitting the same DocNode shape as today's `extract_tables.py`.
- `tests/test_bottom_up_parity.py` — parametrized id-set parity test across all 27 fixtures (xfail-by-default, flipped one fixture at a time).
- `tests/stages/test_detect_cells.py`, `tests/stages/test_aggregate_tables.py`, `tests/stages/test_extract_tables_v2.py` — focused unit tests.

**Modify:**
- `pdf_parser/pipeline.py` — add `use_bottom_up: bool = False` to `parse()`; when True, call `extract_tables_v2.extract_tables` and skip `augment_with_anchor_tables`.
- `pdf_parser/cli.py` — add `--bottom-up / --no-bottom-up` flag, default False.

**Touch only in Phase 10 (cleanup):** `detect_tables.py`, `detect_tables_anchor.py`, `extract_tables.py`, `pipeline.py`, `cli.py`, `README.md`, `docs/anchor_detector.md`.

**Verify-but-do-not-touch:** `stages/stitch_pages.py`, `stages/build_tree.py`, `stages/segment.py`, `stages/ingest.py`, `model.py`, `render/`, `chunk.py`, `validate/`.

---

## Output Contract (mandatory for `extract_tables_v2`)

Each `DocNode` returned from `extract_tables_v2.extract_tables` MUST satisfy:

- `kind="table"`, children all `kind="row"`, grandchildren all `kind="cell"` (enforced by `DocNode._check_child_kinds`).
- `bbox: BBox` (single page) — `stitch_pages.py` later promotes cross-page tables to `list[BBox]`.
- `attrs`:
  - `"n_rows": int`, `"n_cols": int`
  - `"header_signature": tuple[str, ...]` — first row's cell texts, used by `stitch_pages._header_signature`.
  - `"page": int`, `"page_height": float` — used by `stitch_pages._can_merge`.
- `provenance={"extractor": "bottom_up", "stage": "extract_tables_v2"}` — `stitch_pages._source_extractor` uses this to keep intra-extractor stitching.
- Row nodes: `attrs={"page": int, "row_index": int}` and `bbox=table_bbox` (matches today's `extract_tables._build_table`).
- Cell nodes: `attrs={"align": "left"|"right"}`; cells covered by a prior merged cell get `attrs["covered"]=True`; `text=str` when leaf, `text=None` when `children` non-empty.

**The `id` field on every node is derived by `DocNode._compute_id` from `kind|rounded_bbox|text|child_ids` — so id-set parity against the legacy path requires byte-identical bboxes (rounded to 1pt), text, and child structure.**

---

## The 27 Golden Fixtures (verified against `tests/golden/synthetic/`)

```
01_simple_table                          15_multicolumn_text
02_nested_table                          16_text_between_subtables
03_page_spanning                         17_text_between_subtables_spanning
04_multi_column                          18_ruled_header_open_body
05_sections_lists                        19_ruled_header_framed_body
06_page_spanning_no_header_repeat        20_ruled_header_row_strips
07_page_spanning_with_nested             21_vertical_merge_invisible_lines
08_page_spanning_subtable_split          22_text_between_adjacent_tables
09_mixed_toc_and_spanning_table          23_bordered_cell_with_bulleted_prose
10_merged_cells                          24_subtable_flush_outer_edges
11_pl_statement                          25_subtable_flush_outer_vertical_only
12_image_chart                           26_spanning_subtable_flush_at_break
13_comprehensive                         14b_borderless_long_text
14_borderless_table                      14c_borderless_long_text_spanning
```

(Verified at planning time: `tests/golden/synthetic/` contains exactly these 27 directories, each with `source.pdf`, `expected_tree.json`, `expected_skeleton.json`.)

Per-phase parity assignment (each fixture appears at least once):

| Phase | Fixtures reaching parity |
|---|---|
| 1 Foundation | `01_simple_table`, `05_sections_lists`, `12_image_chart` |
| 2 Gutters | `14_borderless_table`, `14b_borderless_long_text`, `15_multicolumn_text` (negative — must NOT detect a table) |
| 3 Aggregation | `03_page_spanning` (single-page slices), `04_multi_column`, `06_page_spanning_no_header_repeat` (single-page slices), `10_merged_cells` (basic), `11_pl_statement`, `22_text_between_adjacent_tables` |
| 4 Nesting via containment | `02_nested_table`, `16_text_between_subtables`, `17_text_between_subtables_spanning` (single-page) |
| 5 Ruled-header + framed-body | `18_ruled_header_open_body`, `19_ruled_header_framed_body`, `20_ruled_header_row_strips`, `23_bordered_cell_with_bulleted_prose` |
| 6 Vertical merges + covered | `10_merged_cells` (full), `21_vertical_merge_invisible_lines` |
| 7 Flush sub-tables | `24_subtable_flush_outer_edges`, `25_subtable_flush_outer_vertical_only`, `26_spanning_subtable_flush_at_break` (single-page) |
| 8 Cross-page stitching | `03_page_spanning`, `06_page_spanning_no_header_repeat`, `07_page_spanning_with_nested`, `08_page_spanning_subtable_split`, `09_mixed_toc_and_spanning_table`, `14c_borderless_long_text_spanning`, `17_text_between_subtables_spanning`, `26_spanning_subtable_flush_at_break` |
| 9 Omnibus | `13_comprehensive` |

---

## Hard Constraints

- MUST keep all 27 fixtures green throughout. The `use_bottom_up=False` default + `extract_tables.py` untouched until Phase 10 guarantees this.
- MUST NOT touch `stitch_pages.py` or `build_tree.py`. Their interfaces are the source of truth for what `extract_tables_v2` must emit.
- MUST NOT touch `detect_tables.py`, `detect_tables_anchor.py`, or `extract_tables.py` until Phase 10.
- MUST preserve every field listed in **Output Contract** above.
- TDD: each task starts with a failing test, then minimal impl, then verification, then commit. Frequent small commits, never batched.
- Verification: `pytest <specific test path>` per step; full `pytest` only at phase boundaries and Phase 10.

---

# Phase 0 — Plumbing the Flag

### Task 0.1: Add `use_bottom_up` parameter to `parse()` (no-op default)

**Files:**
- Modify: `pdf_parser/pipeline.py`
- Test: `tests/test_pipeline_bottom_up_flag.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_bottom_up_flag.py
"""use_bottom_up flag is accepted by parse() and defaults to False (legacy path)."""
import inspect
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse


def test_parse_accepts_use_bottom_up_kwarg():
    sig = inspect.signature(parse)
    assert "use_bottom_up" in sig.parameters
    assert sig.parameters["use_bottom_up"].default is False


def test_parse_default_path_unchanged():
    """With the flag off, output equals legacy output (identity)."""
    pdf = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    legacy = parse(pdf)
    flagged = parse(pdf, use_bottom_up=False)
    assert legacy.id == flagged.id
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_pipeline_bottom_up_flag.py -v
```

Expected: FAIL — `use_bottom_up` not in signature.

- [ ] **Step 3: Add the keyword-only parameter (no-op for now)**

In `pdf_parser/pipeline.py`, change the `parse` signature and route:

```python
def parse(
    pdf_path: Path | str,
    llm_fallback: Optional["LLMFallback"] = None,
    *,
    use_anchor: bool = True,
    use_bottom_up: bool = False,
) -> DocNode:
    """Parse ``pdf_path`` and return the document tree.

    ``use_bottom_up`` (default False) selects the bottom-up cell-clustering
    extractor (:mod:`pdf_parser.stages.extract_tables_v2`) in place of the
    legacy cascade.  When True, ``use_anchor`` is ignored — bottom-up
    subsumes the anchor detector's borderless-table recovery.
    """
    pdf_path = Path(pdf_path)
    raw_pages = ingest(pdf_path)
    segments  = segment(raw_pages)

    with pdfplumber.open(str(pdf_path)) as pdf:
        if use_bottom_up:
            from pdf_parser.stages.extract_tables_v2 import extract_tables as extract_tables_v2
            tables = extract_tables_v2(pdf_path, pdf=pdf)
        else:
            tables = extract_tables(pdf_path, pdf=pdf)
            if use_anchor:
                tables = augment_with_anchor_tables(tables, pdf_path, pdf=pdf)

    tables = stitch_tables(tables)
    tree   = build_tree(segments, tables)

    if llm_fallback is not None and llm_fallback.enabled:
        tree = _apply_llm_fallback(tree, pdf_path, llm_fallback, raw_pages)

    return tree
```

The `extract_tables_v2` import is lazy so Task 0.1 passes before the module exists.

- [ ] **Step 4: Add a stub module so the lazy import resolves later**

```bash
mkdir -p pdf_parser/stages
```

Write `pdf_parser/stages/extract_tables_v2.py`:

```python
"""Stage 4 (bottom-up): cell-clustering table extractor. Stub — Task 1.x fills this in."""
from __future__ import annotations

from pathlib import Path

from pdf_parser.model import DocNode


def extract_tables(pdf_path: Path, *, pdf=None) -> list[DocNode]:
    """Return an empty list. Real implementation in Phase 1+."""
    return []
```

- [ ] **Step 5: Re-run the test — should pass**

```bash
uv run pytest tests/test_pipeline_bottom_up_flag.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pdf_parser/pipeline.py pdf_parser/stages/extract_tables_v2.py tests/test_pipeline_bottom_up_flag.py
git commit -m "feat(pipeline): add use_bottom_up flag (default off) with stub extractor"
```

---

### Task 0.2: Add `--bottom-up / --no-bottom-up` CLI flag

**Files:**
- Modify: `pdf_parser/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add the failing assertion to `tests/test_cli.py`**

Append to `tests/test_cli.py`:

```python
def test_cli_bottom_up_flag_exists():
    from typer.testing import CliRunner
    from pdf_parser.cli import app
    runner = CliRunner()
    result = runner.invoke(app, ["parse", "--help"])
    assert result.exit_code == 0
    assert "--bottom-up" in result.stdout
    assert "--no-bottom-up" in result.stdout
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_cli.py::test_cli_bottom_up_flag_exists -v
```

Expected: FAIL — `--bottom-up` not in help output.

- [ ] **Step 3: Add the flag to `parse` in `cli.py`**

In `pdf_parser/cli.py`, add the option and pass it through:

```python
    bottom_up: bool = typer.Option(
        False, "--bottom-up/--no-bottom-up",
        help="Use the bottom-up cell-clustering extractor instead of the "
             "legacy detect_tables cascade. Default off; flip with --bottom-up "
             "for parity testing.",
    ),
) -> None:
    fb = None
    if enable_llm_fallback:
        from pdf_parser.fallback.llm import AnthropicLLMClient, LLMFallback
        fb = LLMFallback(enabled=True, client=AnthropicLLMClient())

    tree = parse_pdf(
        path,
        llm_fallback=fb,
        use_anchor=not no_anchor,
        use_bottom_up=bottom_up,
    )
```

- [ ] **Step 4: Re-run the test**

```bash
uv run pytest tests/test_cli.py -v
```

Expected: all CLI tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/cli.py tests/test_cli.py
git commit -m "feat(cli): add --bottom-up/--no-bottom-up flag, threaded into parse()"
```

---

### Task 0.3: Parity harness — parametrized xfail across all 27 fixtures

**Files:**
- Create: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Write the parity test, every fixture xfail-by-default**

```python
# tests/test_bottom_up_parity.py
"""Per-fixture parity: parse(use_bottom_up=False) == parse(use_bottom_up=True).

Each fixture is marked ``xfail(strict=False)`` until its phase reaches parity.
When a fixture starts passing (xpassed) the developer removes its xfail in the
same commit as the implementation change, so future regressions surface as
plain failures rather than silent xpasses.

After all 27 cases pass, Phase 10 deletes this file and flips the pipeline
default.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.model import DocNode
from pdf_parser.render.json_ import to_json
from scripts.update_goldens import _load_parser_config, _strip_bbox_noise

CASES_DIR = Path("tests/golden/synthetic")

# Fixtures move OUT of this set in the same commit that brings them to parity.
# Phase 10 deletes the set (and this file) once it is empty.
_XFAIL_CASES: set[str] = {
    "01_simple_table",
    "02_nested_table",
    "03_page_spanning",
    "04_multi_column",
    "05_sections_lists",
    "06_page_spanning_no_header_repeat",
    "07_page_spanning_with_nested",
    "08_page_spanning_subtable_split",
    "09_mixed_toc_and_spanning_table",
    "10_merged_cells",
    "11_pl_statement",
    "12_image_chart",
    "13_comprehensive",
    "14_borderless_table",
    "14b_borderless_long_text",
    "14c_borderless_long_text_spanning",
    "15_multicolumn_text",
    "16_text_between_subtables",
    "17_text_between_subtables_spanning",
    "18_ruled_header_open_body",
    "19_ruled_header_framed_body",
    "20_ruled_header_row_strips",
    "21_vertical_merge_invisible_lines",
    "22_text_between_adjacent_tables",
    "23_bordered_cell_with_bulleted_prose",
    "24_subtable_flush_outer_edges",
    "25_subtable_flush_outer_vertical_only",
    "26_spanning_subtable_flush_at_break",
}


def _all_ids(tree: DocNode) -> set[str]:
    out: set[str] = set()
    stack = [tree]
    while stack:
        n = stack.pop()
        out.add(n.id)
        stack.extend(n.children)
    return out


def _id_to_breadcrumb(tree: DocNode) -> dict[str, str]:
    """Map node.id → 'document>page[0]>table>row[2]>cell[1]' style path."""
    out: dict[str, str] = {}

    def walk(node: DocNode, crumbs: list[str]) -> None:
        out[node.id] = ">".join(crumbs) or node.kind
        for i, c in enumerate(node.children):
            walk(c, crumbs + [f"{c.kind}[{i}]"])

    walk(tree, [tree.kind])
    return out


def _format_diff(a: DocNode, b: DocNode) -> str:
    a_ids, b_ids = _all_ids(a), _all_ids(b)
    only_a, only_b = a_ids - b_ids, b_ids - a_ids
    crumbs_a, crumbs_b = _id_to_breadcrumb(a), _id_to_breadcrumb(b)
    lines = [
        f"  legacy_only ({len(only_a)}):",
        *(f"    {nid}  {crumbs_a[nid]}" for nid in sorted(only_a)),
        f"  bottom_up_only ({len(only_b)}):",
        *(f"    {nid}  {crumbs_b[nid]}" for nid in sorted(only_b)),
    ]
    return "\n".join(lines)


def _all_cases() -> list:
    cases = sorted(p.name for p in CASES_DIR.iterdir() if (p / "source.pdf").exists())
    out = []
    for c in cases:
        if c in _XFAIL_CASES:
            out.append(pytest.param(
                c,
                marks=pytest.mark.xfail(
                    strict=False,
                    reason=f"bottom-up parity pending for {c}",
                ),
            ))
        else:
            out.append(c)
    return out


@pytest.mark.parametrize("case", _all_cases())
def test_bottom_up_matches_legacy(case: str) -> None:
    case_dir = CASES_DIR / case
    pdf = case_dir / "source.pdf"
    cfg = _load_parser_config(case_dir)
    legacy = parse(pdf, **{**cfg, "use_bottom_up": False})
    new = parse(pdf, **{**cfg, "use_bottom_up": True})
    legacy_ids, new_ids = _all_ids(legacy), _all_ids(new)
    assert legacy_ids == new_ids, (
        f"\nbottom-up parity failed for {case}:\n{_format_diff(legacy, new)}"
    )
```

- [ ] **Step 2: Run it — expect 27 xfailed, 0 failed**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: `27 xfailed` (stub returns `[]`, so every fixture diverges; xfail absorbs).

- [ ] **Step 3: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test: bottom-up parity harness, all 27 fixtures xfail by default"
```

---

# Phase 1 — Foundation

### Task 1.1: `Cell` dataclass

**Files:**
- Create: `pdf_parser/stages/detect_cells.py`
- Test: `tests/stages/test_detect_cells.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stages/test_detect_cells.py
"""Cell dataclass shape: bbox, text, source, confidence."""
from pdf_parser.model import BBox
from pdf_parser.stages.detect_cells import Cell


def test_cell_holds_bbox_text_source_confidence():
    bb = BBox(page=0, x0=0, y0=0, x1=10, y1=10)
    c = Cell(bbox=bb, text="x", source="line", confidence=1.0)
    assert c.bbox == bb
    assert c.text == "x"
    assert c.source == "line"
    assert c.confidence == 1.0


def test_cell_source_is_constrained():
    bb = BBox(page=0, x0=0, y0=0, x1=10, y1=10)
    for src in ("line", "gutter", "text"):
        Cell(bbox=bb, text="", source=src, confidence=0.5)
```

- [ ] **Step 2: Run — expect ImportError**

```bash
uv run pytest tests/stages/test_detect_cells.py -v
```

Expected: FAIL — `Cell` not defined.

- [ ] **Step 3: Implement `Cell` and module skeleton**

Replace `pdf_parser/stages/detect_cells.py` with:

```python
"""Bottom-up cell primitive.

A *Cell* is any rectangular page region that holds (or could hold) one
logical table cell.  Three evidence sources, ordered by trust:

  * ``line``   — bounded by visible horizontal+vertical edges (highest).
  * ``gutter`` — bounded by persistent whitespace columns + line gaps.
  * ``text``   — pdfplumber text-strategy fallback (lowest, prose-guarded).

``detect_cells(page, page_index)`` is the only public entry point.  It
returns the union of all three sources; downstream
:mod:`pdf_parser.stages.aggregate_tables` deduplicates and clusters them
into tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pdf_parser.model import BBox

CellSource = Literal["line", "gutter", "text"]


@dataclass(frozen=True)
class Cell:
    bbox: BBox
    text: str
    source: CellSource
    confidence: float


def detect_cells(page, page_index: int) -> list[Cell]:
    """Return every candidate cell on ``page``.  Empty list = no tables here."""
    return []
```

- [ ] **Step 4: Re-run — should pass**

```bash
uv run pytest tests/stages/test_detect_cells.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/detect_cells.py tests/stages/test_detect_cells.py
git commit -m "feat(detect_cells): Cell dataclass and module skeleton"
```

---

### Task 1.2: Line-bounded cell detector

**Files:**
- Modify: `pdf_parser/stages/detect_cells.py`
- Modify: `tests/stages/test_detect_cells.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/stages/test_detect_cells.py`:

```python
from pathlib import Path
import pdfplumber

from pdf_parser.stages.detect_cells import detect_cells, _line_cells


def test_line_cells_on_01_simple_table():
    pdf_path = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _line_cells(page, page_index=0)
    # 01_simple_table = 4 rows × 3 cols = 12 line-bounded cells.
    assert len(cells) == 12
    assert all(c.source == "line" for c in cells)
    assert all(c.confidence == 1.0 for c in cells)
    # Header row contains "Name"/"Score"/"Grade" (any order in detected set).
    texts = {c.text for c in cells}
    assert {"Name", "Score", "Grade"} <= texts
```

- [ ] **Step 2: Run — expect ImportError**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_line_cells_on_01_simple_table -v
```

Expected: FAIL — `_line_cells` not defined.

- [ ] **Step 3: Implement `_line_cells`**

Append to `pdf_parser/stages/detect_cells.py`:

```python
# ---------------------------------------------------------------------------
# Line-bounded cells: pdfplumber's line strategy + visible-edge overdraw
# filtering (background-coloured strokes subtracted).  The overdraw logic is
# vendored from the legacy ``detect_tables._visible_edges`` so we do not
# import the to-be-deleted module.  When parity ships, the helpers live here.
# ---------------------------------------------------------------------------

_LINE_AXIS_TOL  = 0.5
_LINE_SNAP_TOL  = 1.0
_BG_COLOR_TOL   = 0.95
_MIN_CELL_AREA  = 1.0

_DEFAULT_TABLE_SETTINGS = {
    "vertical_strategy":    "lines",
    "horizontal_strategy":  "lines",
    "snap_tolerance":       3,
    "join_tolerance":       3,
    "edge_min_length":      3,
    "min_words_vertical":   1,
    "min_words_horizontal": 1,
}


def _is_background_color(c) -> bool:
    """True if ``c`` is at or near the page background (default: near-white)."""
    if c is None:
        return False
    try:
        seq = tuple(c) if not isinstance(c, (int, float)) else (float(c),)
    except TypeError:
        return False
    if len(seq) in (1, 3):
        return all(v >= _BG_COLOR_TOL for v in seq)
    if len(seq) == 4:  # CMYK
        return all(v <= (1.0 - _BG_COLOR_TOL) for v in seq)
    return False


def _visible_edges_local(page):
    """Return ``(h_lines, v_lines)`` with background-coloured overdraws removed.

    Direct port of ``detect_tables._visible_edges``.  Kept here so the
    bottom-up path stands alone.  See that module for the design notes.
    """
    from pdf_parser.stages.detect_tables import _visible_edges  # Phase 10 inlines this
    h, v, _ = _visible_edges(page)
    return h, v


def _line_cells(page, page_index: int) -> list[Cell]:
    settings = dict(_DEFAULT_TABLE_SETTINGS)
    h_vis, v_vis = _visible_edges_local(page)
    if len(h_vis) >= 2 and len(v_vis) >= 2:
        settings.update(
            vertical_strategy="explicit",
            horizontal_strategy="explicit",
            explicit_vertical_lines=v_vis,
            explicit_horizontal_lines=h_vis,
        )
    tables = page.find_tables(table_settings=settings)
    out: list[Cell] = []
    for t in tables:
        rows = t.extract()
        for r_idx, row in enumerate(t.rows):
            for c_idx, cbox in enumerate(row.cells):
                if cbox is None:
                    continue
                x0, y0, x1, y1 = cbox
                if (x1 - x0) * (y1 - y0) < _MIN_CELL_AREA:
                    continue
                text = (rows[r_idx][c_idx] if r_idx < len(rows)
                                              and c_idx < len(rows[r_idx])
                                              and rows[r_idx][c_idx] is not None
                                              else "")
                out.append(Cell(
                    bbox=BBox(page=page_index, x0=x0, y0=y0, x1=x1, y1=y1),
                    text=text.strip(),
                    source="line",
                    confidence=1.0,
                ))
    return out
```

And update `detect_cells` to use it:

```python
def detect_cells(page, page_index: int) -> list[Cell]:
    """Return every candidate cell on ``page``.  Empty list = no tables here."""
    cells: list[Cell] = []
    cells.extend(_line_cells(page, page_index))
    return cells
```

- [ ] **Step 4: Re-run the test**

```bash
uv run pytest tests/stages/test_detect_cells.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/detect_cells.py tests/stages/test_detect_cells.py
git commit -m "feat(detect_cells): line-bounded cell detector with overdraw filtering"
```

---

### Task 1.3: `aggregate_tables` skeleton + `CellTable` dataclass

**Files:**
- Create: `pdf_parser/stages/aggregate_tables.py`
- Create: `tests/stages/test_aggregate_tables.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stages/test_aggregate_tables.py
"""CellTable dataclass shape + empty-input contract for aggregate()."""
from pdf_parser.model import BBox
from pdf_parser.stages.aggregate_tables import CellTable, aggregate


def test_celltable_fields():
    bb = BBox(page=0, x0=0, y0=0, x1=10, y1=10)
    t = CellTable(
        page_index=0,
        bbox=bb,
        grid=[["a", "b"]],
        cell_bboxes=[[bb, bb]],
        covered=set(),
        header_signature=("a", "b"),
        page_height=792.0,
        nested=[],
        source="line",
    )
    assert t.grid == [["a", "b"]]
    assert t.covered == set()
    assert t.nested == []


def test_aggregate_empty_returns_empty():
    assert aggregate([], page_height=792.0) == []
```

- [ ] **Step 2: Run — expect ImportError**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: Implement the skeleton**

Write `pdf_parser/stages/aggregate_tables.py`:

```python
"""Cluster Cell records into tables.

Pipeline:
  1. Deduplicate cells by rounded bbox (line > gutter > text wins ties).
  2. Cluster cells by y-overlap into rows.
  3. Cluster rows by vertical adjacency into table candidates.
  4. Reject candidates < 2 rows or < 2 cols (single cells are not tables).
  5. Detect nesting via spatial containment: any cluster fully inside another
     cell's bbox becomes that cell's child ``CellTable``.
  6. Mark cells that are spanned over by a prior merged cell as covered.

Output: ``list[CellTable]`` per page.  The wiring layer
(:mod:`pdf_parser.stages.extract_tables_v2`) converts each ``CellTable`` to a
``DocNode(kind='table')`` with the contract documented in the plan header.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from pdf_parser.model import BBox
from pdf_parser.stages.detect_cells import Cell, CellSource


@dataclass
class CellTable:
    page_index: int
    bbox: BBox
    grid: list[list[str]]
    cell_bboxes: list[list[BBox]]
    covered: set[tuple[int, int]]
    header_signature: tuple[str, ...]
    page_height: float
    nested: list["CellTable"]
    source: CellSource


def aggregate(cells: list[Cell], page_height: float) -> list[CellTable]:
    """Group ``cells`` into tables. See module docstring."""
    if not cells:
        return []
    return []  # Tasks 3.x fill this in
```

- [ ] **Step 4: Re-run — should pass**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): CellTable dataclass + aggregate() skeleton"
```

---

### Task 1.4: `extract_tables_v2` — wire line-only path end-to-end

**Files:**
- Modify: `pdf_parser/stages/extract_tables_v2.py`
- Create: `tests/stages/test_extract_tables_v2.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stages/test_extract_tables_v2.py
"""extract_tables_v2 emits one DocNode per detected line-bounded table."""
from pathlib import Path

from pdf_parser.stages.extract_tables_v2 import extract_tables


def test_v2_on_01_simple_table_returns_one_table():
    pdf = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    tables = extract_tables(pdf)
    assert len(tables) == 1
    t = tables[0]
    assert t.kind == "table"
    assert t.attrs["n_rows"] == 4
    assert t.attrs["n_cols"] == 3
    assert t.attrs["header_signature"] == ("Name", "Score", "Grade")
    assert t.provenance == {"extractor": "bottom_up", "stage": "extract_tables_v2"}
    # row → cell hierarchy
    assert all(r.kind == "row" for r in t.children)
    assert all(c.kind == "cell" for r in t.children for c in r.children)


def test_v2_emits_no_tables_on_text_only_pdf():
    """05_sections_lists has no tables — extractor returns []."""
    pdf = Path("tests/golden/synthetic/05_sections_lists/source.pdf")
    assert extract_tables(pdf) == []
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_extract_tables_v2.py -v
```

Expected: FAIL — stub returns `[]`.

- [ ] **Step 3: Implement the line-only end-to-end wiring**

Replace `pdf_parser/stages/extract_tables_v2.py`:

```python
"""Stage 4 (bottom-up): detect_cells → aggregate_tables → DocNode trees."""
from __future__ import annotations

from pathlib import Path

import pdfplumber

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.aggregate_tables import CellTable, aggregate
from pdf_parser.stages.detect_cells import detect_cells

_PROVENANCE = {"extractor": "bottom_up", "stage": "extract_tables_v2"}


def extract_tables(pdf_path: Path, *, pdf=None) -> list[DocNode]:
    if pdf is not None:
        return _extract(pdf)
    with pdfplumber.open(str(pdf_path)) as opened:
        return _extract(opened)


def _extract(pdf) -> list[DocNode]:
    out: list[DocNode] = []
    for page_idx, page in enumerate(pdf.pages):
        cells = detect_cells(page, page_idx)
        tables = aggregate(cells, page_height=float(page.height))
        for t in tables:
            out.append(_celltable_to_docnode(t))
    return out


def _celltable_to_docnode(t: CellTable) -> DocNode:
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(t.grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (t.cell_bboxes[r_idx][c_idx]
                    if r_idx < len(t.cell_bboxes) and c_idx < len(t.cell_bboxes[r_idx])
                    else t.bbox)
            is_covered = (r_idx, c_idx) in t.covered
            attrs: dict = {"align": "left"}
            if is_covered:
                attrs["covered"] = True
            children = [_celltable_to_docnode(sub) for sub in t.nested
                        if _bbox_inside(sub.bbox, cbox)]
            cells.append(DocNode(
                kind="cell",
                bbox=cbox,
                text=text if not children else None,
                children=children,
                attrs=attrs,
                provenance=_PROVENANCE,
            ))
        rows.append(DocNode(
            kind="row",
            bbox=t.bbox,
            children=cells,
            attrs={"page": t.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=t.bbox,
        children=rows,
        attrs={
            "n_rows": len(t.grid),
            "n_cols": len(t.grid[0]) if t.grid else 0,
            "header_signature": t.header_signature,
            "page": t.page_index,
            "page_height": t.page_height,
        },
        provenance=_PROVENANCE,
    )


def _bbox_inside(inner: BBox, outer: BBox, tol: float = 2.0) -> bool:
    return (inner.page == outer.page
            and inner.x0 >= outer.x0 - tol and inner.y0 >= outer.y0 - tol
            and inner.x1 <= outer.x1 + tol and inner.y1 <= outer.y1 + tol)
```

The test still fails — `aggregate()` returns `[]`. We need a minimal aggregator.

- [ ] **Step 4: Implement minimal aggregate() that handles a single line-bounded grid**

Replace `aggregate()` in `pdf_parser/stages/aggregate_tables.py`:

```python
_ROW_Y_TOL = 2.0           # pt; two cells share a row if y-midpoints within this
_TABLE_GAP_MULT = 2.5      # pt; row gap > N × median row height ends a table


def _row_cluster(cells: list[Cell]) -> list[list[Cell]]:
    """Bucket cells into rows by y-midpoint (page-aware)."""
    by_page: dict[int, list[Cell]] = {}
    for c in cells:
        by_page.setdefault(c.bbox.page, []).append(c)
    rows: list[list[Cell]] = []
    for page_cells in by_page.values():
        page_cells.sort(key=lambda c: (c.bbox.y0, c.bbox.x0))
        current: list[Cell] = []
        cur_y: float | None = None
        for c in page_cells:
            ymid = (c.bbox.y0 + c.bbox.y1) / 2.0
            if cur_y is None or abs(ymid - cur_y) <= _ROW_Y_TOL:
                current.append(c)
                cur_y = ymid if cur_y is None else (cur_y + ymid) / 2.0
            else:
                rows.append(sorted(current, key=lambda c: c.bbox.x0))
                current = [c]
                cur_y = ymid
        if current:
            rows.append(sorted(current, key=lambda c: c.bbox.x0))
    return rows


def _split_into_tables(rows: list[list[Cell]]) -> list[list[list[Cell]]]:
    """Adjacent rows with similar geometry form one table candidate."""
    if not rows:
        return []
    tables: list[list[list[Cell]]] = [[rows[0]]]
    for r in rows[1:]:
        prev = tables[-1][-1]
        same_page = prev[0].bbox.page == r[0].bbox.page
        same_xrange = (abs(prev[0].bbox.x0 - r[0].bbox.x0) <= 4.0
                       and abs(prev[-1].bbox.x1 - r[-1].bbox.x1) <= 4.0)
        if same_page and same_xrange:
            tables[-1].append(r)
        else:
            tables.append([r])
    return tables


def _rows_to_celltable(
    rows: list[list[Cell]], page_height: float
) -> CellTable | None:
    if len(rows) < 2 or any(len(r) < 2 for r in rows):
        return None
    n_cols = max(len(r) for r in rows)
    grid: list[list[str]] = [
        [c.text for c in r] + [""] * (n_cols - len(r))
        for r in rows
    ]
    cell_bboxes: list[list[BBox]] = [
        [c.bbox for c in r]
        + [r[-1].bbox] * (n_cols - len(r))     # padding bboxes for ragged rows
        for r in rows
    ]
    page = rows[0][0].bbox.page
    x0 = min(c.bbox.x0 for r in rows for c in r)
    y0 = min(c.bbox.y0 for r in rows for c in r)
    x1 = max(c.bbox.x1 for r in rows for c in r)
    y1 = max(c.bbox.y1 for r in rows for c in r)
    return CellTable(
        page_index=page,
        bbox=BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1),
        grid=grid,
        cell_bboxes=cell_bboxes,
        covered=set(),
        header_signature=tuple(grid[0]),
        page_height=page_height,
        nested=[],
        source=rows[0][0].source,
    )


def aggregate(cells: list[Cell], page_height: float) -> list[CellTable]:
    if not cells:
        return []
    out: list[CellTable] = []
    for table_rows in _split_into_tables(_row_cluster(cells)):
        ct = _rows_to_celltable(table_rows, page_height)
        if ct is not None:
            out.append(ct)
    return out
```

- [ ] **Step 5: Re-run the test**

```bash
uv run pytest tests/stages/test_extract_tables_v2.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py pdf_parser/stages/extract_tables_v2.py tests/stages/test_extract_tables_v2.py
git commit -m "feat(aggregate_tables): minimal rower + clusterer; v2 end-to-end on 01_simple_table"
```

---

### Task 1.5: Phase-1 parity — 01, 05, 12

**Files:**
- Modify: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Confirm the three cases pass via bottom-up**

```bash
uv run pytest tests/test_bottom_up_parity.py -v -k "01_simple_table or 05_sections_lists or 12_image_chart"
```

Expected: all three XPASSED (because they still carry the xfail marker).

- [ ] **Step 2: Remove the three names from `_XFAIL_CASES`**

In `tests/test_bottom_up_parity.py`, edit the `_XFAIL_CASES` set:

```python
_XFAIL_CASES: set[str] = {
    "02_nested_table",
    "03_page_spanning",
    "04_multi_column",
    "06_page_spanning_no_header_repeat",
    "07_page_spanning_with_nested",
    "08_page_spanning_subtable_split",
    "09_mixed_toc_and_spanning_table",
    "10_merged_cells",
    "11_pl_statement",
    "13_comprehensive",
    "14_borderless_table",
    "14b_borderless_long_text",
    "14c_borderless_long_text_spanning",
    "15_multicolumn_text",
    "16_text_between_subtables",
    "17_text_between_subtables_spanning",
    "18_ruled_header_open_body",
    "19_ruled_header_framed_body",
    "20_ruled_header_row_strips",
    "21_vertical_merge_invisible_lines",
    "22_text_between_adjacent_tables",
    "23_bordered_cell_with_bulleted_prose",
    "24_subtable_flush_outer_edges",
    "25_subtable_flush_outer_vertical_only",
    "26_spanning_subtable_flush_at_break",
}
```

- [ ] **Step 3: Re-run — those three now pass plainly, others remain xfail**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: `3 passed, 24 xfailed`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test(parity): 01/05/12 reach bottom-up parity"
```

---

# Phase 2 — Whitespace Gutters

### Task 2.1: Word-line grouping helper

**Files:**
- Modify: `pdf_parser/stages/detect_cells.py`
- Modify: `tests/stages/test_detect_cells.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/stages/test_detect_cells.py`:

```python
from pdf_parser.stages.detect_cells import _group_words_into_lines


def test_word_lines_y_bucketed():
    words = [
        {"x0": 10, "x1": 30, "top": 100, "bottom": 110, "text": "Hello"},
        {"x0": 40, "x1": 60, "top": 101, "bottom": 111, "text": "world"},
        {"x0": 10, "x1": 30, "top": 130, "bottom": 140, "text": "Next"},
    ]
    lines = _group_words_into_lines(words, tol=2.0)
    assert len(lines) == 2
    assert [w["text"] for w in lines[0]] == ["Hello", "world"]
    assert [w["text"] for w in lines[1]] == ["Next"]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_word_lines_y_bucketed -v
```

Expected: FAIL — `_group_words_into_lines` not defined.

- [ ] **Step 3: Implement the helper**

Append to `pdf_parser/stages/detect_cells.py`:

```python
def _group_words_into_lines(words: list[dict], tol: float = 2.0) -> list[list[dict]]:
    """y-bucket pdfplumber word dicts into visual text lines."""
    if not words:
        return []
    by_y: list[tuple[float, dict]] = sorted(
        ((w["top"] + w["bottom"]) / 2.0, w) for w in words
    )
    lines: list[list[dict]] = [[by_y[0][1]]]
    cur_y = by_y[0][0]
    for ymid, w in by_y[1:]:
        if abs(ymid - cur_y) <= tol:
            lines[-1].append(w)
        else:
            lines.append([w])
        cur_y = (cur_y + ymid) / 2.0
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
    return lines
```

- [ ] **Step 4: Re-run — should pass**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_word_lines_y_bucketed -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/detect_cells.py tests/stages/test_detect_cells.py
git commit -m "feat(detect_cells): _group_words_into_lines helper"
```

---

### Task 2.2: Whitespace-gutter column detector

**Files:**
- Modify: `pdf_parser/stages/detect_cells.py`
- Modify: `tests/stages/test_detect_cells.py`

- [ ] **Step 1: Write the failing unit test (algorithmic, no PDF)**

Append to `tests/stages/test_detect_cells.py`:

```python
from pdf_parser.stages.detect_cells import _find_column_gutters


def test_gutters_three_columns():
    """Three text columns with consistent inter-column whitespace.

    Each row's words: [Name        Score   Grade] at fixed x-ranges.
    """
    line_words: list[list[dict]] = []
    for y in (100.0, 120.0, 140.0, 160.0):
        line_words.append([
            {"x0": 50, "x1": 90, "top": y, "bottom": y + 8, "text": "Alice"},
            {"x0": 150, "x1": 170, "top": y, "bottom": y + 8, "text": "95"},
            {"x0": 220, "x1": 230, "top": y, "bottom": y + 8, "text": "A"},
        ])
    gutters = _find_column_gutters(line_words, min_run=3, min_gap_pt=8.0)
    # Two inter-column gutters → 3 column ranges.
    assert len(gutters) == 2
```


- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_gutters_three_columns -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `_find_column_gutters`**

Append to `pdf_parser/stages/detect_cells.py`:

```python
# ---------------------------------------------------------------------------
# Whitespace-gutter cell detection.
#
# Vertical gutters that persist across ≥ ``min_run`` consecutive text lines
# define column boundaries.  Algorithm:
#
#   1. For every line, compute the list of horizontal gaps between adjacent
#      words wider than ``min_gap_pt``.
#   2. Project each gap as an x-interval.  Intersect intervals across lines.
#   3. Persistent (≥ min_run) intersections define columns.
#
# Spiritual replacement for ``detect_tables_anchor._column_anchor_detector``;
# outputs cells, not whole tables.
# ---------------------------------------------------------------------------

_GUTTER_MIN_RUN_LINES = 3
_GUTTER_MIN_GAP_PT    = 8.0


def _line_gaps(words: list[dict], min_gap_pt: float) -> list[tuple[float, float]]:
    """Inter-word gaps wider than ``min_gap_pt`` as ``(x0, x1)`` intervals."""
    gaps: list[tuple[float, float]] = []
    for prev, cur in zip(words, words[1:]):
        if cur["x0"] - prev["x1"] >= min_gap_pt:
            gaps.append((prev["x1"], cur["x0"]))
    return gaps


def _find_column_gutters(
    lines: list[list[dict]],
    min_run: int = _GUTTER_MIN_RUN_LINES,
    min_gap_pt: float = _GUTTER_MIN_GAP_PT,
) -> list[tuple[float, float]]:
    """Return (x0, x1) gutter intervals that persist across ≥ ``min_run`` lines."""
    if len(lines) < min_run:
        return []
    # Per-line gap interval lists; we accumulate "support counts" per x-bucket.
    line_gaps = [_line_gaps(ln, min_gap_pt) for ln in lines]
    # Walk overlapping intervals: for each gap on the first line, see how many
    # consecutive following lines also have an overlapping gap.
    out: list[tuple[float, float]] = []
    seen: list[tuple[float, float]] = []
    for i, gaps_i in enumerate(line_gaps):
        for g in gaps_i:
            if any(_intervals_overlap(g, s) for s in seen):
                continue
            run = 1
            for gaps_j in line_gaps[i + 1:]:
                if any(_intervals_overlap(g, gj) for gj in gaps_j):
                    run += 1
                else:
                    break
            if run >= min_run:
                out.append(g)
                seen.append(g)
    return sorted(out)


def _intervals_overlap(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return not (a[1] < b[0] or b[1] < a[0])
```

- [ ] **Step 4: Re-run the test**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_gutters_three_columns -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/detect_cells.py tests/stages/test_detect_cells.py
git commit -m "feat(detect_cells): persistent-gutter column detection"
```

---

### Task 2.3: Gutter cells from a page

**Files:**
- Modify: `pdf_parser/stages/detect_cells.py`
- Modify: `tests/stages/test_detect_cells.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/stages/test_detect_cells.py`:

```python
from pdf_parser.stages.detect_cells import _gutter_cells


def test_gutter_cells_on_14_borderless_table():
    pdf_path = Path("tests/golden/synthetic/14_borderless_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _gutter_cells(page, page_index=0)
    assert cells, "gutter detector must find cells on a borderless table"
    assert all(c.source == "gutter" for c in cells)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_gutter_cells_on_14_borderless_table -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `_gutter_cells`**

Append to `pdf_parser/stages/detect_cells.py`:

```python
_GUTTER_CONFIDENCE = 0.7
_GUTTER_LINE_TOL   = 2.0


def _column_ranges_from_gutters(
    page_x0: float, page_x1: float, gutters: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Convert gutters → list of column x-ranges spanning [page_x0, page_x1]."""
    if not gutters:
        return [(page_x0, page_x1)]
    bounds = [page_x0]
    for g in gutters:
        bounds.extend(g)
    bounds.append(page_x1)
    bounds = sorted(set(bounds))
    cols: list[tuple[float, float]] = []
    skip_next = False
    for i in range(len(bounds) - 1):
        if skip_next:
            skip_next = False
            continue
        a, b = bounds[i], bounds[i + 1]
        # If (a, b) is itself a gutter, skip.
        if any(abs(a - g[0]) < 0.5 and abs(b - g[1]) < 0.5 for g in gutters):
            continue
        cols.append((a, b))
    return cols


def _bin_words_to_columns(
    words: list[dict], cols: list[tuple[float, float]]
) -> list[str]:
    bins: list[list[tuple[float, str]]] = [[] for _ in cols]
    for w in words:
        xmid = (w["x0"] + w["x1"]) / 2.0
        for i, (cx0, cx1) in enumerate(cols):
            if cx0 - 0.5 <= xmid <= cx1 + 0.5:
                bins[i].append((w["x0"], w["text"]))
                break
    return [" ".join(t for _, t in sorted(b)) for b in bins]


def _gutter_cells(page, page_index: int) -> list[Cell]:
    words = page.extract_words(keep_blank_chars=False, use_text_flow=False)
    if not words:
        return []
    lines = _group_words_into_lines(words, tol=_GUTTER_LINE_TOL)
    if len(lines) < _GUTTER_MIN_RUN_LINES:
        return []
    gutters = _find_column_gutters(lines)
    if not gutters:
        return []
    page_x0 = float(page.bbox[0])
    page_x1 = float(page.bbox[2])
    cols = _column_ranges_from_gutters(page_x0, page_x1, gutters)
    if len(cols) < 2:
        return []
    out: list[Cell] = []
    for ln in lines:
        texts = _bin_words_to_columns(ln, cols)
        y0 = min(w["top"] for w in ln)
        y1 = max(w["bottom"] for w in ln)
        for (cx0, cx1), t in zip(cols, texts):
            if not t:
                continue
            out.append(Cell(
                bbox=BBox(page=page_index, x0=cx0, y0=y0, x1=cx1, y1=y1),
                text=t.strip(),
                source="gutter",
                confidence=_GUTTER_CONFIDENCE,
            ))
    return out
```

Wire `_gutter_cells` into `detect_cells`:

```python
def detect_cells(page, page_index: int) -> list[Cell]:
    line = _line_cells(page, page_index)
    if line:
        return line  # Line-bounded wins; gutter is the borderless fallback.
    return _gutter_cells(page, page_index)
```

- [ ] **Step 4: Re-run — should pass**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_gutter_cells_on_14_borderless_table -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/detect_cells.py tests/stages/test_detect_cells.py
git commit -m "feat(detect_cells): gutter-based cells for borderless pages"
```

---

### Task 2.4: Prose-rejection guard for the gutter detector

**Files:**
- Modify: `pdf_parser/stages/detect_cells.py`
- Modify: `tests/stages/test_detect_cells.py`

A persistent gutter also appears between the two columns of body text in `15_multicolumn_text` — but that document has no table. We need the same prose guards `extract_tables._is_text_strategy_table` uses, narrowed to the gutter source.

- [ ] **Step 1: Write the failing test**

Append to `tests/stages/test_detect_cells.py`:

```python
def test_gutter_cells_reject_multicolumn_prose():
    """15_multicolumn_text is body prose; gutter detector must NOT see a table."""
    pdf_path = Path("tests/golden/synthetic/15_multicolumn_text/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _gutter_cells(page, page_index=0)
    assert cells == [], (
        f"Multi-column prose was misclassified as table cells: "
        f"{[c.text[:30] for c in cells[:5]]}"
    )
```

- [ ] **Step 2: Run — expect FAIL** (the naive detector returns cells)

```bash
uv run pytest tests/stages/test_detect_cells.py::test_gutter_cells_reject_multicolumn_prose -v
```

Expected: FAIL.

- [ ] **Step 3: Add the prose guards (vendored from legacy)**

Edit `_gutter_cells` and add helpers in `pdf_parser/stages/detect_cells.py`:

```python
# Tuned narrower than the legacy text-strategy guard because line-bounded
# detection already absorbs most real tables; gutter only fires on borderless
# layouts where false positives (multi-column prose) are the dominant risk.
_GUTTER_MAX_AVG_CELL_CHARS         = 12   # avg cell length must stay ≤ this
_GUTTER_MAX_LOWERCASE_START_RATIO  = 0.40 # ≤40% of cells may start lowercase


def _is_gutter_table_shape(grid: list[list[str]]) -> bool:
    cells = [c.strip() for row in grid for c in row if c.strip()]
    if len(cells) < 4:
        return False
    avg_len = sum(len(c) for c in cells) / len(cells)
    if avg_len > _GUTTER_MAX_AVG_CELL_CHARS:
        return False
    lowercase_starts = sum(1 for c in cells if c[:1].islower())
    if lowercase_starts / len(cells) > _GUTTER_MAX_LOWERCASE_START_RATIO:
        return False
    return True
```

Wrap the final emission inside `_gutter_cells`:

```python
    out: list[Cell] = []
    # Buffer rows first so we can run the prose guard on the candidate grid.
    candidate_rows: list[list[str]] = []
    candidate_cells: list[Cell] = []
    for ln in lines:
        texts = _bin_words_to_columns(ln, cols)
        if not any(t for t in texts):
            continue
        y0 = min(w["top"] for w in ln)
        y1 = max(w["bottom"] for w in ln)
        row_cells: list[Cell] = []
        for (cx0, cx1), t in zip(cols, texts):
            if not t:
                continue
            row_cells.append(Cell(
                bbox=BBox(page=page_index, x0=cx0, y0=y0, x1=cx1, y1=y1),
                text=t.strip(),
                source="gutter",
                confidence=_GUTTER_CONFIDENCE,
            ))
        if row_cells:
            candidate_rows.append(texts)
            candidate_cells.extend(row_cells)
    if not _is_gutter_table_shape(candidate_rows):
        return []
    return candidate_cells
```

- [ ] **Step 4: Re-run — should pass**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_gutter_cells_reject_multicolumn_prose tests/stages/test_detect_cells.py::test_gutter_cells_on_14_borderless_table -v
```

Expected: both pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/detect_cells.py tests/stages/test_detect_cells.py
git commit -m "feat(detect_cells): prose-rejection guard for gutter detector"
```

---

### Task 2.5: Text-strategy fallback (lowest confidence)

**Files:**
- Modify: `pdf_parser/stages/detect_cells.py`
- Modify: `tests/stages/test_detect_cells.py`

A fallback that fires only when both line and gutter return empty — pdfplumber's text-strategy, with the same guards.

- [ ] **Step 1: Write the failing test (negative — must not fire on prose)**

```python
def test_text_fallback_not_invoked_when_line_or_gutter_succeed():
    """01_simple_table is line-bounded; text fallback never runs."""
    pdf_path = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)
    assert all(c.source == "line" for c in cells)
```

- [ ] **Step 2: Run — should already pass on current code; this locks the invariant**

```bash
uv run pytest tests/stages/test_detect_cells.py::test_text_fallback_not_invoked_when_line_or_gutter_succeed -v
```

Expected: PASS.

- [ ] **Step 3: Add `_text_cells` fallback and wire as last resort**

Append to `pdf_parser/stages/detect_cells.py`:

```python
_TEXT_FALLBACK_SETTINGS = {
    "vertical_strategy":    "text",
    "horizontal_strategy":  "text",
    "snap_tolerance":       3,
    "join_tolerance":       3,
    "edge_min_length":      3,
    "min_words_vertical":   2,
    "min_words_horizontal": 1,
}
_TEXT_CELL_CONFIDENCE = 0.4


def _text_cells(page, page_index: int) -> list[Cell]:
    tables = page.find_tables(table_settings=_TEXT_FALLBACK_SETTINGS)
    out: list[Cell] = []
    for t in tables:
        rows = t.extract()
        flat = [c.strip() for row in rows for c in row if c]
        if not _is_gutter_table_shape([[c.strip() for c in row if c] for row in rows]):
            continue
        for r_idx, row in enumerate(t.rows):
            for c_idx, cbox in enumerate(row.cells):
                if cbox is None:
                    continue
                x0, y0, x1, y1 = cbox
                txt = (rows[r_idx][c_idx] or "").strip() if r_idx < len(rows) else ""
                out.append(Cell(
                    bbox=BBox(page=page_index, x0=x0, y0=y0, x1=x1, y1=y1),
                    text=txt,
                    source="text",
                    confidence=_TEXT_CELL_CONFIDENCE,
                ))
    return out
```

Wire as last resort:

```python
def detect_cells(page, page_index: int) -> list[Cell]:
    line = _line_cells(page, page_index)
    if line:
        return line
    gutter = _gutter_cells(page, page_index)
    if gutter:
        return gutter
    return _text_cells(page, page_index)
```

- [ ] **Step 4: Re-run the cell tests**

```bash
uv run pytest tests/stages/test_detect_cells.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/detect_cells.py tests/stages/test_detect_cells.py
git commit -m "feat(detect_cells): pdfplumber text-strategy fallback as last resort"
```

---

### Task 2.6: Phase-2 parity — 14, 14b, 15

**Files:**
- Modify: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Confirm via parity harness**

```bash
uv run pytest tests/test_bottom_up_parity.py -v -k "14_borderless_table or 14b_borderless_long_text or 15_multicolumn_text"
```

Expected: 3 xpassed (still xfail-marked).

- [ ] **Step 2: Remove the three names from `_XFAIL_CASES`**

In `tests/test_bottom_up_parity.py`, drop `"14_borderless_table"`, `"14b_borderless_long_text"`, `"15_multicolumn_text"` from the set.

- [ ] **Step 3: Re-run parity**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: `6 passed, 21 xfailed`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test(parity): 14/14b/15 reach bottom-up parity via gutters"
```

---

# Phase 3 — Aggregation: spanning, adjacent, merged-basic

### Task 3.1: Cell deduplication when line + gutter overlap

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

When `_line_cells` returns results, gutter is currently skipped; but in mixed-source pages (ruled header + open body) both fire on the same region. Aggregation must dedupe by rounded bbox, preferring `line > gutter > text`.

- [ ] **Step 1: Write the failing test**

Append to `tests/stages/test_aggregate_tables.py`:

```python
from pdf_parser.stages.detect_cells import Cell
from pdf_parser.stages.aggregate_tables import _dedupe_cells


def test_dedupe_prefers_line_over_gutter_over_text():
    bb = BBox(page=0, x0=10, y0=20, x1=30, y1=40)
    cells = [
        Cell(bbox=bb, text="gutter", source="gutter", confidence=0.7),
        Cell(bbox=bb, text="LINE",   source="line",   confidence=1.0),
        Cell(bbox=bb, text="text",   source="text",   confidence=0.4),
    ]
    out = _dedupe_cells(cells)
    assert len(out) == 1
    assert out[0].source == "line"
    assert out[0].text == "LINE"
```

- [ ] **Step 2: Run — expect ImportError**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_dedupe_prefers_line_over_gutter_over_text -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `_dedupe_cells`**

Add to `pdf_parser/stages/aggregate_tables.py`:

```python
_SOURCE_RANK = {"line": 3, "gutter": 2, "text": 1}


def _dedupe_cells(cells: list[Cell]) -> list[Cell]:
    """Drop cells that share a rounded bbox with a higher-ranked cell."""
    best: dict[tuple[int, int, int, int, int], Cell] = {}
    for c in cells:
        key = c.bbox.rounded()
        cur = best.get(key)
        if cur is None or _SOURCE_RANK[c.source] > _SOURCE_RANK[cur.source]:
            best[key] = c
    return list(best.values())
```

Call it at the top of `aggregate()`:

```python
def aggregate(cells: list[Cell], page_height: float) -> list[CellTable]:
    if not cells:
        return []
    cells = _dedupe_cells(cells)
    out: list[CellTable] = []
    for table_rows in _split_into_tables(_row_cluster(cells)):
        ct = _rows_to_celltable(table_rows, page_height)
        if ct is not None:
            out.append(ct)
    return out
```

- [ ] **Step 4: Re-run — should pass**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): bbox-keyed dedup, line>gutter>text"
```

---

### Task 3.2: Adjacent-table splitting via vertical-gap clustering

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

22_text_between_adjacent_tables has two stacked tables separated by a paragraph. `_split_into_tables` must break on a large vertical gap, not merge them.

- [ ] **Step 1: Write the failing test**

```python
def test_split_breaks_on_large_vertical_gap():
    """Two table candidates separated by > N×median row-gap split correctly."""
    bb = lambda y0, y1, x0=10, x1=30: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Table A: rows at y=100, 120
        Cell(bbox=bb(100, 110), text="a1", source="line", confidence=1.0),
        Cell(bbox=bb(100, 110, 40, 60), text="a2", source="line", confidence=1.0),
        Cell(bbox=bb(120, 130), text="a3", source="line", confidence=1.0),
        Cell(bbox=bb(120, 130, 40, 60), text="a4", source="line", confidence=1.0),
        # Big paragraph gap from y=130 to y=300
        # Table B: rows at y=300, 320
        Cell(bbox=bb(300, 310), text="b1", source="line", confidence=1.0),
        Cell(bbox=bb(300, 310, 40, 60), text="b2", source="line", confidence=1.0),
        Cell(bbox=bb(320, 330), text="b3", source="line", confidence=1.0),
        Cell(bbox=bb(320, 330, 40, 60), text="b4", source="line", confidence=1.0),
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 2
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_split_breaks_on_large_vertical_gap -v
```

Expected: FAIL — the current adjacency check only looks at xrange.

- [ ] **Step 3: Add gap-aware splitting**

Replace `_split_into_tables` in `pdf_parser/stages/aggregate_tables.py`:

```python
import statistics


def _row_height(row: list[Cell]) -> float:
    return max(c.bbox.y1 for c in row) - min(c.bbox.y0 for c in row)


def _row_top(row: list[Cell]) -> float:
    return min(c.bbox.y0 for c in row)


def _split_into_tables(rows: list[list[Cell]]) -> list[list[list[Cell]]]:
    """Adjacent rows with similar geometry AND small inter-row gap form one table."""
    if not rows:
        return []
    tables: list[list[list[Cell]]] = [[rows[0]]]
    for r in rows[1:]:
        prev_table = tables[-1]
        prev = prev_table[-1]
        if prev[0].bbox.page != r[0].bbox.page:
            tables.append([r])
            continue
        # Geometric similarity guard.
        same_xrange = (abs(prev[0].bbox.x0 - r[0].bbox.x0) <= 4.0
                       and abs(prev[-1].bbox.x1 - r[-1].bbox.x1) <= 4.0)
        if not same_xrange:
            tables.append([r])
            continue
        # Gap guard: gap above median row-height × multiplier means break.
        gap = _row_top(r) - max(c.bbox.y1 for c in prev)
        heights = [_row_height(rr) for rr in prev_table]
        median_h = statistics.median(heights) if heights else 0.0
        if gap > max(_TABLE_GAP_MULT * median_h, 12.0):
            tables.append([r])
        else:
            prev_table.append(r)
    return tables
```

- [ ] **Step 4: Re-run — should pass**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: all aggregate tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): break adjacent tables on large inter-row gaps"
```

---

### Task 3.3: Phase-3 parity — 03, 04, 06, 10 (basic), 11, 22

**Files:**
- Modify: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Run parity for the six fixtures**

```bash
uv run pytest tests/test_bottom_up_parity.py -v -k "03_page_spanning or 04_multi_column or 06_page_spanning_no_header_repeat or 10_merged_cells or 11_pl_statement or 22_text_between_adjacent_tables"
```

If any fails (rather than xpasses), inspect the printed id-set diff. The most common Phase-3 misses:
- Wrong header_signature (first row text differs by whitespace) → tighten `_rows_to_celltable` text cleanup.
- Missing covered cells in 10_merged_cells (full merged handling is Phase 6) → if `10_merged_cells` xfails strict=False here, accept and leave for Phase 6.

- [ ] **Step 2: Remove fixtures that reached parity from `_XFAIL_CASES`**

Edit `tests/test_bottom_up_parity.py`, drop only the names that XPASSED:

```python
# Example after Phase 3 — actual set depends on Step 1 results.
# At minimum 03, 04, 06, 11, 22 should pass; 10 deferred to Phase 6.
_XFAIL_CASES: set[str] = {
    "02_nested_table",
    "07_page_spanning_with_nested",
    "08_page_spanning_subtable_split",
    "09_mixed_toc_and_spanning_table",
    "10_merged_cells",
    "13_comprehensive",
    "14c_borderless_long_text_spanning",
    "16_text_between_subtables",
    "17_text_between_subtables_spanning",
    "18_ruled_header_open_body",
    "19_ruled_header_framed_body",
    "20_ruled_header_row_strips",
    "21_vertical_merge_invisible_lines",
    "23_bordered_cell_with_bulleted_prose",
    "24_subtable_flush_outer_edges",
    "25_subtable_flush_outer_vertical_only",
    "26_spanning_subtable_flush_at_break",
}
```

- [ ] **Step 3: Re-run parity**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: passes increase by 5 (or 6 if 10_merged_cells also flipped); xfails decrease accordingly.

- [ ] **Step 4: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test(parity): Phase-3 single-page tables reach bottom-up parity"
```

---

# Phase 4 — Nesting via Spatial Containment

The replacement for `extract_tables.py`'s recursive `detect_tables(region_bbox=cell_bbox)` call. Instead of re-running cell detection inside each cell, every cell already exists in the page-level `cells` list — a sub-cluster of cells whose bboxes fall inside another cell's bbox IS the nested table.

### Task 4.1: Containment helper

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

- [ ] **Step 1: Write the failing test**

```python
from pdf_parser.stages.aggregate_tables import _cells_inside


def test_cells_inside_filters_by_containment():
    outer = BBox(page=0, x0=0, y0=0, x1=100, y1=100)
    inside_bb = BBox(page=0, x0=10, y0=10, x1=30, y1=30)
    outside_bb = BBox(page=0, x0=200, y0=200, x1=220, y1=220)
    cells = [
        Cell(bbox=inside_bb, text="in", source="line", confidence=1.0),
        Cell(bbox=outside_bb, text="out", source="line", confidence=1.0),
    ]
    inside = _cells_inside(cells, outer)
    assert [c.text for c in inside] == ["in"]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_cells_inside_filters_by_containment -v
```

Expected: FAIL.

- [ ] **Step 3: Implement `_cells_inside`**

Add to `pdf_parser/stages/aggregate_tables.py`:

```python
_CONTAIN_TOL = 2.0


def _cells_inside(cells: list[Cell], outer: BBox) -> list[Cell]:
    """Return cells whose bbox is strictly inside ``outer`` (with slack)."""
    return [
        c for c in cells
        if (c.bbox.page == outer.page
            and c.bbox.x0 >= outer.x0 - _CONTAIN_TOL
            and c.bbox.y0 >= outer.y0 - _CONTAIN_TOL
            and c.bbox.x1 <= outer.x1 + _CONTAIN_TOL
            and c.bbox.y1 <= outer.y1 + _CONTAIN_TOL
            # Strict: must be smaller than outer in at least one dimension.
            and (c.bbox.x1 - c.bbox.x0 < outer.x1 - outer.x0 - _CONTAIN_TOL
                 or c.bbox.y1 - c.bbox.y0 < outer.y1 - outer.y0 - _CONTAIN_TOL))
    ]
```

- [ ] **Step 4: Re-run — pass**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_cells_inside_filters_by_containment -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): _cells_inside containment helper"
```

---

### Task 4.2: Recursive nested-table aggregation

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

- [ ] **Step 1: Write the failing test**

```python
def test_nested_table_detected_via_containment():
    """A 2-row × 2-col outer table whose top-right cell contains a 2×2 inner table.

    Outer cell layout (page 0):
      [ A:0,0,50,20 ] [ B:50,0,100,20 ]
      [ C:0,20,50,60] [ inner cell block 60-100 ]

    Inner table inside cell at (50,20,100,60): four cells at corners.
    """
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    outer = [
        Cell(bbox=bb(0,  0, 50, 20), text="A", source="line", confidence=1.0),
        Cell(bbox=bb(50, 0,100, 20), text="B", source="line", confidence=1.0),
        Cell(bbox=bb(0, 20, 50, 60), text="C", source="line", confidence=1.0),
        Cell(bbox=bb(50,20,100, 60), text="",  source="line", confidence=1.0),
    ]
    inner = [
        Cell(bbox=bb(50, 20, 75, 40), text="i1", source="line", confidence=1.0),
        Cell(bbox=bb(75, 20,100, 40), text="i2", source="line", confidence=1.0),
        Cell(bbox=bb(50, 40, 75, 60), text="i3", source="line", confidence=1.0),
        Cell(bbox=bb(75, 40,100, 60), text="i4", source="line", confidence=1.0),
    ]
    tables = aggregate(outer + inner, page_height=792.0)
    assert len(tables) == 1, f"expected one outer; got {len(tables)}"
    outer_t = tables[0]
    assert outer_t.grid[0] == ["A", "B"]
    # Inner table attached as nested
    assert len(outer_t.nested) == 1
    inner_t = outer_t.nested[0]
    assert inner_t.grid == [["i1", "i2"], ["i3", "i4"]]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_nested_table_detected_via_containment -v
```

Expected: FAIL.

- [ ] **Step 3: Make `aggregate` recurse via containment**

Replace `aggregate()` in `pdf_parser/stages/aggregate_tables.py`:

```python
def aggregate(cells: list[Cell], page_height: float) -> list[CellTable]:
    if not cells:
        return []
    cells = _dedupe_cells(cells)
    return _aggregate_recursive(cells, page_height)


def _aggregate_recursive(cells: list[Cell], page_height: float) -> list[CellTable]:
    """Produce top-level tables; attach contained sub-tables as ``nested``."""
    top: list[CellTable] = []
    for table_rows in _split_into_tables(_row_cluster(cells)):
        ct = _rows_to_celltable(table_rows, page_height)
        if ct is None:
            continue
        # For each leaf cell whose bbox is large enough to contain a sub-cluster,
        # recurse on the cells strictly inside it (excluding the cell itself).
        used: set[tuple[int, int, int, int, int]] = {
            c.bbox.rounded() for row in table_rows for c in row
        }
        remaining = [c for c in cells if c.bbox.rounded() not in used]
        for r_idx, row in enumerate(table_rows):
            for c_idx, c in enumerate(row):
                inside = _cells_inside(remaining, c.bbox)
                if len(inside) < 4:
                    continue  # < 2×2 cannot form a table
                sub_tables = _aggregate_recursive(inside, page_height)
                if not sub_tables:
                    continue
                ct.nested.extend(sub_tables)
                # Clear the parent cell's text — the nested table replaces it.
                ct.grid[r_idx][c_idx] = ""
        top.append(ct)
    return top
```

- [ ] **Step 4: Re-run — pass**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: all aggregate tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): recursive nesting via spatial containment"
```

---

### Task 4.3: Between-text inside cells (port from `extract_tables._between_text_nodes`)

`16_text_between_subtables` and `17_text_between_subtables_spanning` require that paragraph text living between two nested sub-tables in a cell be preserved as paragraph siblings of the sub-tables. The legacy implementation lives in `extract_tables._between_text_nodes`; the bottom-up extractor must call equivalent logic. We import the existing helper from `extract_tables.py` (it is a pure function over `page.chars` and bboxes — not part of the deleted cascade — and stays put through Phase 9; Phase 10 inlines it locally).

**Files:**
- Modify: `pdf_parser/stages/extract_tables_v2.py`
- Create: `tests/stages/test_extract_tables_v2_between.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/stages/test_extract_tables_v2_between.py
"""Cells containing both a nested table AND a between-text paragraph keep both."""
from pathlib import Path

from pdf_parser.pipeline import parse


def test_16_keeps_between_text():
    pdf = Path("tests/golden/synthetic/16_text_between_subtables/source.pdf")
    tree = parse(pdf, use_bottom_up=True)
    # Walk every cell; somewhere a cell has BOTH a table child AND a paragraph child.
    has_mixed = False

    def walk(n):
        nonlocal has_mixed
        if n.kind == "cell":
            kinds = {c.kind for c in n.children}
            if "table" in kinds and ("paragraph" in kinds or "list_item" in kinds):
                has_mixed = True
        for c in n.children:
            walk(c)

    walk(tree)
    assert has_mixed, "Expected a cell with both a nested table and between-text"
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_extract_tables_v2_between.py -v
```

Expected: FAIL — between-text is not emitted yet.

- [ ] **Step 3: Wire `_between_text_nodes` into `_celltable_to_docnode`**

In `pdf_parser/stages/extract_tables_v2.py`:

```python
from pdf_parser.stages.extract_tables import _between_text_nodes  # Phase 10 inlines


def _extract(pdf) -> list[DocNode]:
    out: list[DocNode] = []
    for page_idx, page in enumerate(pdf.pages):
        cells = detect_cells(page, page_idx)
        tables = aggregate(cells, page_height=float(page.height))
        page_chars = page.chars
        for t in tables:
            out.append(_celltable_to_docnode(t, page_chars=page_chars))
    return out


def _celltable_to_docnode(t: CellTable, page_chars: list[dict] | None = None) -> DocNode:
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(t.grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (t.cell_bboxes[r_idx][c_idx]
                    if r_idx < len(t.cell_bboxes) and c_idx < len(t.cell_bboxes[r_idx])
                    else t.bbox)
            is_covered = (r_idx, c_idx) in t.covered
            attrs: dict = {"align": "left"}
            if is_covered:
                attrs["covered"] = True

            nested_children = [
                _celltable_to_docnode(sub, page_chars=page_chars)
                for sub in t.nested if _bbox_inside(sub.bbox, cbox)
            ]
            extra: list[DocNode] = []
            if nested_children and page_chars is not None:
                nested_bboxes = [
                    sub.bbox for sub in t.nested if _bbox_inside(sub.bbox, cbox)
                ]
                extra = _between_text_nodes(page_chars, cbox, nested_bboxes)

            children = sorted(
                nested_children + extra,
                key=lambda n: n.bbox.y0 if hasattr(n.bbox, "y0") else n.bbox[0].y0,
            )
            cells.append(DocNode(
                kind="cell",
                bbox=cbox,
                text=text if not children else None,
                children=children,
                attrs=attrs,
                provenance=_PROVENANCE,
            ))
        rows.append(DocNode(
            kind="row",
            bbox=t.bbox,
            children=cells,
            attrs={"page": t.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=t.bbox,
        children=rows,
        attrs={
            "n_rows": len(t.grid),
            "n_cols": len(t.grid[0]) if t.grid else 0,
            "header_signature": t.header_signature,
            "page": t.page_index,
            "page_height": t.page_height,
        },
        provenance=_PROVENANCE,
    )
```

- [ ] **Step 4: Re-run — should pass**

```bash
uv run pytest tests/stages/test_extract_tables_v2_between.py -v
```

Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/extract_tables_v2.py tests/stages/test_extract_tables_v2_between.py
git commit -m "feat(extract_tables_v2): preserve between-text inside cells with nested tables"
```

---

### Task 4.4: Phase-4 parity — 02, 16, 17

**Files:**
- Modify: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Run parity for the three nesting fixtures**

```bash
uv run pytest tests/test_bottom_up_parity.py -v -k "02_nested_table or 16_text_between_subtables or 17_text_between_subtables_spanning"
```

Expected: 3 XPASSED.

- [ ] **Step 2: Remove the three names from `_XFAIL_CASES`**

```python
# In tests/test_bottom_up_parity.py
_XFAIL_CASES: set[str] = {
    "07_page_spanning_with_nested",
    "08_page_spanning_subtable_split",
    "09_mixed_toc_and_spanning_table",
    "10_merged_cells",
    "13_comprehensive",
    "14c_borderless_long_text_spanning",
    "18_ruled_header_open_body",
    "19_ruled_header_framed_body",
    "20_ruled_header_row_strips",
    "21_vertical_merge_invisible_lines",
    "23_bordered_cell_with_bulleted_prose",
    "24_subtable_flush_outer_edges",
    "25_subtable_flush_outer_vertical_only",
    "26_spanning_subtable_flush_at_break",
}
```

- [ ] **Step 3: Re-run parity**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: passes increase by 3, xfails decrease by 3.

- [ ] **Step 4: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test(parity): 02/16/17 reach bottom-up parity via containment-nesting"
```

---

# Phase 5 — Ruled-header + Framed-body

These four fixtures (18/19/20/23) draw cell borders only on the header row and leave body rows as open prose. Today's `extract_tables._redistribute_ruled_header_body` rebuilds the grid by binning words against the header's column x-bounds. The bottom-up rewrite handles this naturally: the line detector finds the header cells, the gutter detector projects those column boundaries downward and yields body-row cells. Phase 5 is mostly verification + small tuning, not new code.

### Task 5.1: Project header columns into the open body

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

- [ ] **Step 1: Write the failing test**

```python
def test_header_columns_extend_below():
    """Three line-bounded header cells + 4 prose lines below at the same x-ranges
    must produce a 4-row body in the same column structure."""
    bb = lambda x0, y0, x1, y1, src="line": Cell(
        bbox=BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1),
        text="H" if src == "line" else "x",
        source=src,
        confidence=1.0 if src == "line" else 0.7,
    )
    cells = [
        # Header (line-bounded): 3 cells
        bb(50, 100, 100, 115),
        bb(100, 100, 200, 115),
        bb(200, 100, 280, 115),
        # Body (gutter): 4 lines × 3 columns, same x-ranges, y=120..180
        *[bb(50, 120 + 15*i, 100, 130 + 15*i, "gutter") for i in range(4)],
        *[bb(100, 120 + 15*i, 200, 130 + 15*i, "gutter") for i in range(4)],
        *[bb(200, 120 + 15*i, 280, 130 + 15*i, "gutter") for i in range(4)],
    ]
    tables = aggregate(cells, page_height=792.0)
    assert len(tables) == 1
    assert tables[0].attrs.get("n_rows", len(tables[0].grid)) == 5  # 1 header + 4 body
```

- [ ] **Step 2: Run — expect FAIL or PASS depending on Phase-3 work**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_header_columns_extend_below -v
```

Expected: behaviour depends on tuning. If FAIL, the gap-split is too eager. Adjust:

- [ ] **Step 3: Tighten the gap-split threshold to allow tight header→body transition**

In `_split_into_tables`, change `if gap > max(_TABLE_GAP_MULT * median_h, 12.0):` to use a minimum of 8.0 pt rather than 12.0 pt — the header-to-first-body-row gap can be tight:

```python
        if gap > max(_TABLE_GAP_MULT * median_h, 8.0):
            tables.append([r])
        else:
            prev_table.append(r)
```

- [ ] **Step 4: Re-run — pass**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: all aggregate tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): tighten gap-split to absorb ruled-header bodies"
```

---

### Task 5.2: Phase-5 parity — 18, 19, 20, 23

**Files:**
- Modify: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Run parity for the four ruled-header fixtures**

```bash
uv run pytest tests/test_bottom_up_parity.py -v -k "18_ruled_header_open_body or 19_ruled_header_framed_body or 20_ruled_header_row_strips or 23_bordered_cell_with_bulleted_prose"
```

If a fixture still fails, inspect the diff. Common Phase-5 misses:
- `23` has bulleted prose inside a bordered cell — the cell's children may include `list_item` nodes that `_between_text_nodes` already produces. If wrong, look at `extract_tables_v2._celltable_to_docnode` and confirm `extra` is being sorted with `nested_children` by y0.
- `19_ruled_header_framed_body` has the body framed in a single tall cell — that cell's contents must be re-grouped by gutter sub-detection inside it. If 19 still xfails, add a recursive `_text_cells` call inside leaf-cell post-processing.

- [ ] **Step 2: Remove fixtures that XPASSED from `_XFAIL_CASES`**

```python
# Update tests/test_bottom_up_parity.py — drop the names that passed.
```

- [ ] **Step 3: Re-run parity**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: 4 more passes (or whatever subset reached parity).

- [ ] **Step 4: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test(parity): 18/19/20/23 reach bottom-up parity (ruled-header fixtures)"
```

---

# Phase 6 — Vertical Merges + `covered` Cells

`10_merged_cells` and `21_vertical_merge_invisible_lines` exercise merged-cell semantics. In the legacy code, `extract_tables._logical_grid_from_table` reconstructs a merged grid from outer horizontal lines and emits `attrs.covered=True` on cells spanned over by a prior merged cell. Bottom-up: when a row has fewer cells than the maximum, the missing slots are inferred from the previous row's cell bboxes; the inherited cells are marked `covered`.

### Task 6.1: Detect merged cells from x-range gaps

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

- [ ] **Step 1: Write the failing test**

```python
def test_merged_cell_marks_covered():
    """Row 0 has 3 cells; row 1 has 2 cells where the left one spans cols 0-1.

    Logical layout:
      row 0: [a] [b] [c]                                 (3 cells)
      row 1: [d-spans-cols-0-and-1] [e]                  (2 cells)

    Expected: the wide cell in row 1 is placed in slot (1,0); slot (1,1) is
    an empty 'covered' cell pointing back to (1,0)'s bbox.
    """
    bb = lambda x0, x1, y0, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        Cell(bbox=bb(  0, 100,  0, 20), text="a", source="line", confidence=1.0),
        Cell(bbox=bb(100, 200,  0, 20), text="b", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300,  0, 20), text="c", source="line", confidence=1.0),
        Cell(bbox=bb(  0, 200, 20, 40), text="d", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300, 20, 40), text="e", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    assert (1, 1) in t.covered
    assert t.grid[1] == ["d", "", "e"]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_merged_cell_marks_covered -v
```

Expected: FAIL.

- [ ] **Step 3: Implement column-anchor alignment + covered detection**

Replace `_rows_to_celltable` in `pdf_parser/stages/aggregate_tables.py`:

```python
def _column_anchors(rows: list[list[Cell]]) -> list[tuple[float, float]]:
    """Use the widest row (most cells) as the canonical column set."""
    widest = max(rows, key=len)
    return [(c.bbox.x0, c.bbox.x1) for c in widest]


def _assign_row_to_columns(
    row: list[Cell], anchors: list[tuple[float, float]], tol: float = 4.0
) -> tuple[list[Cell | None], set[int]]:
    """Place each cell into the column whose anchor it overlaps; mark covered
    slots for horizontal merges (cell spans multiple anchors)."""
    slots: list[Cell | None] = [None] * len(anchors)
    covered_idx: set[int] = set()
    for c in row:
        first_match: int | None = None
        for i, (ax0, ax1) in enumerate(anchors):
            if c.bbox.x0 <= ax1 + tol and c.bbox.x1 >= ax0 - tol:
                if first_match is None:
                    first_match = i
                    slots[i] = c
                else:
                    covered_idx.add(i)
        if first_match is None:
            # Cell extends beyond all anchors; drop on the floor.
            continue
    return slots, covered_idx


def _rows_to_celltable(
    rows: list[list[Cell]], page_height: float
) -> CellTable | None:
    if len(rows) < 2 or all(len(r) < 2 for r in rows):
        return None
    anchors = _column_anchors(rows)
    n_cols = len(anchors)
    if n_cols < 2:
        return None
    grid: list[list[str]] = []
    cell_bboxes: list[list[BBox]] = []
    covered: set[tuple[int, int]] = set()
    for r_idx, row in enumerate(rows):
        slots, cov = _assign_row_to_columns(row, anchors)
        row_grid: list[str] = []
        row_bbs: list[BBox] = []
        for c_idx in range(n_cols):
            cell = slots[c_idx]
            if cell is not None:
                row_grid.append(cell.text)
                row_bbs.append(cell.bbox)
            else:
                # Either covered by horizontal-merge or vertically inherited
                ax0, ax1 = anchors[c_idx]
                row_top = min(c.bbox.y0 for c in row) if row else 0.0
                row_bot = max(c.bbox.y1 for c in row) if row else 0.0
                row_grid.append("")
                row_bbs.append(BBox(
                    page=row[0].bbox.page,
                    x0=ax0, y0=row_top, x1=ax1, y1=row_bot,
                ))
                covered.add((r_idx, c_idx))
        for c_idx in cov:
            covered.add((r_idx, c_idx))
        grid.append(row_grid)
        cell_bboxes.append(row_bbs)
    page = rows[0][0].bbox.page
    x0 = min(c.bbox.x0 for r in rows for c in r)
    y0 = min(c.bbox.y0 for r in rows for c in r)
    x1 = max(c.bbox.x1 for r in rows for c in r)
    y1 = max(c.bbox.y1 for r in rows for c in r)
    return CellTable(
        page_index=page,
        bbox=BBox(page=page, x0=x0, y0=y0, x1=x1, y1=y1),
        grid=grid,
        cell_bboxes=cell_bboxes,
        covered=covered,
        header_signature=tuple(grid[0]),
        page_height=page_height,
        nested=[],
        source=rows[0][0].source,
    )
```

- [ ] **Step 4: Re-run — pass**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: all aggregate tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): column-anchor alignment + covered-cell semantics"
```

---

### Task 6.2: Union-cluster column anchors (handle colspan-heavy "widest" rows)

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

The widest-row heuristic from Task 6.1 picks the row with the most cells as the canonical column set. That fails when the row with the most cells is itself a partial colspan — e.g., a section subheader spanning the whole table with 2 wide cells, when the data rows have 4 narrow cells each. The fix is to cluster cell `x0` positions across all rows: every column boundary that appears in any row contributes to the anchor set.

- [ ] **Step 1: Write the failing test**

Append to `tests/stages/test_aggregate_tables.py`:

```python
def test_column_anchors_survive_colspan_heavy_row():
    """A row that is itself a partial colspan (e.g. section subheader)
    must not collapse the anchor set. Anchors come from union-clustering
    cell x0 positions across all rows."""
    bb = lambda x0, x1, y0, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Section header: 2 cells, each spanning 2 logical columns
        Cell(bbox=bb(  0, 200,  0, 20), text="Group A", source="line", confidence=1.0),
        Cell(bbox=bb(200, 400,  0, 20), text="Group B", source="line", confidence=1.0),
        # Data row 1: 4 narrow cells (drive the column set)
        Cell(bbox=bb(  0, 100, 20, 40), text="a", source="line", confidence=1.0),
        Cell(bbox=bb(100, 200, 20, 40), text="b", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300, 20, 40), text="c", source="line", confidence=1.0),
        Cell(bbox=bb(300, 400, 20, 40), text="d", source="line", confidence=1.0),
        # Data row 2: same 4-column structure
        Cell(bbox=bb(  0, 100, 40, 60), text="e", source="line", confidence=1.0),
        Cell(bbox=bb(100, 200, 40, 60), text="f", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300, 40, 60), text="g", source="line", confidence=1.0),
        Cell(bbox=bb(300, 400, 40, 60), text="h", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    assert t.grid[0] == ["Group A", "", "Group B", ""]
    assert (0, 1) in t.covered
    assert (0, 3) in t.covered
    assert t.grid[1] == ["a", "b", "c", "d"]
    assert t.grid[2] == ["e", "f", "g", "h"]
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_column_anchors_survive_colspan_heavy_row -v
```

Expected: FAIL. The widest-row anchors give 2 columns; the assertion on `t.grid[1]` having 4 cells fails.

- [ ] **Step 3: Add `statistics` import**

At the top of `pdf_parser/stages/aggregate_tables.py`, add (only if not already present from earlier tasks):

```python
import statistics
```

- [ ] **Step 4: Replace `_column_anchors` with the union-cluster version**

In `pdf_parser/stages/aggregate_tables.py`, replace the body of `_column_anchors` defined in Task 6.1:

```python
def _column_anchors(rows: list[list[Cell]]) -> list[tuple[float, float]]:
    """Cluster cell x0 positions across ALL rows to form the canonical column set.

    The previous 'widest row' heuristic failed when the row with the most cells
    was itself a partial colspan (section subheader, etc.). Union-clustering
    sidesteps this: every column boundary that appears in any row contributes
    to the anchor set.
    """
    TOL = 4.0
    positions = sorted({c.bbox.x0 for r in rows for c in r})
    if not positions:
        return []
    clusters: list[list[float]] = [[positions[0]]]
    for p in positions[1:]:
        if p - clusters[-1][-1] <= TOL:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    cluster_x0 = [statistics.fmean(c) for c in clusters]
    max_x1 = max(c.bbox.x1 for r in rows for c in r)
    anchors: list[tuple[float, float]] = []
    for i, x0 in enumerate(cluster_x0):
        x1 = cluster_x0[i + 1] if i + 1 < len(cluster_x0) else max_x1
        anchors.append((x0, x1))
    return anchors
```

- [ ] **Step 5: Re-run — expect PASS, no Task-6.1 regression**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: every aggregate test passes, including `test_merged_cell_marks_covered` from Task 6.1.

- [ ] **Step 6: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): union-cluster column anchors (handle colspan-heavy 'widest' rows)"
```

---

### Task 6.3: Legacy-faithful covered-cell bboxes (parity-aligned)

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

`DocNode._compute_id` (`pdf_parser/model.py`) hashes the rounded bbox into every node id, so id-set parity against the legacy path requires byte-identical bboxes on covered cells. Task 6.1 synthesised covered bboxes from anchors + the row's min/max y across all cells. The legacy `_logical_grid_from_table` (`pdf_parser/stages/extract_tables.py`) derives covered bboxes from the spanning cell's own y-extent. The two differ whenever cells in the same row have non-uniform heights — common in framed-body tables and vertical-merge fixtures (10, 21). This task aligns the new path with the legacy convention so the Task 6.4 parity check is not blocked by bbox drift.

- [ ] **Step 1: Write the failing test**

Append to `tests/stages/test_aggregate_tables.py`:

```python
def test_covered_bbox_uses_spanning_cell_y_extent():
    """A covered slot's y bounds come from the SPANNING cell, not the
    row's min/max y across all cells. Matches `_logical_grid_from_table`
    in the legacy extractor — required for id-set parity on fixtures 10/21."""
    bb = lambda x0, x1, y0, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    cells = [
        # Row 0: tall colspan cell across cols 0-1 (y 0..30) + short cell col 2 (y 0..20).
        Cell(bbox=bb(  0, 200,  0, 30), text="header", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300,  0, 20), text="x",      source="line", confidence=1.0),
        # Row 1: three narrow cells.
        Cell(bbox=bb(  0, 100, 30, 50), text="a", source="line", confidence=1.0),
        Cell(bbox=bb(100, 200, 30, 50), text="b", source="line", confidence=1.0),
        Cell(bbox=bb(200, 300, 30, 50), text="c", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    cov_bbox = t.cell_bboxes[0][1]
    # x: anchor[1] bounds; y: spanning cell's full y-extent (0..30) —
    # NOT row min/max y (which would be 0..30 in this row but for the
    # wrong semantic reason; rows with uneven cell heights would diverge).
    assert (cov_bbox.x0, cov_bbox.x1) == (100, 200)
    assert (cov_bbox.y0, cov_bbox.y1) == (0, 30)
```

- [ ] **Step 2: Run — expect FAIL**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_covered_bbox_uses_spanning_cell_y_extent -v
```

Expected: FAIL. Task 6.1's bbox-from-row-y-extent path returns a bbox that, in fixtures where cell heights diverge, does not match the spanning cell.

- [ ] **Step 3: Thread spanning-cell bboxes through `_assign_row_to_columns`**

In `pdf_parser/stages/aggregate_tables.py`, replace `_assign_row_to_columns` and the relevant per-slot block of `_rows_to_celltable` defined in Task 6.1:

```python
def _assign_row_to_columns(
    row: list[Cell], anchors: list[tuple[float, float]], tol: float = 4.0
) -> tuple[list[Cell | None], dict[int, BBox]]:
    """Place each cell into the column whose anchor it overlaps.

    Returns ``(slots, covered_bboxes)`` where ``covered_bboxes[i]`` is the
    bbox to record for slot ``i`` when that slot is covered by a horizontal
    merge originating earlier in the same row. The covered bbox uses the
    SPANNING cell's y-extent (legacy `_logical_grid_from_table` convention)
    so id-set parity holds against the legacy path.
    """
    slots: list[Cell | None] = [None] * len(anchors)
    covered_bboxes: dict[int, BBox] = {}
    for c in row:
        first_match: int | None = None
        for i, (ax0, ax1) in enumerate(anchors):
            if c.bbox.x0 <= ax1 + tol and c.bbox.x1 >= ax0 - tol:
                if first_match is None:
                    first_match = i
                    slots[i] = c
                else:
                    covered_bboxes[i] = BBox(
                        page=c.bbox.page,
                        x0=ax0, y0=c.bbox.y0, x1=ax1, y1=c.bbox.y1,
                    )
    return slots, covered_bboxes
```

And update the per-slot loop inside `_rows_to_celltable` to consume the new return shape:

```python
slots, cov_bboxes = _assign_row_to_columns(row, anchors)
row_grid: list[str] = []
row_bbs: list[BBox] = []
for c_idx in range(n_cols):
    cell = slots[c_idx]
    if cell is not None:
        row_grid.append(cell.text)
        row_bbs.append(cell.bbox)
    else:
        row_grid.append("")
        if c_idx in cov_bboxes:
            # Covered by a horizontal-merge in this row: spanning-cell y-extent.
            row_bbs.append(cov_bboxes[c_idx])
        else:
            # Sparse slot (no spanning cell in this row): anchor x + row y-extent.
            ax0, ax1 = anchors[c_idx]
            row_top = min(c.bbox.y0 for c in row) if row else 0.0
            row_bot = max(c.bbox.y1 for c in row) if row else 0.0
            row_bbs.append(BBox(page=row[0].bbox.page, x0=ax0, y0=row_top, x1=ax1, y1=row_bot))
        covered.add((r_idx, c_idx))
grid.append(row_grid)
cell_bboxes.append(row_bbs)
```

(Delete Task 6.1's separate `for c_idx in cov: covered.add(...)` follow-up loop — covered indices are now recorded inline.)

- [ ] **Step 4: Re-run — expect PASS, no earlier regression**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: every aggregate test passes, including `test_merged_cell_marks_covered` from Task 6.1 and `test_column_anchors_survive_colspan_heavy_row` from Task 6.2.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): covered bboxes use spanning-cell y-extent (parity-aligned)"
```

---

### Task 6.4: Phase-6 parity — 10 (full), 21

**Files:**
- Modify: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Run parity**

```bash
uv run pytest tests/test_bottom_up_parity.py -v -k "10_merged_cells or 21_vertical_merge_invisible_lines"
```

Expected: 2 XPASSED.

- [ ] **Step 2: Remove the two from `_XFAIL_CASES`**

```python
_XFAIL_CASES: set[str] = {
    "07_page_spanning_with_nested",
    "08_page_spanning_subtable_split",
    "09_mixed_toc_and_spanning_table",
    "13_comprehensive",
    "14c_borderless_long_text_spanning",
    "24_subtable_flush_outer_edges",
    "25_subtable_flush_outer_vertical_only",
    "26_spanning_subtable_flush_at_break",
}
```

- [ ] **Step 3: Re-run parity**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: 2 more passes.

- [ ] **Step 4: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test(parity): 10/21 reach bottom-up parity via merged-cell logic"
```

---

# Phase 7 — Flush Sub-Tables

`24_subtable_flush_outer_edges`, `25_subtable_flush_outer_vertical_only`, `26_spanning_subtable_flush_at_break` are exactly the failure mode `_try_decompose_megatable` exists to fix in the legacy cascade: a sub-table whose edges are flush with the parent cell's edges. The bottom-up containment rule handles this for free — sub-cells are inside the parent cell's bbox even when the edges coincide (the `_CONTAIN_TOL=2.0` slack covers anti-aliasing). Phase 7 is verification, plus one edge case: a sub-cluster whose bbox is *the same* as the parent must still be recognised as a child, not deduped.

### Task 7.1: Containment must not require strict inequality on both axes

**Files:**
- Modify: `pdf_parser/stages/aggregate_tables.py`
- Modify: `tests/stages/test_aggregate_tables.py`

The current `_cells_inside` requires strict inequality on at least one axis. For a sub-cluster of multiple cells inside a single parent cell, the sub-cluster's *combined* bbox may equal the parent. We must check each sub-cell individually, not their union — the existing implementation already does this. But the parent cell ITSELF appears in `remaining` if it shares the rounded bbox with no other cell. Verify:

- [ ] **Step 1: Write the failing test**

```python
def test_flush_subtable_inside_parent_cell():
    """Inner 2×2 sub-table flush with parent cell on all sides."""
    bb = lambda x0, y0, x1, y1: BBox(page=0, x0=x0, y0=y0, x1=x1, y1=y1)
    # Outer 1-row × 2-col where col 1 is a cell that exactly contains an inner 2×2.
    cells = [
        Cell(bbox=bb(  0, 0,100, 60), text="A", source="line", confidence=1.0),
        Cell(bbox=bb(100, 0,200, 60), text="",  source="line", confidence=1.0),
        # Inner 2×2 with edges flush at 100..200, 0..60
        Cell(bbox=bb(100,  0, 150, 30), text="i1", source="line", confidence=1.0),
        Cell(bbox=bb(150,  0, 200, 30), text="i2", source="line", confidence=1.0),
        Cell(bbox=bb(100, 30, 150, 60), text="i3", source="line", confidence=1.0),
        Cell(bbox=bb(150, 30, 200, 60), text="i4", source="line", confidence=1.0),
    ]
    # Need a second outer row to qualify as a table (≥2 rows).
    cells += [
        Cell(bbox=bb(  0, 60,100, 80), text="B1", source="line", confidence=1.0),
        Cell(bbox=bb(100, 60,200, 80), text="B2", source="line", confidence=1.0),
    ]
    [t] = aggregate(cells, page_height=792.0)
    # Outer cell (0,1) must have one nested sub-table
    assert len(t.nested) == 1
    sub = t.nested[0]
    assert sub.grid == [["i1", "i2"], ["i3", "i4"]]
```

- [ ] **Step 2: Run — expect FAIL or PASS**

```bash
uv run pytest tests/stages/test_aggregate_tables.py::test_flush_subtable_inside_parent_cell -v
```

If FAIL because `_cells_inside`'s strict-smaller check rejects the inner cells (each inner cell is half the parent cell on both axes — so they ARE smaller and the test passes; if not, debug):

- [ ] **Step 3: If needed, relax `_cells_inside`'s strict-smaller check**

If the inner cell happens to be the full width OR full height of the parent (true for 1-column or 1-row nested tables), strict inequality on both axes fails. Change `_cells_inside` to:

```python
def _cells_inside(cells: list[Cell], outer: BBox) -> list[Cell]:
    """Return cells whose bbox is inside ``outer`` and NOT identical to it."""
    out_key = outer.rounded()
    return [
        c for c in cells
        if (c.bbox.page == outer.page
            and c.bbox.x0 >= outer.x0 - _CONTAIN_TOL
            and c.bbox.y0 >= outer.y0 - _CONTAIN_TOL
            and c.bbox.x1 <= outer.x1 + _CONTAIN_TOL
            and c.bbox.y1 <= outer.y1 + _CONTAIN_TOL
            and c.bbox.rounded() != out_key)
    ]
```

- [ ] **Step 4: Re-run — pass**

```bash
uv run pytest tests/stages/test_aggregate_tables.py -v
```

Expected: all aggregate tests pass.

- [ ] **Step 5: Commit**

```bash
git add pdf_parser/stages/aggregate_tables.py tests/stages/test_aggregate_tables.py
git commit -m "feat(aggregate_tables): containment by bbox-not-identical (handles flush sub-tables)"
```

---

### Task 7.2: Phase-7 parity — 24, 25, 26 (single-page slice)

**Files:**
- Modify: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Run parity for the flush-edge fixtures**

```bash
uv run pytest tests/test_bottom_up_parity.py -v -k "24_subtable_flush_outer_edges or 25_subtable_flush_outer_vertical_only or 26_spanning_subtable_flush_at_break"
```

Expected: 24 and 25 XPASS immediately; 26 may still xfail since stitching across page break is Phase 8.

- [ ] **Step 2: Remove 24 and 25 (and 26 if it passed) from `_XFAIL_CASES`**

```python
_XFAIL_CASES: set[str] = {
    "07_page_spanning_with_nested",
    "08_page_spanning_subtable_split",
    "09_mixed_toc_and_spanning_table",
    "13_comprehensive",
    "14c_borderless_long_text_spanning",
    "26_spanning_subtable_flush_at_break",   # leave for Phase 8 if needed
}
```

- [ ] **Step 3: Re-run parity**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: 2 (or 3) more passes.

- [ ] **Step 4: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test(parity): 24/25 reach bottom-up parity (flush sub-tables via containment)"
```

---

# Phase 8 — Cross-Page Stitching Verification

`stitch_pages.py` is UNCHANGED. It already merges tables on adjacent pages when (a) the source extractor matches, (b) column anchors match within 4 pt, (c) the previous table sits near the page bottom. Bottom-up output must satisfy all three.

### Task 8.1: Confirm `_source_extractor` accepts the bottom-up tag

**Files:**
- Create: `tests/stages/test_stitch_pages_bottom_up.py`

- [ ] **Step 1: Write a unit test that stitches two bottom-up table fragments**

```python
# tests/stages/test_stitch_pages_bottom_up.py
"""Two bottom-up tables on consecutive pages with matching anchors must stitch."""
from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.stitch_pages import stitch_tables

_PROV = {"extractor": "bottom_up", "stage": "extract_tables_v2"}


def _row(page: int, y0: float, y1: float, texts: list[str]) -> DocNode:
    cells = [
        DocNode(
            kind="cell",
            bbox=BBox(page=page, x0=10 + 50*i, y0=y0, x1=60 + 50*i, y1=y1),
            text=t,
            attrs={"align": "left"},
            provenance=_PROV,
        )
        for i, t in enumerate(texts)
    ]
    return DocNode(
        kind="row",
        bbox=BBox(page=page, x0=10, y0=y0, x1=10 + 50*len(texts), y1=y1),
        children=cells,
        attrs={"page": page, "row_index": 0},
    )


def _table(page: int, y0: float, y1: float) -> DocNode:
    rows = [_row(page, y0, y0 + 15, ["H1", "H2"]),
            _row(page, y0 + 15, y1, ["a", "b"])]
    return DocNode(
        kind="table",
        bbox=BBox(page=page, x0=10, y0=y0, x1=110, y1=y1),
        children=rows,
        attrs={
            "n_rows": 2, "n_cols": 2,
            "header_signature": ("H1", "H2"),
            "page": page, "page_height": 792.0,
        },
        provenance=_PROV,
    )


def test_stitch_bottom_up_tables_across_pages():
    a = _table(page=0, y0=700.0, y1=730.0)   # near bottom of page 0
    b = _table(page=1, y0=100.0, y1=130.0)
    merged = stitch_tables([a, b])
    assert len(merged) == 1
    assert isinstance(merged[0].bbox, list)
    assert merged[0].provenance["extractor"].startswith("bottom_up")
```

- [ ] **Step 2: Run — should pass already since `stitch_pages` is generic**

```bash
uv run pytest tests/stages/test_stitch_pages_bottom_up.py -v
```

Expected: PASS.

If it fails (unexpected), inspect `_source_extractor` — it strips `+stitch` suffix but keeps any other prefix. Bottom-up tables go through it as `"bottom_up"`, which is stable across stitches as `"bottom_up+stitch"`. Both pass the `_can_merge` extractor check.

- [ ] **Step 3: Commit**

```bash
git add tests/stages/test_stitch_pages_bottom_up.py
git commit -m "test(stitch_pages): verify bottom-up provenance survives stitching"
```

---

### Task 8.2: Phase-8 parity — 07, 08, 09, 14c, 17, 26 (recheck across pages)

**Files:**
- Modify: `tests/test_bottom_up_parity.py`

- [ ] **Step 1: Run parity for all cross-page fixtures**

```bash
uv run pytest tests/test_bottom_up_parity.py -v -k "07_page_spanning_with_nested or 08_page_spanning_subtable_split or 09_mixed_toc_and_spanning_table or 14c_borderless_long_text_spanning or 17_text_between_subtables_spanning or 26_spanning_subtable_flush_at_break"
```

For each xfail (not xpass), check the parity diff output:
- "legacy_only" rows = legacy detector emitted; bottom-up missed.
- "bottom_up_only" rows = bottom-up emitted; legacy missed.

Common Phase-8 misses and fixes:
- Continuation page table missing the header → either bottom-up dropped the header on page 2, or stitching is dropping the wrong duplicate. Confirm `header_signature` matches between fragments.
- Nested sub-table on page 2 not detected → check `detect_cells` on the continuation page surfaces the inner cells; if not, the sub-table's flush edge to the page top is being mistreated.
- 03/06 should already be passing from Phase 3 — re-confirm.

- [ ] **Step 2: Remove fixtures that reached parity**

Edit `tests/test_bottom_up_parity.py`, drop names that XPASSED:

```python
_XFAIL_CASES: set[str] = {
    "13_comprehensive",
}
```

- [ ] **Step 3: Re-run parity**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: 26 passed, 1 xfailed (only `13_comprehensive` remains).

- [ ] **Step 4: Commit**

```bash
git add tests/test_bottom_up_parity.py
git commit -m "test(parity): 07/08/09/14c/17/26 reach bottom-up parity across pages"
```

---

# Phase 9 — Omnibus: 13_comprehensive

The 13_comprehensive fixture is 18 pages and exercises every prior use case in a single document. The `tests/test_comprehensive.py` file holds 24+ behavioural assertions ALL of which currently run with the legacy default. Phase 9 verifies they all hold under `use_bottom_up=True`.

### Task 9.1: Parameterize `test_comprehensive.py` over both paths

**Files:**
- Modify: `tests/test_comprehensive.py`

- [ ] **Step 1: Replace the module-scoped `tree` fixture with a parameterized one**

In `tests/test_comprehensive.py`, change:

```python
@pytest.fixture(scope="module")
def tree() -> DocNode:
    return parse(PDF)
```

to:

```python
@pytest.fixture(scope="module", params=[False, True], ids=["legacy", "bottom_up"])
def tree(request) -> DocNode:
    return parse(PDF, use_bottom_up=request.param)
```

Every existing test that takes `tree` now runs against both paths automatically.

- [ ] **Step 2: Run only the bottom-up variants**

```bash
uv run pytest tests/test_comprehensive.py -v -k bottom_up
```

For every failure: copy the assertion, parse the fixture both ways, diff the offending region. Common Phase-9 fixes are minor (e.g. text spacing in a between-text node, a row's `attrs.page` mismatched across the page-spanning Project Tracking table).

Iterate the bottom-up implementation until all `bottom_up` variants pass. Use:

```bash
uv run pytest tests/test_comprehensive.py::test_NAME -v -k bottom_up
```

per assertion, fix, re-run, commit per fix.

- [ ] **Step 3: After all bottom-up tests pass, run the full file**

```bash
uv run pytest tests/test_comprehensive.py -v
```

Expected: every test passes for both `[legacy]` and `[bottom_up]` parameter.

- [ ] **Step 4: Confirm parity flips for 13_comprehensive**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: `27 xpassed` or — once you delete `_XFAIL_CASES = {"13_comprehensive"}` — `27 passed`.

- [ ] **Step 5: Remove the last entry from `_XFAIL_CASES`**

```python
# tests/test_bottom_up_parity.py
_XFAIL_CASES: set[str] = set()
```

- [ ] **Step 6: Final parity run**

```bash
uv run pytest tests/test_bottom_up_parity.py -v
```

Expected: `27 passed`.

- [ ] **Step 7: Commit**

```bash
git add tests/test_bottom_up_parity.py tests/test_comprehensive.py
git commit -m "test(parity+comprehensive): 13_comprehensive reaches bottom-up parity; comprehensive runs both paths"
```

---

### Task 9.2: Full-suite green check (manual gate before Phase 10)

- [ ] **Step 1: Run the entire test suite**

```bash
uv run pytest
```

Expected: full suite green (243 prior tests + the new units + `27 passed` in parity + comprehensive variants).

If anything fails, STOP and fix before flipping the default. The Phase-10 deletions are irrecoverable from inside the same commit.

- [ ] **Step 2: No commit — the working tree should be clean already.**

---

# Phase 10 — Flip Default + Delete the Cascade ✅ COMPLETE

**Landed 2026-05-25.**  Bottom-up is the default; the legacy cascade is gone
(~2,400 LoC across `detect_tables.py`, `detect_tables_anchor.py`,
`extract_tables.py` + the `scripts/explore_anchor_detector.py` exploration).
`_between_text_nodes` was inlined into `extract_tables_v2.py` and
`_visible_edges` was ported into `detect_cells.py` so the bottom-up path
stands alone.

One detour: fixture 25 surfaced silent data loss (NOTE-MID1 / NOTE-MID2
dropped) that the prior "wrapper-only divergence" residual notes missed.
Fixed inline by threading `page_words` through `aggregate` so
`_split_into_tables` carves the row cluster on text-bearing gaps —
preserving inter-table prose as page siblings when the closed_rect outer
frame is rejected by `_frame_cells`.  Goldens for fixtures 22/24/25
re-baked to the bottom-up canonical (different tree shape, same content).

Final state: 409 passed, 1 skipped, 0 xfailed, 0 failures.

Once Phase 9 is green, the bottom-up path is provably at parity. Phase 10 makes it the default and deletes the legacy code in atomic, easily-revertible commits.

### Task 10.1: Flip pipeline default to `use_bottom_up=True`

**Files:**
- Modify: `pdf_parser/pipeline.py`
- Modify: `tests/test_pipeline_bottom_up_flag.py`

- [ ] **Step 1: Flip the default and update the test**

In `pdf_parser/pipeline.py`:

```python
def parse(
    pdf_path: Path | str,
    llm_fallback: Optional["LLMFallback"] = None,
    *,
    use_anchor: bool = True,        # still accepted; ignored when bottom_up=True
    use_bottom_up: bool = True,     # FLIPPED: bottom-up is now default
) -> DocNode:
```

In `tests/test_pipeline_bottom_up_flag.py`, update:

```python
def test_parse_accepts_use_bottom_up_kwarg():
    sig = inspect.signature(parse)
    assert "use_bottom_up" in sig.parameters
    assert sig.parameters["use_bottom_up"].default is True
```

- [ ] **Step 2: Run the full suite**

```bash
uv run pytest
```

Expected: full suite green. The golden tests (`test_golden.py`, `test_hierarchy.py`) now exercise the bottom-up path implicitly; they pass because of Phase-9 parity.

- [ ] **Step 3: Commit**

```bash
git add pdf_parser/pipeline.py tests/test_pipeline_bottom_up_flag.py
git commit -m "feat(pipeline): default to bottom-up cell-clustering extractor"
```

---

### Task 10.2: Delete `detect_tables_anchor.py` entirely

**Files:**
- Delete: `pdf_parser/stages/detect_tables_anchor.py`
- Modify: `pdf_parser/pipeline.py` (drop the now-dead import and call)

- [ ] **Step 1: Remove the import and call from `pipeline.py`**

In `pdf_parser/pipeline.py`:

```python
# DELETE THIS IMPORT:
# from pdf_parser.stages.detect_tables_anchor import augment_with_anchor_tables

# DELETE THE use_anchor PARAM AND THE CALL:
def parse(
    pdf_path: Path | str,
    llm_fallback: Optional["LLMFallback"] = None,
    *,
    use_bottom_up: bool = True,     # kept for one release as a no-op escape hatch
) -> DocNode:
    ...
    with pdfplumber.open(str(pdf_path)) as pdf:
        if use_bottom_up:
            from pdf_parser.stages.extract_tables_v2 import extract_tables as extract_tables_v2
            tables = extract_tables_v2(pdf_path, pdf=pdf)
        else:
            tables = extract_tables(pdf_path, pdf=pdf)
    ...
```

Also remove the `--no-anchor` CLI option from `pdf_parser/cli.py` since the anchor path no longer exists:

```python
# REMOVE the no_anchor: bool = typer.Option(...) parameter
# REMOVE the use_anchor=not no_anchor kwarg from the parse_pdf() call
tree = parse_pdf(path, llm_fallback=fb, use_bottom_up=bottom_up)
```

- [ ] **Step 2: Delete the file**

```bash
rm pdf_parser/stages/detect_tables_anchor.py
```

- [ ] **Step 3: Delete the anchor-specific tests**

```bash
git rm -f tests/stages/test_detect_tables_anchor.py 2>/dev/null || true
```

(Drop any other test file that imports `augment_with_anchor_tables` or `detect_tables_anchor`. Use `grep -l detect_tables_anchor tests/` to find them before deletion.)

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest
```

Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete detect_tables_anchor (subsumed by bottom-up gutter detector)"
```

---

### Task 10.3: Inline `_visible_edges` + `_between_text_nodes` into the new modules

`detect_cells._visible_edges_local` currently imports `_visible_edges` from `detect_tables`; `extract_tables_v2._celltable_to_docnode` imports `_between_text_nodes` from `extract_tables`. Inline both so the new modules stand alone before we delete the source.

**Files:**
- Modify: `pdf_parser/stages/detect_cells.py`
- Modify: `pdf_parser/stages/extract_tables_v2.py`

- [ ] **Step 1: Copy `_visible_edges`, `_is_background_color`, `_interval_subtract`, `_clip_line` from `detect_tables.py` into `detect_cells.py`**

Open `pdf_parser/stages/detect_tables.py` and copy the bodies of these four functions verbatim into `pdf_parser/stages/detect_cells.py`, replacing the `_visible_edges_local` thin wrapper:

```python
# (Paste the four function bodies from detect_tables.py here; they only depend
#  on stdlib + pdfplumber types. _BG_COLOR_TOL, _LINE_SNAP_TOL, _AXIS_TOL
#  constants come along.)
```

Then replace the `from pdf_parser.stages.detect_tables import _visible_edges` line with the inlined call:

```python
def _line_cells(page, page_index: int) -> list[Cell]:
    settings = dict(_DEFAULT_TABLE_SETTINGS)
    h_vis, v_vis, had_overdraws = _visible_edges(page)   # now local
    if had_overdraws and len(h_vis) >= 2 and len(v_vis) >= 2:
        settings.update(...)
    ...
```

- [ ] **Step 2: Copy `_between_text_nodes` + its helpers (`_absorb_dangling_bullets_in_cell`, `_join_wrapped_cell_lines`, `_is_cell_bullet_lead`, bullet constants) into `extract_tables_v2.py`**

Copy these from `pdf_parser/stages/extract_tables.py` verbatim into `pdf_parser/stages/extract_tables_v2.py`. Drop the import:

```python
# REMOVE: from pdf_parser.stages.extract_tables import _between_text_nodes
```

- [ ] **Step 3: Run the full suite**

```bash
uv run pytest
```

Expected: full suite green.

- [ ] **Step 4: Commit**

```bash
git add pdf_parser/stages/detect_cells.py pdf_parser/stages/extract_tables_v2.py
git commit -m "refactor: inline _visible_edges and _between_text_nodes into new modules"
```

---

### Task 10.4: Delete `extract_tables.py` and `detect_tables.py`

**Files:**
- Delete: `pdf_parser/stages/extract_tables.py`
- Delete: `pdf_parser/stages/detect_tables.py`

- [ ] **Step 1: Confirm nothing else imports them**

```bash
uv run python -c "import grep" 2>/dev/null || true
```

```bash
git grep -l 'detect_tables\b\|extract_tables\b' -- pdf_parser tests
```

The only matches should be `extract_tables_v2.py` (self), `tests/stages/test_*.py` (which we are about to remove), and the README (Phase 10.6).

- [ ] **Step 2: Delete the modules**

```bash
git rm pdf_parser/stages/extract_tables.py pdf_parser/stages/detect_tables.py
```

- [ ] **Step 3: Delete now-orphaned tests**

```bash
git rm tests/stages/test_extract_tables.py tests/stages/test_detect_tables.py 2>/dev/null || true
```

Also remove any other stage tests that import the deleted modules; the relevant logic is now covered by `tests/stages/test_detect_cells.py`, `tests/stages/test_aggregate_tables.py`, `tests/stages/test_extract_tables_v2.py`, the parity test, and the golden tests.

- [ ] **Step 4: Run the full suite**

```bash
uv run pytest
```

Expected: full suite green.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "refactor: delete detect_tables and extract_tables (cascade replaced by bottom-up)"
```

---

### Task 10.5: Rename `extract_tables_v2.py` → `extract_tables.py`; drop the `use_bottom_up` flag

**Files:**
- Rename: `pdf_parser/stages/extract_tables_v2.py` → `pdf_parser/stages/extract_tables.py`
- Modify: `pdf_parser/pipeline.py`
- Modify: `pdf_parser/cli.py`
- Delete: `tests/test_pipeline_bottom_up_flag.py`
- Delete: `tests/test_bottom_up_parity.py`
- Delete: `tests/stages/test_extract_tables_v2.py` (rename to `test_extract_tables.py`)
- Modify: `tests/test_comprehensive.py` (drop the dual-path parametrize)

- [ ] **Step 1: Rename modules**

```bash
git mv pdf_parser/stages/extract_tables_v2.py pdf_parser/stages/extract_tables.py
git mv tests/stages/test_extract_tables_v2.py tests/stages/test_extract_tables.py
```

Inside the renamed module, update the docstring and `provenance` constant:

```python
"""Stage 4: cell-clustering table extractor (detect_cells → aggregate_tables → DocNode trees)."""
...
_PROVENANCE = {"extractor": "bottom_up", "stage": "extract_tables"}
```

Inside the renamed test file, update the import:

```python
from pdf_parser.stages.extract_tables import extract_tables
```

- [ ] **Step 2: Simplify `pdf_parser/pipeline.py`**

```python
"""Pipeline orchestrator: PDF path → DocNode tree."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

import pdfplumber

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.build_tree import build_tree
from pdf_parser.stages.extract_tables import extract_tables
from pdf_parser.stages.ingest import ingest
from pdf_parser.stages.segment import segment
from pdf_parser.stages.stitch_pages import stitch_tables

if TYPE_CHECKING:
    from pdf_parser.fallback.llm import LLMFallback


def _has_leaf_text(node: DocNode) -> bool:
    stack = [node]
    while stack:
        n = stack.pop()
        if n.text:
            return True
        stack.extend(n.children)
    return False


def _apply_llm_fallback(tree, pdf_path, fb, raw_pages):
    # unchanged from before
    ...


def parse(
    pdf_path: Path | str,
    llm_fallback: Optional["LLMFallback"] = None,
) -> DocNode:
    """Parse ``pdf_path`` and return the document tree."""
    pdf_path = Path(pdf_path)
    raw_pages = ingest(pdf_path)
    segments  = segment(raw_pages)

    with pdfplumber.open(str(pdf_path)) as pdf:
        tables = extract_tables(pdf_path, pdf=pdf)

    tables = stitch_tables(tables)
    tree   = build_tree(segments, tables)

    if llm_fallback is not None and llm_fallback.enabled:
        tree = _apply_llm_fallback(tree, pdf_path, llm_fallback, raw_pages)

    return tree
```

- [ ] **Step 3: Simplify `pdf_parser/cli.py`**

Drop the `bottom_up` option entirely:

```python
@app.command()
def parse(
    path: Path,
    format: str = typer.Option("json", "--format", "-f",
                               help="json | markdown | html | chunks"),
    output: Optional[Path] = typer.Option(None, "--output", "-o",
                                          help="..."),
    validate_only: bool = typer.Option(False, "--validate-only"),
    enable_llm_fallback: bool = typer.Option(False, "--enable-llm-fallback"),
    visualize: Optional[Path] = typer.Option(None, "--visualize"),
) -> None:
    fb = None
    if enable_llm_fallback:
        from pdf_parser.fallback.llm import AnthropicLLMClient, LLMFallback
        fb = LLMFallback(enabled=True, client=AnthropicLLMClient())

    tree = parse_pdf(path, llm_fallback=fb)
    # rest unchanged
```

- [ ] **Step 4: Delete parity and flag tests**

```bash
git rm tests/test_pipeline_bottom_up_flag.py tests/test_bottom_up_parity.py
```

- [ ] **Step 5: Revert `tests/test_comprehensive.py` to single-path fixture**

```python
@pytest.fixture(scope="module")
def tree() -> DocNode:
    return parse(PDF)
```

- [ ] **Step 6: Run the full suite**

```bash
uv run pytest
```

Expected: full suite green; the bottom-up extractor is now THE extractor.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: rename extract_tables_v2→extract_tables; drop use_bottom_up flag and parity harness"
```

---

### Task 10.6: Update docs

**Files:**
- Delete: `docs/anchor_detector.md`
- Modify: `README.md`

- [ ] **Step 1: Delete the obsolete anchor-detector doc**

```bash
git rm docs/anchor_detector.md
```

- [ ] **Step 2: Rewrite the pipeline section of `README.md`**

In `README.md`, replace the "Stages 3+4 – Detect & Extract Tables" block with:

```markdown
  ▼  Stages 3+4 – Detect & Extract Tables  (pdfplumber)
  │  PDF → list[DocNode]  (table subtrees)
  │  A single bottom-up cell-clustering primitive (`detect_cells`) finds every
  │  candidate cell on each page from three evidence sources, ordered by trust:
  │    • line   — bounded by visible horizontal+vertical edges (highest).
  │    • gutter — bounded by persistent whitespace columns + line-gap statistics.
  │    • text   — pdfplumber text-strategy fallback (lowest, prose-guarded).
  │  `aggregate_tables` then clusters cells into rows, rows into tables, and
  │  recurses into each cell to detect nested tables via spatial containment
  │  (no separate "borderless frame" or "mega-table decomposer" passes — they
  │  are all subsumed by the containment rule). Merged cells are detected from
  │  column-anchor alignment and marked `covered=True` on the spanned slots.
```

Remove any mention of `--no-anchor` from the CLI section. Update the `attrs` row in the data-model table to keep the existing description (no change needed — the field set is unchanged).

- [ ] **Step 3: Run the suite one final time**

```bash
uv run pytest
```

Expected: full suite green.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: README pipeline diagram and stage list updated for bottom-up extractor"
```

---

### Task 10.7: Final verification

- [ ] **Step 1: Confirm the file listing matches the new architecture**

```bash
git ls-files pdf_parser/stages/
```

Expected output:

```
pdf_parser/stages/__init__.py
pdf_parser/stages/aggregate_tables.py
pdf_parser/stages/build_tree.py
pdf_parser/stages/detect_cells.py
pdf_parser/stages/extract_tables.py
pdf_parser/stages/ingest.py
pdf_parser/stages/segment.py
pdf_parser/stages/stitch_pages.py
```

(8 files; `detect_tables.py`, `detect_tables_anchor.py`, `extract_tables_v2.py` are gone.)

- [ ] **Step 2: Full green run**

```bash
uv run pytest
```

Expected: every test passes.

- [ ] **Step 3: Inspect the diff to confirm scope**

```bash
git log --oneline main..HEAD
```

You should see ~30 small commits (one per task step bundle) culminating in the Phase-10 deletions.

- [ ] **Step 4: No commit — the work is complete.**

---

# Done

The cascade is gone. One primitive (`detect_cells`) + one clusterer (`aggregate_tables`) produces every table that previously needed five separate detectors. `stitch_pages.py` and `build_tree.py` are untouched. All 27 golden fixtures and every behavioural assertion in `tests/test_comprehensive.py` are green under the new path.

Future maintainers extend table detection by adding evidence sources to `detect_cells.py` or aggregation rules to `aggregate_tables.py` — not by stacking another conditional pass on a 1008-line module.