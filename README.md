# pdf-parser

Deterministic, layout-first PDF parser. Produces a hierarchical `DocNode` tree
that preserves nested tables and tables that overflow across pages. Renders to
JSON / Markdown / HTML and emits RAG chunks.

See `docs/superpowers/specs/2026-05-19-pdf-parser-design.md` for the design.

## How it works

The parser is a six-stage deterministic pipeline (`pdf_parser/pipeline.py`):

```
PDF file
  │
  ▼  Stage 1 – Ingest         (pypdfium2 + pdfplumber)
  │  PDF → PageRaw list
  │  Extracts text spans (text, bbox, font name/size, bold, italic),
  │  vector drawings, and embedded images for every page.
  │  Images smaller than 10 pt in either dimension are dropped as decorative.
  │
  ▼  Stage 2 – Segment        (pure Python)
  │  PageRaw list → PageSegmented list
  │  Groups spans into lines by y-center (2 pt buckets), then classifies
  │  each line using the page's median font size as the body baseline:
  │    • avg size > 1.15 × body, or all-bold at body size  → heading
  │      (level 1 / 2 / 3 by size ratio: >1.6 ×, >1.3 ×, else 3)
  │    • starts with a bullet character                     → list_item
  │    • everything else                                    → paragraph
  │
  ▼  Stages 3+4 – Detect & Extract Tables  (pdfplumber)
  │  PDF → list[DocNode]  (table subtrees)
  │  pdfplumber detects table regions and cell grids. For each region a
  │  table → row → cell DocNode subtree is built. Merged cells are resolved
  │  by reconstructing a logical grid from horizontal and vertical lines.
  │  Each cell is recursively probed for nested tables.
  │
  ▼  Stage 5 – Stitch Pages   (pure Python)
  │  list[DocNode] → list[DocNode]  (cross-page tables merged)
  │  Consecutive tables on adjacent pages whose column anchor x-coordinates
  │  match within 4 pt are merged into one table node with a list[BBox]
  │  spanning all pages. Duplicate header rows are detected by header
  │  signature and stripped from the continuation.
  │
  ▼  Stage 6 – Build Tree     (pure Python)
     PageSegmented list + stitched tables → DocNode (root)
     Tables are indexed by their first (anchor) page. For each page:
       1. Text blocks whose bbox overlaps any table region are suppressed.
       2. Figure nodes are created for images outside table regions.
       3. All nodes are sorted by y0 (reading order).
       4. Consecutive list_items are wrapped in a list node.
     Every page becomes a page node; all pages live under a document root.
     Structural invariants (depth, table shape, reading order) are asserted
     before the tree is returned.
```

### Data model

Every node in the tree is a `DocNode` (pydantic, `model.py`):

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `Kind` | One of `document`, `page`, `section`, `heading`, `paragraph`, `table`, `row`, `cell`, `list`, `list_item`, `figure` |
| `bbox` | `BBox \| list[BBox]` | Bounding box(es) in PDF points. A list means the node spans multiple pages. |
| `text` | `str \| None` | Leaf text content (heading, paragraph, cell, list_item). |
| `children` | `list[DocNode]` | Child nodes. `table` children must all be `row`; `row` children must all be `cell`. |
| `attrs` | `dict` | Kind-specific metadata. E.g. `level` for headings; `n_rows` / `n_cols` / `spans_pages` for tables; `image_id` / `width` / `height` for figures; `covered: true` on merged cells that are spanned over by a prior cell. |
| `provenance` | `dict` | Which extractor and stage produced the node. |
| `id` | `str` | 12-hex SHA-256 fingerprint of kind + bbox + text + child ids. |

### Validation (separate pass)

`pdf_parser/validate/` runs after parsing and is not part of the pipeline itself:

- **Layer 1 — Structural invariants**: well-formedness (no orphaned kinds), table shape (uniform column count), reading order (y0 monotonicity within a page), cross-page integrity (page indices match bbox page fields).
- **Layer 2 — Coverage**: checks that the fraction of PDF text captured in the tree meets a configurable threshold, per page.

`--validate-only` on the CLI runs both layers and exits `0` / `1`.

### Chunking

`chunk_tree()` in `pdf_parser/chunk.py` walks the `DocNode` tree depth-first, carrying a heading breadcrumb. It produces `Chunk` records suitable for RAG ingestion:

- **Paragraphs** are split at 800 tokens (~1 token ≈ 0.75 words) with 100-token overlap between consecutive chunks.
- **Tables** are split row-wise; the header row is repeated at the top of every chunk so each chunk is self-contained.
- Every chunk carries `text`, `breadcrumb` (ancestor heading path), `page_range`, `source_ids` (contributing node ids), and `kind_summary`.

### Stage-by-stage I/O examples

All examples use the `01_simple_table` golden fixture (a one-page PDF with a title,
a short paragraph, and a 3-row × 3-column table). The cross-page stitch example
uses `03_page_spanning`. Every example is Python — the types are the actual objects
in memory at each stage boundary.

---

**Stage 1 — Ingest** &nbsp;`PDF → list[PageRaw]`

pypdfium2 extracts every text span with its bounding box and font metadata; pdfplumber
handles image stream extraction. Each page also carries the raw drawing paths and image descriptors.

```python
PageRaw(
    index=0,
    width=612.0,
    height=792.0,
    spans=[
        Span(text='Simple Table Example', font_size=18.0, bold=True,  italic=False,
             bbox=BBox(page=0, x0=78.0,  y0=76.7,  x1=268.1, y1=101.5)),
        Span(text='The table below has three columns.', font_size=10.0, bold=False, italic=False,
             bbox=BBox(page=0, x0=78.0,  y0=123.2, x1=235.8, y1=137.0)),
        Span(text='Name',     font_size=10.0, bold=False, italic=False,
             bbox=BBox(page=0, x0=245.6, y0=150.2, x1=272.3, y1=164.0)),
        Span(text='Quantity', font_size=10.0, bold=False, italic=False,
             bbox=BBox(page=0, x0=292.1, y0=150.2, x1=329.3, y1=164.0)),
        # … 7 more spans (Price, Apple, 3, $1.00, Banana, 6, $0.50)
    ],
    drawings=[…],   # 9 vector paths (table grid lines)
    images=[],
)
```

---

**Stage 2 — Segment** &nbsp;`list[PageRaw] → list[PageSegmented]`

Spans are clustered into lines and each line is classified. The page's median font
size is 10 pt (body baseline). "Simple Table Example" is 18 pt and bold → heading
level 1. Table cell spans (10 pt, not bold, no bullet) are classified as paragraphs
at this stage — they will be suppressed in Stage 6 once the table region is known.

```python
PageSegmented(
    index=0,
    blocks=[
        Block(kind_hint='heading',   level=1, text='Simple Table Example',
              bbox=BBox(page=0, x0=78.0,  y0=76.7,  x1=268.1, y1=101.5)),
        Block(kind_hint='paragraph', level=0, text='The table below has three columns.',
              bbox=BBox(page=0, x0=78.0,  y0=123.2, x1=235.8, y1=137.0)),
        Block(kind_hint='paragraph', level=0, text='Name Quantity Price',
              bbox=BBox(page=0, x0=245.6, y0=150.2, x1=364.1, y1=164.0)),
        Block(kind_hint='paragraph', level=0, text='Apple 3 $1.00',
              bbox=BBox(page=0, x0=245.6, y0=168.2, x1=366.4, y1=182.0)),
        Block(kind_hint='paragraph', level=0, text='Banana 6 $0.50',
              bbox=BBox(page=0, x0=245.6, y0=186.2, x1=366.4, y1=200.0)),
    ],
)
```

---

**Stages 3 + 4 — Detect & Extract Tables** &nbsp;`PDF → list[DocNode]`

pdfplumber detects the table region from the grid lines and extracts the cell text.
The result is a fully typed `DocNode` subtree. `header_signature` is stored in
`attrs` so Stage 5 can recognise a repeated header on the next page.

```python
DocNode(
    kind='table',
    bbox=BBox(page=0, x0=239.6, y0=148.0, x1=372.4, y1=202.0),
    attrs={'n_rows': 3, 'n_cols': 3, 'header_signature': ('Name', 'Quantity', 'Price'), 'page': 0},
    provenance={'extractor': 'pdfplumber', 'stage': 'extract_tables'},
    id='23b5a81424f2',
    children=[
        DocNode(kind='row', attrs={'page': 0, 'row_index': 0}, id='d94e13f30887', children=[
            DocNode(kind='cell', text='Name',     bbox=BBox(page=0, x0=239.6, y0=148.0, x1=286.1, y1=166.0), id='7cc49c35be4c'),
            DocNode(kind='cell', text='Quantity', bbox=BBox(page=0, x0=286.1, y0=148.0, x1=335.3, y1=166.0), id='5984719d8c18'),
            DocNode(kind='cell', text='Price',    bbox=BBox(page=0, x0=335.3, y0=148.0, x1=372.4, y1=166.0), id='1d00bb54a46f'),
        ]),
        DocNode(kind='row', attrs={'page': 0, 'row_index': 1}, id='35f48c0e8218', children=[
            DocNode(kind='cell', text='Apple',  id='9df466a6e15f'),
            DocNode(kind='cell', text='3',      id='f9318109181c'),
            DocNode(kind='cell', text='$1.00',  id='df6cedbbc53d'),
        ]),
        DocNode(kind='row', attrs={'page': 0, 'row_index': 2}, id='a997c0ed0e7c', children=[
            DocNode(kind='cell', text='Banana', id='ceac38f51a78'),
            DocNode(kind='cell', text='6',      id='a7965d6936aa'),
            DocNode(kind='cell', text='$0.50',  id='37627ab0b3f7'),
        ]),
    ],
)
```

---

**Stage 5 — Stitch Pages** &nbsp;`list[DocNode] → list[DocNode]`

Example from `03_page_spanning` (50-row table split across two pages by reportlab).
The two fragments share the same `header_signature` and their column anchor
x-coordinates match within 4 pt, so they are merged. The duplicate header row from
the second fragment is stripped.

```python
# Before — two separate DocNode tables
[
    DocNode(kind='table', bbox=BBox(page=0, …), attrs={'n_rows': 33, 'header_signature': ('ID', 'Description', 'Value'), …}),
    DocNode(kind='table', bbox=BBox(page=1, …), attrs={'n_rows': 19, 'header_signature': ('ID', 'Description', 'Value'), …}),
]

# After — one merged DocNode; bbox is now a list spanning both pages
[
    DocNode(
        kind='table',
        bbox=[BBox(page=0, …), BBox(page=1, …)],
        attrs={
            'n_rows': 51,          # 33 + 19 − 1 duplicate header
            'n_cols': 3,
            'header_signature': ('ID', 'Description', 'Value'),
            'spans_pages': [0, 1],
        },
        children=[
            DocNode(kind='row', children=[DocNode(kind='cell', text='ID'), …]),          # header
            # … 49 data rows …
            DocNode(kind='row', children=[DocNode(kind='cell', text='50'), DocNode(kind='cell', text='Item number 50'), DocNode(kind='cell', text='$75.00')]),
        ],
    ),
]
```

Single-page PDFs pass through unchanged.

---

**Stage 6 — Build Tree** &nbsp;`list[PageSegmented] + list[DocNode] → DocNode`

Tables are indexed by anchor page. For each page the three paragraph blocks whose
bboxes overlap the table region (`y0=148 … y1=202`) are dropped; only the heading
and the free paragraph survive. Nodes are sorted by `y0` and the table is inserted
in reading order.

```python
DocNode(kind='document', bbox=BBox(page=0, x0=0, y0=0, x1=0, y1=0), children=[
    DocNode(kind='page', bbox=BBox(page=0, x0=0, y0=0, x1=612.0, y1=792.0), children=[
        DocNode(kind='heading',   bbox=BBox(page=0, x0=78.0, y0=76.7,  …), text='Simple Table Example',
                attrs={'level': 1}),
        DocNode(kind='paragraph', bbox=BBox(page=0, x0=78.0, y0=123.2, …), text='The table below has three columns.'),
        DocNode(kind='table', bbox=BBox(page=0, x0=239.6, y0=148.0, x1=372.4, y1=202.0),
                attrs={'n_rows': 3, 'n_cols': 3, …}, children=[
            DocNode(kind='row', children=[DocNode(kind='cell', text='Name'), DocNode(kind='cell', text='Quantity'), DocNode(kind='cell', text='Price')]),
            DocNode(kind='row', children=[DocNode(kind='cell', text='Apple'), DocNode(kind='cell', text='3'), DocNode(kind='cell', text='$1.00')]),
            DocNode(kind='row', children=[DocNode(kind='cell', text='Banana'), DocNode(kind='cell', text='6'), DocNode(kind='cell', text='$0.50')]),
        ]),
    ]),
])
```

---

**Chunks** &nbsp;`DocNode → list[Chunk]`

The heading node is not chunked directly — it becomes part of the `breadcrumb` for
every chunk that follows it. The paragraph and the table each produce one chunk.
Note that `breadcrumb` now correctly carries `'Simple Table Example'` (the heading
that precedes both nodes in the tree).

```python
[
    Chunk(
        text='The table below has three columns.',
        breadcrumb=['Simple Table Example'],
        kind_summary='paragraph',
        page_range=(0, 0),
        source_ids=['5b8957cf2e41'],
    ),
    Chunk(
        text='| Name | Quantity | Price |\n| Apple | 3 | $1.00 |\n| Banana | 6 | $0.50 |',
        breadcrumb=['Simple Table Example'],
        kind_summary='table:3x3',
        page_range=(0, 0),
        source_ids=['23b5a81424f2', '35f48c0e8218', 'a997c0ed0e7c'],
    ),
]
```

#### Table chunking in detail

For a table that fits within `max_tokens` (default 800) the whole table is one chunk.
When the table is larger, it splits row-wise and the header row is repeated at the
top of every chunk so each piece is self-contained for retrieval.

Example: the `03_page_spanning` fixture has a 51-row table stitched across two pages.
At the default budget it fits in one chunk:

```python
Chunk(
    text='| ID | Description | Value |\n| 1 | Item number 1 | $1.50 |\n| 2 | … |\n…\n| 50 | Item number 50 | $75.00 |',
    breadcrumb=['Page-Spanning Table'],
    kind_summary='table:51x3',    # total shape of the original table
    page_range=(0, 1),            # spans both pages
    source_ids=['<table_id>', '<row_1_id>', …, '<row_50_id>'],  # 51 ids total
)
```

With a tighter budget (`max_tokens=100`), the same table splits into 8 chunks.
Every chunk starts with the header row regardless of where in the table it begins:

```python
# chunk[0] — rows 1-7
Chunk(
    text='| ID | Description | Value |\n| 1 | Item number 1 | $1.50 |\n…\n| 7 | Item number 7 | $10.50 |',
    breadcrumb=['Page-Spanning Table'],
    kind_summary='table:51x3',
    page_range=(0, 0),
    source_ids=['<table_id>', '<row_1_id>', …, '<row_7_id>'],
)
# chunk[1] — rows 8-14, header still present
Chunk(
    text='| ID | Description | Value |\n| 8 | Item number 8 | $12.00 |\n…\n| 14 | Item number 14 | $21.00 |',
    breadcrumb=['Page-Spanning Table'],
    kind_summary='table:51x3',
    page_range=(0, 0),
    source_ids=['<table_id>', '<row_8_id>', …, '<row_14_id>'],
)
# … 6 more chunks following the same pattern
```

`source_ids` always includes the parent table's id as the first entry, so you can
look up the full table node from any chunk. `kind_summary` encodes the total shape
(`51x3`) not the per-chunk shape, so filtering on table dimensions works uniformly
across all chunks of the same table.

## Setup

    uv venv && source .venv/bin/activate
    uv pip install -e ".[dev]"

(Add `[llm]` if you want to opt into the per-page LLM fallback.)

## CLI

The package installs a single `pdf-parser` entrypoint with one subcommand, `parse`:

    pdf-parser parse <path> [OPTIONS]

`<path>` is the input PDF. Output goes to stdout; diagnostics go to stderr.

### Try it

The repo ships a small synthetic corpus under `tests/golden/synthetic/`. Pick one and parse it:

    pdf-parser parse tests/golden/synthetic/02_nested_table/source.pdf --format markdown

Expected output: a single `# Nested Table Example` heading followed by a 3×3 table whose middle cell contains a nested 2-column sub-table — proof that nested-table parsing round-trips. Swap `--format markdown` for `json`, `html`, or `chunks` to see the other renderers.

### Options

`-f, --format {json,markdown,html,chunks}` — output shape. Default `json`.

- `json` — full `DocNode` tree (`pydantic` model_dump), pretty-printed with indent=2. Preserves hierarchy, bboxes, page numbers, and nested tables. Use this when you want the raw structured output.
- `markdown` — flattened Markdown rendering: headings become `#`/`##`/…, tables become GitHub-style pipe tables, paragraphs become plain text. Lossy with respect to bboxes.
- `html` — page-faithful absolute layout: each page becomes a white box sized to the PDF page dimensions (scaled 1.5×). Elements are absolutely positioned from their `BBox` coordinates so column widths, row heights, and reading flow match the source. Table fill colours are extracted directly from the PDF. Use this for visual spot-checking or human review.
- `chunks` — RAG-ready chunks as a JSON array. Each chunk carries `text`, `breadcrumb` (heading path), page span, bbox span, and token estimate. Paragraphs are split at ~800 tokens with ~100-token overlap; tables are split row-wise with the header repeated on each piece.

`-o, --output <path>` — write the rendered output to `<path>` instead of stdout. Parent directories are created if missing. Mutually composable with `--visualize`: you can write the parse tree to a file and the bbox overlay PDF in the same invocation.

`--validate-only` — skip rendering. Runs the Layer 1 invariant + coverage checks against the parsed tree and the source PDF, prints any errors to stderr, and exits `0` on pass / `1` on fail. Useful in CI to gate on parse quality without diffing output.

`--enable-llm-fallback` — opt into the per-page LLM fallback for pages the deterministic parser cannot resolve. Requires the `[llm]` extra and an `ANTHROPIC_API_KEY`. **Currently a no-op in v1**: the flag is accepted and a warning is printed; the fallback module is not yet wired into the pipeline.

`--visualize <path>` — also render bbox overlays per node kind (heading / paragraph / table / row / cell / …) for human spot-checking. If `<path>` ends in `.pdf`, writes a single multi-page debug PDF. Otherwise treats `<path>` as a directory and writes one PNG per page (`page_000.png`, `page_001.png`, …). Does not affect the stdout output.

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

### Real-world fixtures

`tests/golden/real_world/` holds golden fixtures derived from real PDFs (Word exports, LaTeX papers, financial reports). The corpus starts empty; add cases with:

    python scripts/add_real_world_fixture.py --add <name> <path-to-pdf>

Inspect the skeleton output before committing:

    python scripts/add_real_world_fixture.py --inspect <name>

Quality bar: all expected headings present, no floating paragraph text where a table node should be, no duplicate text blocks.

    # Update after an intentional parser change
    python scripts/add_real_world_fixture.py --update <name>
