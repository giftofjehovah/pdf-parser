"""End-to-end tests for the image pipeline: ingest → tree → HTML."""

from __future__ import annotations

import io
from pathlib import Path

import pymupdf
import pytest

from pdf_parser.pipeline import parse
from pdf_parser.render.html import to_html
from pdf_parser.stages.ingest import ImageInfo, ingest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_with_image(width: int = 50, height: int = 50) -> bytes:
    """Build a minimal single-page PDF with one embedded raster image."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 60), "Image Test Page", fontsize=14)
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, width, height), False)
    pix.set_rect(pix.irect, (200, 100, 50))   # a visible colour
    page.insert_image(pymupdf.Rect(72, 90, 300, 250), pixmap=pix)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


@pytest.fixture(scope="module")
def pdf_with_image(tmp_path_factory) -> Path:
    p = tmp_path_factory.mktemp("imgs") / "with_image.pdf"
    p.write_bytes(_make_pdf_with_image())
    return p


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def test_ingest_finds_image(pdf_with_image):
    pages = ingest(pdf_with_image)
    assert len(pages) == 1
    assert len(pages[0].images) == 1
    img = pages[0].images[0]
    assert isinstance(img, ImageInfo)
    assert img.xref > 0
    assert img.width == 50 and img.height == 50


def test_ingest_image_bbox_reasonable(pdf_with_image):
    pages = ingest(pdf_with_image)
    img = pages[0].images[0]
    b = img.bbox
    # image was placed at Rect(72, 90, 300, 250)
    assert abs(b.x0 - 72) < 2
    assert abs(b.y0 - 90) < 2
    assert abs(b.x1 - 300) < 2
    assert abs(b.y1 - 250) < 2


def test_ingest_no_images_on_text_only_pdf():
    simple = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    pages = ingest(simple)
    assert all(len(p.images) == 0 for p in pages)


# ---------------------------------------------------------------------------
# Tree
# ---------------------------------------------------------------------------

def test_tree_contains_figure_node(pdf_with_image):
    tree = parse(pdf_with_image)
    page = tree.children[0]
    figures = [n for n in page.children if n.kind == "figure"]
    assert len(figures) == 1
    fig = figures[0]
    assert fig.attrs.get("xref", 0) > 0
    assert fig.attrs.get("width") == 50
    assert fig.attrs.get("height") == 50


def test_figure_node_bbox_matches_placement(pdf_with_image):
    tree = parse(pdf_with_image)
    fig = next(n for n in tree.children[0].children if n.kind == "figure")
    b = fig.bbox
    assert abs(b.x0 - 72) < 2
    assert abs(b.y0 - 90) < 2


# ---------------------------------------------------------------------------
# HTML renderer
# ---------------------------------------------------------------------------

def test_html_embeds_image_as_data_uri(pdf_with_image):
    tree = parse(pdf_with_image)
    html = to_html(tree, pdf_path=pdf_with_image)
    assert "data:image/" in html
    assert "<img src=" in html


def test_html_without_pdf_path_omits_image_src(pdf_with_image):
    tree = parse(pdf_with_image)
    html = to_html(tree)   # no pdf_path → no data URI
    # figure div still present but img src is empty
    assert "<img src=" in html
    assert "data:image/" not in html
