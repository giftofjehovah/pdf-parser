"""Two bottom-up tables on consecutive pages with matching anchors must stitch.

``stitch_pages.py`` is extractor-agnostic: it keys merge eligibility off
``provenance["extractor"]`` (stripping any ``+stitch`` suffix from prior
merges). This test locks in that bottom-up provenance survives the same
intra-extractor stitch path as legacy ``"lattice"`` / ``"anchor"``.
"""
from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.stitch_pages import stitch_tables

_PROV = {"extractor": "bottom_up", "stage": "extract_tables_v2"}


def _row(page: int, y0: float, y1: float, texts: list[str]) -> DocNode:
    cells = [
        DocNode(
            kind="cell",
            bbox=BBox(page=page, x0=10 + 50 * i, y0=y0, x1=60 + 50 * i, y1=y1),
            text=t,
            attrs={"align": "left"},
            provenance=_PROV,
        )
        for i, t in enumerate(texts)
    ]
    return DocNode(
        kind="row",
        bbox=BBox(page=page, x0=10, y0=y0, x1=10 + 50 * len(texts), y1=y1),
        children=cells,
        attrs={"page": page, "row_index": 0},
    )


def _table(page: int, y0: float, y1: float) -> DocNode:
    rows = [
        _row(page, y0, y0 + 15, ["H1", "H2"]),
        _row(page, y0 + 15, y1, ["a", "b"]),
    ]
    return DocNode(
        kind="table",
        bbox=BBox(page=page, x0=10, y0=y0, x1=110, y1=y1),
        children=rows,
        attrs={
            "n_rows": 2,
            "n_cols": 2,
            "header_signature": ("H1", "H2"),
            "page": page,
            "page_height": 792.0,
        },
        provenance=_PROV,
    )


def test_stitch_bottom_up_tables_across_pages():
    a = _table(page=0, y0=700.0, y1=730.0)   # near bottom of page 0
    b = _table(page=1, y0=100.0, y1=130.0)
    merged = stitch_tables([a, b])
    assert len(merged) == 1
    assert isinstance(merged[0].bbox, list)
    assert merged[0].provenance["extractor"].startswith("bottom_up")
