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
