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
- `html` — same content as `markdown`, emitted as semantic HTML (`<h1>`, `<table>`, `<p>`, …).
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
