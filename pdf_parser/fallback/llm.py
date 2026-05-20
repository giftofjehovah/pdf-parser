"""Per-page LLM fallback. Off by default. Anthropic SDK loaded lazily."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

from pdf_parser.model import BBox, DocNode


class LLMClient(Protocol):
    def parse_region(self, pdf_path: Path, region: BBox) -> DocNode: ...


@dataclass
class LLMFallback:
    enabled: bool = False
    client: Optional[LLMClient] = None
    audit_log: list[dict] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.audit_log is None:
            self.audit_log = []


def fallback_for_region(fb: LLMFallback, pdf_path: Path, region: BBox) -> Optional[DocNode]:
    if not fb.enabled or fb.client is None:
        return None
    node = fb.client.parse_region(pdf_path, region)
    fb.audit_log.append({"page": region.page, "bbox": region.model_dump(), "result_id": node.id})
    return node
