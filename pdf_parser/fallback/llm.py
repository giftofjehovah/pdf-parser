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

# ---------------------------------------------------------------------------
# Page renderer (used by AnthropicLLMClient)
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
You are a document parser. Extract the content from this PDF page image.
Respond with ONLY a JSON object matching this schema exactly:
{
  "children": [
    {"kind": "heading", "level": 1, "text": "..."},
    {"kind": "paragraph", "text": "..."},
    {"kind": "table", "n_rows": N, "n_cols": M,
     "rows": [["cell", "cell"], ...]}
  ]
}
Rules:
- kind must be one of: heading, paragraph, table
- heading requires level (1=largest, 3=smallest) and text
- paragraph requires text
- table requires n_rows, n_cols, and rows (list of lists of strings)
- Output ONLY valid JSON; no prose, no markdown fences.
"""


def _render_page_png(pdf_path: Path, page_idx: int, scale: float = 2.0) -> bytes:
    """Render one PDF page to PNG bytes using pypdfium2."""
    import io

    import pypdfium2 as pdfium  # transitive dep via pdfplumber
    from PIL import Image  # requires [llm] extras

    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        page = doc[page_idx]
        bitmap = page.render(scale=scale)
        pil_img: Image.Image = bitmap.to_pil()
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


def _parse_llm_response(text: str, region: BBox) -> DocNode:
    """Convert the LLM JSON string into a page DocNode."""
    import json
    import re

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Tolerate a JSON object embedded in prose.
        m = re.search(r"\{.*\}", text, re.DOTALL)
        data = json.loads(m.group()) if m else {"children": []}

    children: list[DocNode] = []
    for item in data.get("children", []):
        kind = item.get("kind", "paragraph")
        if kind == "heading":
            children.append(DocNode(
                kind="heading",
                text=item.get("text", ""),
                bbox=region,
                attrs={"level": item.get("level", 1)},
                provenance={"extractor": "llm", "stage": "fallback"},
            ))
        elif kind == "paragraph":
            children.append(DocNode(
                kind="paragraph",
                text=item.get("text", ""),
                bbox=region,
                provenance={"extractor": "llm", "stage": "fallback"},
            ))
        elif kind == "table":
            rows_data: list[list[str]] = item.get("rows", [])
            row_nodes: list[DocNode] = []
            for r_idx, row in enumerate(rows_data):
                cell_nodes = [
                    DocNode(
                        kind="cell",
                        text=str(cell),
                        bbox=region,
                        provenance={"extractor": "llm", "stage": "fallback"},
                    )
                    for cell in row
                ]
                row_nodes.append(DocNode(
                    kind="row",
                    bbox=region,
                    children=cell_nodes,
                    attrs={"row_index": r_idx, "page": region.page},
                ))
            children.append(DocNode(
                kind="table",
                bbox=region,
                children=row_nodes,
                attrs={
                    "n_rows": item.get("n_rows", len(rows_data)),
                    "n_cols": item.get("n_cols", len(rows_data[0]) if rows_data else 0),
                    "header_signature": tuple(rows_data[0]) if rows_data else (),
                    "page": region.page,
                },
                provenance={"extractor": "llm", "stage": "fallback"},
            ))

    return DocNode(
        kind="page",
        bbox=region,
        children=children,
        attrs={"page": region.page},
        provenance={"extractor": "llm", "stage": "fallback"},
    )


class AnthropicLLMClient:
    """Claude vision client. Imports anthropic SDK lazily so it is not required at import time."""

    def __init__(self, model: str = "claude-3-5-sonnet-20241022") -> None:
        import anthropic  # noqa: PLC0415
        self._anthropic = anthropic.Anthropic()
        self._model = model

    def parse_region(self, pdf_path: Path, region: BBox) -> DocNode:
        import base64
        img_bytes = _render_page_png(pdf_path, region.page)
        img_b64 = base64.b64encode(img_bytes).decode()
        response = self._anthropic.messages.create(
            model=self._model,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": img_b64,
                        },
                    },
                    {"type": "text", "text": _EXTRACT_PROMPT},
                ],
            }],
        )
        return _parse_llm_response(response.content[0].text, region)
