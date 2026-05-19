"""Canonical DocNode tree + Chunk record. Invariants enforced at construction."""

from __future__ import annotations

import hashlib
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DEPTH = 3

Kind = Literal[
    "document", "page", "section", "heading", "paragraph",
    "table", "row", "cell", "list", "list_item", "figure",
]


class BBox(BaseModel):
    model_config = ConfigDict(frozen=True)
    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def rounded(self) -> tuple[int, int, int, int, int]:
        return (self.page, round(self.x0), round(self.y0), round(self.x1), round(self.y1))


class DocNode(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    kind: Kind
    bbox: BBox | list[BBox]
    text: Optional[str] = None
    children: list["DocNode"] = Field(default_factory=list)
    attrs: dict = Field(default_factory=dict)
    provenance: dict = Field(default_factory=dict)
    id: str = ""

    @model_validator(mode="after")
    def _finalize(self) -> "DocNode":
        self._check_child_kinds()
        if not self.id:
            self.id = self._compute_id()
        return self

    def _check_child_kinds(self) -> None:
        if self.kind == "table":
            if any(c.kind != "row" for c in self.children):
                raise ValueError("table children must all be row")
        elif self.kind == "row":
            if any(c.kind != "cell" for c in self.children):
                raise ValueError("row children must all be cell")

    def _compute_id(self) -> str:
        bbox = self.bbox if isinstance(self.bbox, BBox) else self.bbox[0]
        material = f"{self.kind}|{bbox.rounded()}|{self.text or ''}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]

    def assert_invariants(self, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise ValueError(f"node exceeds max depth {MAX_DEPTH}")
        for c in self.children:
            c.assert_invariants(depth + 1)


DocNode.model_rebuild()


class Chunk(BaseModel):
    text: str
    breadcrumb: list[str] = Field(default_factory=list)
    page_range: tuple[int, int]
    source_ids: list[str] = Field(default_factory=list)
    kind_summary: str
