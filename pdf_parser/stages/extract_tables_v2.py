"""Stage 4 (bottom-up): detect_cells → aggregate_tables → DocNode trees."""
from __future__ import annotations

from pathlib import Path

import pdfplumber

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.aggregate_tables import CellTable, aggregate
from pdf_parser.stages.detect_cells import detect_cells
# ``_between_text_nodes`` is a pure function over (page_chars, cell_bbox,
# nested_bboxes) → list[paragraph DocNode].  It does NOT belong to the
# legacy cascade we replace; Phase 10 inlines it locally.
from pdf_parser.stages.extract_tables import _between_text_nodes

_PROVENANCE = {"extractor": "bottom_up", "stage": "extract_tables_v2"}


def extract_tables(pdf_path: Path, *, pdf=None) -> list[DocNode]:
    if pdf is not None:
        return _extract(pdf)
    with pdfplumber.open(str(pdf_path)) as opened:
        return _extract(opened)


def _extract(pdf) -> list[DocNode]:
    out: list[DocNode] = []
    for page_idx, page in enumerate(pdf.pages):
        cells = detect_cells(page, page_idx)
        # ``page.extract_words`` feeds aggregate's between-text gap split so
        # inter-table prose (NOTE-MID1/2 on fixture 25) survives when the
        # outer pure-closed_rect frame would otherwise fuse two sub-tables
        # sharing column anchors into one table region.  See
        # ``aggregate_tables._gap_has_between_text``.
        page_words = page.extract_words(
            keep_blank_chars=False, use_text_flow=False,
        )
        tables = aggregate(
            cells, page_height=float(page.height), page_words=page_words,
        )
        page_chars = page.chars
        for t in tables:
            out.append(_celltable_to_docnode(t, page_chars=page_chars))
    return out


def _celltable_to_docnode(
    t: CellTable, page_chars: list[dict] | None = None,
) -> DocNode:
    rows: list[DocNode] = []
    for r_idx, row_texts in enumerate(t.grid):
        cells: list[DocNode] = []
        for c_idx, text in enumerate(row_texts):
            cbox = (t.cell_bboxes[r_idx][c_idx]
                    if r_idx < len(t.cell_bboxes) and c_idx < len(t.cell_bboxes[r_idx])
                    else t.bbox)
            is_covered = (r_idx, c_idx) in t.covered
            attrs: dict = {"align": "left"}
            if is_covered:
                attrs["covered"] = True
            nested_in_cell = [sub for sub in t.nested if _bbox_inside(sub.bbox, cbox)]
            nested_children = [
                _celltable_to_docnode(sub, page_chars=page_chars)
                for sub in nested_in_cell
            ]
            # Between-text: paragraphs that sit in this cell's y-range but
            # outside every nested sub-table.  Sorted with the nested
            # children so vertical order is preserved.
            extras: list[DocNode] = []
            if nested_children and page_chars is not None:
                extras = _between_text_nodes(
                    page_chars, cbox, [sub.bbox for sub in nested_in_cell],
                )
            combined = sorted(
                nested_children + extras,
                key=lambda n: n.bbox.y0 if hasattr(n.bbox, "y0") else n.bbox[0].y0,
            )
            cells.append(DocNode(
                kind="cell",
                bbox=cbox,
                text=text if not combined else None,
                children=combined,
                attrs=attrs,
                provenance=_PROVENANCE,
            ))
        row_bbox = (t.row_bboxes[r_idx]
                    if t.bbox_style == "tight" and r_idx < len(t.row_bboxes)
                    else t.bbox)
        rows.append(DocNode(
            kind="row",
            bbox=row_bbox,
            children=cells,
            attrs={"page": t.page_index, "row_index": r_idx},
        ))
    return DocNode(
        kind="table",
        bbox=t.bbox,
        children=rows,
        attrs={
            "n_rows": len(t.grid),
            "n_cols": len(t.grid[0]) if t.grid else 0,
            "header_signature": t.header_signature,
            "page": t.page_index,
            "page_height": t.page_height,
        },
        provenance=_PROVENANCE,
    )


def _bbox_inside(inner: BBox, outer: BBox, tol: float = 2.0) -> bool:
    return (inner.page == outer.page
            and inner.x0 >= outer.x0 - tol and inner.y0 >= outer.y0 - tol
            and inner.x1 <= outer.x1 + tol and inner.y1 <= outer.y1 + tol)
