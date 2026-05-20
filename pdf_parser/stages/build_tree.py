"""Stage 6: assemble the final DocNode tree from segmented blocks + stitched tables."""

from __future__ import annotations

from pdf_parser.model import BBox, DocNode
from pdf_parser.stages.segment import Block, PageSegmented


def _bbox_top(node_or_bbox) -> float:
    bbox = node_or_bbox.bbox if hasattr(node_or_bbox, "bbox") else node_or_bbox
    if isinstance(bbox, list):
        bbox = bbox[0]
    return bbox.y0



def _bbox_overlaps_tbox(block_bbox: BBox, tbox: BBox) -> bool:
    if block_bbox.page != tbox.page:
        return False
    return not (block_bbox.y1 < tbox.y0 or block_bbox.y0 > tbox.y1)


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


def _build_page(
    seg: PageSegmented,
    table_nodes_anchored_here: list[DocNode],
    table_bboxes_on_page: list[BBox],
) -> DocNode:
    # Drop blocks whose bbox overlaps ANY page-local table bbox (covers every page a stitched table spans).
    free_blocks = [
        b for b in seg.blocks
        if not any(_bbox_overlaps_tbox(b.bbox, tb) for tb in table_bboxes_on_page)
    ]
    nodes: list[DocNode] = [_block_to_node(b) for b in free_blocks] + list(table_nodes_anchored_here)
    nodes.sort(key=_bbox_top)
    nodes = _group_list_items(nodes)
    return DocNode(
        kind="page",
        bbox=BBox(page=seg.index, x0=0, y0=0, x1=seg.width, y1=seg.height),
        children=nodes,
        attrs={"page_index": seg.index},
    )


def _index_tables(tables: list[DocNode]) -> tuple[dict[int, list[DocNode]], dict[int, list[BBox]]]:
    """Return ({anchor_page: [tables...]}, {page: [page-local table bboxes...]}).

    A stitched table is anchored to its FIRST page (so it appears once in the tree),
    but its page-local bbox is registered on every page it spans (so overlapping
    text blocks on later pages are filtered correctly).
    """
    anchors: dict[int, list[DocNode]] = {}
    bboxes_by_page: dict[int, list[BBox]] = {}
    for t in tables:
        page_bboxes = t.bbox if isinstance(t.bbox, list) else [t.bbox]
        anchor_page = page_bboxes[0].page
        anchors.setdefault(anchor_page, []).append(t)
        for bb in page_bboxes:
            bboxes_by_page.setdefault(bb.page, []).append(bb)
    return anchors, bboxes_by_page


def build_tree(segments: list[PageSegmented], tables: list[DocNode]) -> DocNode:
    anchors, bboxes_by_page = _index_tables(tables)
    pages: list[DocNode] = []
    for seg in segments:
        pages.append(_build_page(
            seg,
            anchors.get(seg.index, []),
            bboxes_by_page.get(seg.index, []),
        ))
    root = DocNode(
        kind="document",
        bbox=BBox(page=0, x0=0, y0=0, x1=0, y1=0),
        children=pages,
    )
    root.assert_invariants()
    return root
