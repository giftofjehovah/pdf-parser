from pathlib import Path

from pdf_parser.pipeline import parse
from scripts.visualize import render_overlays

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_visualize_writes_one_png_per_page(tmp_path):
    tree = parse(SIMPLE)
    render_overlays(SIMPLE, tree, tmp_path)
    pngs = sorted(tmp_path.glob("page_*.png"))
    assert len(pngs) == 1
    assert pngs[0].stat().st_size > 0
