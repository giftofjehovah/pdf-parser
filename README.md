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
