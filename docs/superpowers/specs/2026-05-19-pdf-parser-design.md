# PDF Parser — Design

**Date:** 2026-05-19
**Status:** Draft (pending user review)

## 1. Problem

Parse complex born-digital PDFs with **varied layouts** into a canonical hierarchical
representation that preserves nested tables and tables overflowing across pages.
The output must be usable for downstream rendering (Markdown / HTML / JSON) and for
RAG chunking into a vector store. The parse must be **deterministic** so that
correctness can be verified by automatic checks and regression tests.

### Constraints / context

- PDFs are **born-digital, varied formats** (no fixed template, no OCR required).
- Output is a **generic hierarchical tree** — no prior schema imposed.
- **High-volume batch pipeline**: per-doc cost matters. Deterministic libs first;
  LLM is a per-page fallback only.
- **Python** runtime.
- Validation must verify content equivalence AND hierarchy equivalence
  (especially: tables inside tables stay nested).

### Non-goals

- OCR / scanned-document support (out of scope for v1).
- Imposing a domain schema (e.g., "this is an invoice"). The tree is generic.
- Recovering semantic meaning beyond layout-derived hierarchy.

## 2. Approach (selected)

**Layout-first deterministic pipeline** built on `pymupdf` + `pdfplumber`, with an
optional **per-page LLM fallback** triggered only when structural validation fails
on a region. The main path contains no ML inference and no randomness, so
re-parsing the same PDF with the same pinned library versions produces an
identical tree.

Alternatives considered and rejected for v1:

- *ML layout backbone (`unstructured` / `layoutparser`)*: better on weird layouts
  but introduces non-determinism across versions/GPUs, which conflicts with the
  "same input → same output" requirement.
- *LLM-first extraction*: highest ceiling on pathological docs but expensive at
  batch scale and sampling makes determinism fragile. Retained only as a
  per-page fallback for pages that fail their own self-checks.

## 3. Data model

One polymorphic node type, `DocNode`, is the canonical structure produced by
every parse and consumed by every renderer.

```
DocNode
  id           sha256(kind + page + rounded_bbox + text)[:12]
               — stable, reproducible across runs.
  kind         "document" | "page" | "section" | "heading" | "paragraph"
               | "table" | "row" | "cell" | "list" | "list_item" | "figure"
  bbox         (page_index, x0, y0, x1, y1).
               For cross-page tables: list of bboxes (one per page span).
  text         Extracted text (None for containers).
  children     Ordered list[DocNode].
  attrs        kind-specific dict — font, heading level, table shape (n_rows,
               n_cols), col header signature, cell page index, etc.
  provenance   {extractor, confidence, stage} — for debugging and fallback
               routing.
```

### Invariants enforced at construction

1. `table` children are all `row`; `row` children are all `cell`.
2. Cells may contain any kind — including a `table` (this is how nested tables
   are represented; no special-case type is needed).
3. Children are in **reading order**: pages in page order, blocks within a page
   top→bottom then left→right.
4. Every leaf node's text is a substring of the page's raw text (enables the
   coverage check).
5. `id` is content-addressed — deterministic across runs.
6. Nesting depth is capped (default 3) to prevent runaway on pathological input.

### Why one polymorphic type

- Recursion is uniform: a cell containing a sub-table is `cell → table → row → cell`,
  the same shape as any other table.
- JSON serialization and golden-file diffs are trivial.
- Validation walks one tree shape.

### Renderer mapping (Markdown / HTML)

| `kind`                       | Markdown                          | HTML                       |
|------------------------------|-----------------------------------|----------------------------|
| `document`                   | concat children                   | `<article>`                |
| `section`                    | children, heading first           | `<section>`                |
| `heading`                    | `#`/`##`/… by `attrs.level`       | `<h1>`…`<h6>`              |
| `paragraph`                  | text + blank line                 | `<p>`                      |
| `list` / `list_item`         | `- item`                          | `<ul><li>`                 |
| `table`                      | GFM pipe table                    | `<table>`                  |
| `row` / `cell`               | `\| … \| … \|`                    | `<tr><td>`                 |
| nested `table` inside `cell` | inline HTML fallback (GFM can't nest) | native `<table>` inside `<td>` |
| `figure`                     | `![alt](path)`                    | `<figure>`                 |

Note: GFM cannot represent nested tables. The Markdown renderer falls back to
inline HTML for any cell that contains a child `table`. HTML has no such limit.

### RAG / chunking consumer

A separate module (`chunk.py`) emits `Chunk` records from the tree:

- `text` — chunk content
- `breadcrumb` — list of ancestor heading texts (e.g., `["3. Financials", "3.2 Revenue"]`)
- `page_range` — `(first_page, last_page)`
- `source_ids` — `DocNode.id`s the chunk was assembled from
- `kind_summary` — e.g., `"paragraph"` or `"table:12×4"`

Chunking rules:

- Never split a `row` or `cell`.
- Keep ancestor headings as `breadcrumb` context (not in chunk text).
- Big tables split on row-group boundaries; the header row is repeated in each
  resulting chunk.
- A `--max-tokens` setting governs target chunk size; default 800 tokens with
  100-token overlap on paragraph chunks (no overlap on table chunks).

## 4. Pipeline stages

Each stage is a pure function on typed inputs; no shared mutable state.

```
PDF file
   │
   ▼
[1] ingest          pymupdf → PageRaw[] (text spans w/ bbox+font,
                    drawing paths, image rects).
   │
   ▼
[2] segment         cluster spans into Block[] per page;
                    heading/paragraph/list candidates from font + indent.
   │
   ▼
[3] detect_tables   pdfplumber ruled-line detector + whitespace/column
                    detector per page → TableRegion[] with cell bboxes.
   │
   ▼
[4] extract_tables  build grid for each region; recurse into each cell's
                    bbox to detect nested tables (same code path).
   │
   ▼
[5] stitch_pages    merge tables that span pages (column-anchor +
                    header-signature match); merge orphan sections.
   │
   ▼
[6] build_tree      assemble final DocNode tree; assign stable ids;
                    enforce invariants.
   │
   ▼
[7] validate        Layer 1 structural + coverage checks.
                    On failure → optional LLM fallback for the affected
                    region; otherwise mark region for manual review.
   │
   ▼
[8] render          json | markdown | html | chunks (json+chunks).
```

Properties:

- Stages 1–6 contain no randomness, no network calls, no time reads, no env
  reads. Same PDF + same pinned libraries ⇒ identical tree.
- Nested tables are not a special case — stage 4 simply recurses with a smaller
  bbox.
- LLM fallback is wired in at stage 7 only; the rest of the doc stays
  deterministic even when one page falls back.

## 5. The two hard problems

### 5.1 Cross-page table stitching

A table on page N continues onto page N+1 when all the following hold:

1. The page-N table ends near the bottom margin (configurable; default y1
   within 5% of page height).
2. The page-N+1 candidate table starts near the top margin.
3. **Column anchors match**: x-ranges of columns on N+1 align with those on N
   within tolerance (default 4 pt). Strongest signal.
4. Either: the first row on N+1 matches the header signature of N (drop as
   duplicate), OR there is no header row and column alignment is exact.

When matched:

- Page-N+1 rows are appended to the page-N table.
- Duplicate header row (if present) is dropped.
- The merged table's `bbox` becomes a list of per-page bboxes.
- Each cell/row retains its source page in `attrs.page` so chunks and citations
  point back to the right page.

Section stitching uses the same idea but simpler: a heading on N with no body,
followed by paragraphs on N+1 at the same indent level, merges.

**Failure modes** (handled by the validator, see §6):

- Column shifts by a few points due to a footnote pushing layout → validator
  catches orphan row count mismatch, retries with looser tolerance once, then
  falls back.
- False-positive nested table (two text spans in a cell look like a 2-col
  table) → minimum-area threshold + "every cell non-empty or explicit merged"
  invariant rejects.

### 5.2 Nested table detection

Inside `extract_tables`, after computing each cell's bounding box, the same
`detect_tables` pass is re-run **restricted to that bbox**. If it finds a table
with ≥2 rows and ≥2 columns whose cells fit inside the parent cell, that table
becomes a child `table` node of the cell. Recursion is capped at the depth
limit set in §3.

This works because `pdfplumber.Page.find_tables(table_settings=…)` accepts
explicit bbox cropping — no reimplementation is required, just recursive
invocation on a smaller region.

## 6. Validation (deterministic & verifiable)

Three layers, each catching a different class of bug. Together they implement
the "content + hierarchy preserved" requirement.

### Layer 1 — Structural invariants (every parse, no ground truth)

Pure checks on the tree; fast (<1 s/doc). A failure means the parse is wrong.

- **Coverage**: the multiset of leaf-text characters in the tree equals the
  multiset from `pymupdf.get_text()`, modulo normalized whitespace and a
  documented whitelist of dropped tokens (page numbers, repeating
  headers/footers).
- **Well-formedness**: `table` children are all `row`; `row` children all
  `cell`; depth ≤ cap; no duplicate `id`s; reading order monotonic per page.
- **Table shape**: every row in a table has the same column count (or explicit
  `colspan` in `attrs`); no orphan rows; column-anchor x-ranges consistent
  within a table.
- **Cross-page integrity**: stitched tables have continuous row indexing;
  per-page bboxes are in page order.

These run in CI on every parse and on every PDF in the golden corpus.

### Layer 2 — Golden corpus (regression tests)

`tests/golden/<case>/source.pdf` paired with `expected_tree.json`. Re-parsing
must produce a tree **structurally equal** to the expected JSON. Equality
compares `kind`, `text`, `children` order, and `attrs` (excluding bbox
floating-point noise — bboxes are rounded to 1 pt for comparison).

The corpus has **two halves**:

- `tests/golden/synthetic/<case>/` — PDFs generated by `tests/fixtures/build_pdfs.py`
  using `reportlab`. The generator is the source of truth: rerunning it
  reproduces the PDFs byte-equivalently (with a fixed seed and pinned
  `reportlab` version). Used for the core invariant cases.
- `tests/golden/real/<case>/` — real PDFs collected from production. Added as
  pathological cases surface. The PDF binary is committed (small docs only;
  large docs referenced by hash + fetched from an external store).

Initial synthetic corpus (≥5 cases):

| case                  | exercises                                          |
|-----------------------|----------------------------------------------------|
| `01_simple_table`     | single-page, single table, no nesting              |
| `02_nested_table`     | table whose cell contains a sub-table (Layer 3 hero) |
| `03_page_spanning`    | table that overflows pages 1→2→3 with repeated header |
| `04_multi_column`     | two-column page layout, reading order across columns |
| `05_sections_lists`   | hierarchical headings + nested lists + a small table |

Adding a case: edit `build_pdfs.py` (or drop a real PDF into `real/<case>/`),
run `scripts/update_goldens.py --case <name>`, hand-inspect the rendered HTML
that the script also writes, commit the PDF + JSON + skeleton. Diff-driven
workflow — when a change shifts a tree, the diff shows exactly what regressed.

### Layer 3 — Hierarchy-equivalence test (the user's explicit ask)

For each corpus PDF, a minimal `expected_skeleton.json` alongside it lists only
`kind`, `text`, and `children`. The test:

```python
assert tree_skeleton(parse(pdf)) == load(expected_skeleton.json)
```

`tree_skeleton` drops bboxes, ids, attrs, provenance — keeping only structure.
This is the laser-focused check for "tables-within-tables stayed nested with
the right data."

### LLM fallback's place in validation

When Layer 1 invariants fail for a page region, the optional LLM fallback is
invoked for just that region. Its output is re-run through Layer 1. If it also
fails, the region is marked `status: "manual_review"` in the output, the
pipeline keeps going (a batch run does not crash), and the page image is
logged for human review. Fallback is **disabled by default in batch runs** and
must be opted in per-doc.

### Tooling

- `pytest` runs the three layers (three test files).
- `hypothesis` is used for renderer property tests: parse → render → re-parse
  is idempotent for the subset of trees the renderer is lossless on.
- `scripts/visualize.py` renders bbox overlays on the source PDF (one PNG per
  page) for human spot-checking. Not run in CI.
- `scripts/update_goldens.py --case <name>` regenerates the expected JSON;
  reviewing the git diff IS the review.

## 7. Project layout

```
pdf-parser/
├── pyproject.toml              # pinned versions
├── README.md
├── pdf_parser/
│   ├── __init__.py
│   ├── model.py                # DocNode, Chunk, ids, invariants
│   ├── pipeline.py             # orchestrates stages 1–8
│   ├── stages/
│   │   ├── ingest.py
│   │   ├── segment.py
│   │   ├── detect_tables.py
│   │   ├── extract_tables.py
│   │   ├── stitch_pages.py
│   │   └── build_tree.py
│   ├── validate/
│   │   ├── invariants.py
│   │   ├── coverage.py
│   │   └── report.py
│   ├── render/
│   │   ├── json_.py
│   │   ├── markdown.py
│   │   └── html.py
│   ├── chunk.py
│   ├── fallback/
│   │   └── llm.py              # off by default
│   └── cli.py                  # `pdf-parser parse FILE [--format json|md|html|chunks]`
├── tests/
│   ├── test_invariants.py
│   ├── test_golden.py
│   ├── test_hierarchy.py
│   ├── test_renderers.py
│   ├── fixtures/
│   │   └── build_pdfs.py       # reportlab-based synthetic PDF generator
│   └── golden/
│       ├── synthetic/
│       │   └── <case>/
│       │       ├── source.pdf            # generated by build_pdfs.py
│       │       ├── expected_tree.json
│       │       └── expected_skeleton.json
│       └── real/
│           └── <case>/
│               ├── source.pdf            # real-world PDF
│               ├── expected_tree.json
│               └── expected_skeleton.json
├── scripts/
│   ├── visualize.py
│   ├── build_fixtures.py       # thin wrapper: runs build_pdfs.py
│   └── update_goldens.py
└── docs/
    └── superpowers/specs/
        └── 2026-05-19-pdf-parser-design.md   # this file
```

## 8. Dependencies

Pinned in `pyproject.toml` / `uv.lock`.

| Library          | Role                                           | Notes              |
|------------------|------------------------------------------------|--------------------|
| `pymupdf`        | Text + layout extraction (primary ingester)    | Fast, accurate.    |
| `pdfplumber`     | Table detection (ruling + whitespace)          | Used recursively.  |
| `pydantic`       | `DocNode` validation, JSON serialization       | Invariants at construction. |
| `pytest`         | Test runner                                    | —                  |
| `hypothesis`     | Property tests for renderers                   | —                  |
| `pillow`         | Bbox overlay rendering (`visualize.py`)        | Dev-only.          |
| `reportlab`      | Synthetic test-PDF generation                  | Dev-only; pinned so PDFs regenerate identically. |
| `typer`          | CLI                                            | Lightweight.       |
| `anthropic`      | LLM fallback                                   | Optional dep; loaded only if fallback enabled. |

Deliberately excluded: `unstructured`, `layoutparser`, `detectron2`,
`opencv`-heavy stacks — they add ML non-determinism that conflicts with the
core guarantee.

## 9. Determinism guarantees

- All library versions pinned (`pyproject.toml` + `uv.lock`).
- Stages 1–6 contain no randomness, no network, no time/env reads.
- `DocNode.id = sha256(kind + page + rounded_bbox + text)[:12]`. Reproducible.
- LLM fallback (when enabled) logs prompt + response so its non-determinism is
  auditable; disabled by default in batch runs.

## 10. CLI surface (v1)

```
pdf-parser parse <path>           # default format: json
pdf-parser parse <path> --format markdown|html|chunks
pdf-parser parse <path> --validate-only            # exit non-zero on invariant failures
pdf-parser parse <path> --enable-llm-fallback      # opt-in per doc
pdf-parser parse <path> --visualize <out-dir>      # write bbox overlay PNGs
```

Batch mode is a thin wrapper that fans out per file; not part of the v1
binary's core surface beyond a `--input-dir / --output-dir` pair.

## 11. Open questions (to resolve during planning, not blocking this design)

- Exact pinning of `pymupdf` / `pdfplumber` versions — pick latest stable at
  plan time and lock.
- Heuristic thresholds (margin %, column-anchor pt tolerance) — start with the
  defaults stated above; tune against the golden corpus as it grows.
- Whether to emit OpenAI-style or Anthropic-style chunk metadata — defer until
  the vector-store choice is known.

## 12. Out of scope for v1

- OCR / scanned PDFs.
- Form-field extraction (AcroForm).
- Domain-specific schemas (invoices, contracts).
- A web UI / API server (the CLI is the v1 entry point).
