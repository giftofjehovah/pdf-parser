# pdf-parser

Deterministic layout-first PDF parser. See `docs/superpowers/specs/2026-05-19-pdf-parser-design.md` for design.

## Quick start

    uv venv && source .venv/bin/activate
    uv pip install -e ".[dev]"
    pytest

## CLI

    pdf-parser parse <path> [--format json|markdown|html|chunks]
