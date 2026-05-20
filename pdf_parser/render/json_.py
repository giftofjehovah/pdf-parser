"""JSON renderer — pydantic serialization with stable key order."""

from __future__ import annotations

from pdf_parser.model import DocNode


def to_json(tree: DocNode, indent: int | None = None) -> str:
    return tree.model_dump_json(indent=indent)
