"""CLI: pdf-parser parse <path> [--format ...] [--output PATH] [--debug DIR]."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from pdf_parser.chunk import chunk_tree
from pdf_parser.pipeline import parse as parse_pdf
from pdf_parser.render.html import to_html
from pdf_parser.render.json_ import to_json, to_tree_json
from pdf_parser.render.markdown import to_markdown
from pdf_parser.render.llm_prompt import to_llm_prompts
from pdf_parser.validate.report import validate

app = typer.Typer(add_completion=False, help="Deterministic PDF parser.")


@app.callback()
def _root() -> None:
    """Force subcommand mode so `pdf-parser parse <path>` is the entrypoint."""


def _render(tree, format: str, pdf_path: Path | None = None) -> str:
    if format == "json":
        return to_json(tree, indent=2)
    if format == "markdown":
        return to_markdown(tree)
    if format == "html":
        return to_html(tree, pdf_path=pdf_path)
    if format == "chunks":
        return json.dumps([c.model_dump() for c in chunk_tree(tree)], indent=2)
    if format == "llm-prompt":
        return "\n\n=====================\n\n".join(to_llm_prompts(tree))
    if format == "tree":
        return to_tree_json(tree, indent=2)
    raise typer.BadParameter(f"unknown format: {format}", param_hint="--format")


@app.command()
def parse(
    path: Path,
    format: str = typer.Option("json", "--format", "-f",
                               help="json | tree | markdown | html | chunks | llm-prompt"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Write rendered output to PATH instead of stdout. Parent dirs are created.",
    ),
    validate_only: bool = typer.Option(False, "--validate-only"),
    enable_llm_fallback: bool = typer.Option(False, "--enable-llm-fallback"),
    visualize: Optional[Path] = typer.Option(None, "--visualize"),
    debug: Optional[Path] = typer.Option(
        None, "--debug",
        help="Write a debug bundle (overlays, per-stage JSON, manifest) to DIR. "
             "Re-runs the pipeline capturing intermediates. Disables --enable-llm-fallback "
             "for the bundle pass since the bundle reports only the deterministic stages.",
    ),
) -> None:
    if debug is not None:
        # The debug bundle re-runs the pipeline capturing every intermediate.
        # Skip the regular parse and emit the rendered output from the bundle's tree.
        from pdf_parser.debug import parse_with_debug, write_bundle
        bundle = parse_with_debug(path)
        write_bundle(bundle, debug)
        tree = bundle.tree
        typer.echo(f"debug bundle written to {debug}", err=True)
    else:
        fb = None
        if enable_llm_fallback:
            from pdf_parser.fallback.llm import AnthropicLLMClient, LLMFallback
            fb = LLMFallback(enabled=True, client=AnthropicLLMClient())
        tree = parse_pdf(path, llm_fallback=fb)

    if validate_only:
        report = validate(tree, path)
        for e in report.errors:
            typer.echo(e, err=True)
        raise typer.Exit(code=0 if report.passed else 1)

    if visualize is not None:
        from scripts.visualize import render_overlays
        render_overlays(path, tree, visualize)

    rendered = _render(tree, format, pdf_path=path)
    if output is None:
        typer.echo(rendered)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n")


if __name__ == "__main__":
    app()
