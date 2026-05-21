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
    for i, item in enumerate(data.get("children", [])):
        kind = item.get("kind", "paragraph")
        # Assign unique y-offset to each child to ensure unique bboxes for deduplication
        child_y0 = region.y0 + i
        child_y1 = region.y0 + i + 1
        child_bbox = BBox(page=region.page, x0=region.x0, y0=child_y0, x1=region.x1, y1=child_y1)

        if kind == "heading":
            children.append(DocNode(
                kind="heading",
                text=item.get("text", ""),
                bbox=child_bbox,
                attrs={"level": item.get("level", 1)},
                provenance={"extractor": "llm", "stage": "fallback"},
            ))
        elif kind == "paragraph":
            children.append(DocNode(
                kind="paragraph",
                text=item.get("text", ""),
                bbox=child_bbox,
                provenance={"extractor": "llm", "stage": "fallback"},
            ))
        elif kind == "table":
            rows_data: list[list[str]] = item.get("rows", [])
            row_nodes: list[DocNode] = []
            for r_idx, row in enumerate(rows_data):
                # Each row gets a unique y-offset within the child_bbox
                row_y0 = child_y0 + r_idx * 0.1
                row_bbox = BBox(page=region.page, x0=region.x0, y0=row_y0, x1=region.x1, y1=row_y0 + 0.1)
                cell_nodes = [
                    DocNode(
                        kind="cell",
                        text=str(cell),
                        bbox=BBox(page=region.page, x0=region.x0 + c_idx, y0=row_y0, x1=region.x0 + c_idx + 1, y1=row_y0 + 0.1),
                        provenance={"extractor": "llm", "stage": "fallback"},
                    )
                    for c_idx, cell in enumerate(row)
                ]
                row_nodes.append(DocNode(
                    kind="row",
                    bbox=row_bbox,
                    children=cell_nodes,
                    attrs={"row_index": r_idx, "page": region.page},
                ))
            children.append(DocNode(
                kind="table",
                bbox=child_bbox,
                children=row_nodes,
                attrs={
                    "n_rows": item.get("n_rows", len(rows_data)),
                    "n_cols": item.get("n_cols", len(rows_data[0]) if rows_data else 0),
                    "header_signature": tuple(rows_data[0]) if rows_data else (),
                    "page": region.page,
                },
                provenance={"extractor": "llm", "stage": "fallback"},
            ))
        else:
            # Unknown kind from LLM: surface as a paragraph to avoid silent data loss.
            raw_text = item.get("text", json.dumps(item))
            children.append(DocNode(
                kind="paragraph",
                text=f"[llm:unknown_kind={kind!r}] {raw_text}".strip(),
                bbox=child_bbox,
                provenance={"extractor": "llm", "stage": "fallback", "warn": "unknown_kind"},
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

    def __init__(self, model: str = "claude-3-5-sonnet-20241022", client=None) -> None:
        if client is not None:
            self._anthropic = client
        else:
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
        text_blocks = [b for b in response.content if getattr(b, "type", None) == "text"]
        if not text_blocks:
            raise ValueError(
                f"Anthropic response contained no text block; stop_reason={response.stop_reason!r}"
            )
        return _parse_llm_response(text_blocks[0].text, region)
