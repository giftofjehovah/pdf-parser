"""End-to-end tests for the image pipeline: ingest → tree → HTML."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image as PilImage
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas

from pdf_parser.pipeline import parse
from pdf_parser.render.html import to_html
from pdf_parser.stages.ingest import ImageInfo, ingest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pdf_with_image(img_width: int = 50, img_height: int = 50) -> bytes:
    """Build a minimal single-page PDF with one embedded raster image.

    Image is placed at Rect(72, 90, 300, 250) in top-origin page coordinates
    (matching the original PyMuPDF fixture's placement).  pdfplumber reports
    image coordinates in the same top-origin convention, so the test assertions
    on x0/y0/x1/y1 are identical to the original test.
    """
    # Create a solid-colour source image.
    pil_img = PilImage.new("RGB", (img_width, img_height), color=(200, 100, 50))
    img_buf = io.BytesIO()
    pil_img.save(img_buf, format="PNG")
    img_buf.seek(0)

    # US Letter page: 612 × 792 pt.
    # reportlab uses bottom-origin; convert the target top-origin rect:
    #   top-origin rect  : x0=72, y0=90,  x1=300, y1=250
    #   reportlab bottom : x=72,  y=792-250=542, width=228, height=160
    page_w, page_h = 612, 792
    img_x, img_y_top = 72, 90
    img_x1, img_y1_top = 300, 250
    img_rl_y = page_h - img_y1_top          # bottom edge in reportlab coords
    img_rl_w = img_x1 - img_x
    img_rl_h = img_y1_top - img_y_top

    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
    # Insert text near the top (position is baseline; approximate top-origin ~60).
    c.drawString(72, page_h - 60 - 14, "Image Test Page")
    c.drawImage(ImageReader(img_buf), img_x, img_rl_y, width=img_rl_w, height=img_rl_h)
    c.save()
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
    assert img.image_id >= 0
    assert img.width == 50 and img.height == 50


def test_ingest_image_bbox_reasonable(pdf_with_image):
    pages = ingest(pdf_with_image)
    img = pages[0].images[0]
    b = img.bbox
    # image was placed at Rect(72, 90, 300, 250) in top-origin coordinates
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
    assert fig.attrs.get("image_id", -1) >= 0
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
