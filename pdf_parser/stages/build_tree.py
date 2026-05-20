"""Stage 6: assemble the final DocNode tree from segmented blocks + stitched tables."""

from __future__ import annotations

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.segment import Block, PageSegmented


def _bbox_top(node_or_bbox) -> float:
    bbox = node_or_bbox.bbox if hasattr(node_or_bbox, "bbox") else node_or_bbox
    if isinstance(bbox, list):
        bbox = bbox[0]
    return bbox.y0


def _bbox_page(node_or_bbox) -> int:
    bbox = node_or_bbox.bbox if hasattr(node_or_bbox, "bbox") else node_or_bbox
    if isinstance(bbox, list):
        bbox = bbox[0]
    return bbox.page


def _bbox_overlaps_table(block: Block, table: DocNode) -> bool:
    tbox = table.bbox[0] if isinstance(table.bbox, list) else table.bbox
    if block.bbox.page != tbox.page:
        return False
    return not (block.bbox.y1 < tbox.y0 or block.bbox.y0 > tbox.y1)


def _block_to_node(block: Block) -> DocNode:
    if block.kind_hint == "heading":
        return DocNode(
            kind="heading",
            bbox=block.bbox,
            text=block.text,
            attrs={"level": block.level},
            provenance={"extractor": "segment", "stage": "build_tree"},
        )
    if block.kind_hint == "list_item":
        return DocNode(
            kind="list_item",
            bbox=block.bbox,
            text=block.text,
            provenance={"extractor": "segment", "stage": "build_tree"},
        )
    return DocNode(
        kind="paragraph",
        bbox=block.bbox,
        text=block.text,
        provenance={"extractor": "segment", "stage": "build_tree"},
    )


def _group_list_items(nodes: list[DocNode]) -> list[DocNode]:
    out: list[DocNode] = []
    buf: list[DocNode] = []
    for n in nodes:
        if n.kind == "list_item":
            buf.append(n)
        else:
            if buf:
                out.append(DocNode(kind="list", bbox=buf[0].bbox, children=buf))
                buf = []
            out.append(n)
    if buf:
        out.append(DocNode(kind="list", bbox=buf[0].bbox, children=buf))
    return out


def _build_page(seg: PageSegmented, tables_on_page: list[DocNode]) -> DocNode:
    # Drop blocks whose bbox overlaps any table region (avoid double-counting cell text).
    free_blocks = [b for b in seg.blocks if not any(_bbox_overlaps_table(b, t) for t in tables_on_page)]
    nodes: list[DocNode] = [_block_to_node(b) for b in free_blocks] + list(tables_on_page)
    nodes.sort(key=_bbox_top)
    nodes = _group_list_items(nodes)
    return DocNode(
        kind="page",
        bbox=BBox(page=seg.index, x0=0, y0=0, x1=seg.width, y1=seg.height),
        children=nodes,
        attrs={"page_index": seg.index},
    )


def _attach_tables_to_pages(tables: list[DocNode]) -> dict[int, list[DocNode]]:
    by_page: dict[int, list[DocNode]] = {}
    for t in tables:
        p = _bbox_page(t)
        by_page.setdefault(p, []).append(t)
    return by_page


def build_tree(segments: list[PageSegmented], tables: list[DocNode]) -> DocNode:
    by_page = _attach_tables_to_pages(tables)
    pages: list[DocNode] = []
    for seg in segments:
        pages.append(_build_page(seg, by_page.get(seg.index, [])))
    root = DocNode(
        kind="document",
        bbox=BBox(page=0, x0=0, y0=0, x1=0, y1=0),
        children=pages,
    )
    root.assert_invariants()
    return root
