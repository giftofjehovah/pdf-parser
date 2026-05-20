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


@app.callback()
def _root() -> None:
    """Force subcommand mode so `pdf-parser parse <path>` is the entrypoint."""


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
