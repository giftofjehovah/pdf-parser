from pathlib import Path

from pdf_parser.stages.build_tree import build_tree
from pdf_parser.stages.extract_tables import extract_tables
from pdf_parser.stages.ingest import ingest
from pdf_parser.stages.segment import segment
from pdf_parser.stages.stitch_pages import stitch_tables

FIXTURE = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "01_simple_table" / "source.pdf"


def _run(pdf_path):
    pages = ingest(pdf_path)
    segs = segment(pages)
    tables = stitch_tables(extract_tables(pdf_path))
    return build_tree(segs, tables)


SPAN = Path(__file__).resolve().parents[1] / "golden" / "synthetic" / "03_page_spanning" / "source.pdf"


def test_page_spanning_table_does_not_duplicate_cell_text_as_paragraphs():
    tree = _run(SPAN)

    def walk(n):
        yield n
        for c in n.children:
            yield from walk(c)

    # Cell text like "Item number 30" appears on page 2. If overlap filtering
    # is broken, the segmenter's text shows up as a paragraph node on page 2.
    paragraphs = [n for n in walk(tree) if n.kind == "paragraph"]
    leaked = [p for p in paragraphs if p.text and "Item number" in p.text]
    assert leaked == [], f"page-spanning table cell text leaked as paragraphs: {[p.text for p in leaked]}"

def test_root_is_document():
    tree = _run(FIXTURE)
    assert tree.kind == "document"
    assert len(tree.children) == 1  # one page in 01_simple_table


def test_page_has_heading_and_table():
    tree = _run(FIXTURE)
    page = tree.children[0]
    assert page.kind == "page"
    kinds = [c.kind for c in page.children]
    assert "heading" in kinds
    assert "table" in kinds


def test_reading_order_top_to_bottom():
    tree = _run(FIXTURE)
    page = tree.children[0]
    ys = []
    for c in page.children:
        bbox = c.bbox if hasattr(c.bbox, "y0") else c.bbox[0]
        ys.append(bbox.y0)
    assert ys == sorted(ys)


def test_ids_unique():
    tree = _run(FIXTURE)

    def walk(n):
        yield n
        for c in n.children:
            yield from walk(c)

    ids = [n.id for n in walk(tree)]
    assert len(ids) == len(set(ids))
