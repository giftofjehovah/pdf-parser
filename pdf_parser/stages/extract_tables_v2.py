"""Stage 4 (bottom-up): detect_cells → aggregate_tables → DocNode trees."""
from __future__ import annotations

from pathlib import Path

import pdfplumber

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.aggregate_tables import CellTable, aggregate
from pdf_parser.stages.detect_cells import detect_cells

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
        tables = aggregate(cells, page_height=float(page.height))
        for t in tables:
            out.append(_celltable_to_docnode(t))
    return out


def _celltable_to_docnode(t: CellTable) -> DocNode:
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
            children = [_celltable_to_docnode(sub) for sub in t.nested
                        if _bbox_inside(sub.bbox, cbox)]
            cells.append(DocNode(
                kind="cell",
                bbox=cbox,
                text=text if not children else None,
                children=children,
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
