"""Deterministic synthetic-PDF generator. Same code + pinned reportlab → byte-equivalent PDFs."""

from __future__ import annotations

import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak,
)

# Force reproducible PDFs (reportlab embeds a /CreationDate; pin via env).
os.environ.setdefault("SOURCE_DATE_EPOCH", "1700000000")

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "synthetic"


def _styles():
    return getSampleStyleSheet()


def build_01_simple_table(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    story = [
        Paragraph("Simple Table Example", s["Heading1"]),
        Spacer(1, 12),
        Paragraph("The table below has three columns.", s["BodyText"]),
        Spacer(1, 12),
        Table(
            [["Name", "Quantity", "Price"],
             ["Apple", "3", "$1.00"],
             ["Banana", "6", "$0.50"]],
            style=TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ]),
        ),
    ]
    doc.build(story)


def build_02_nested_table(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    inner = Table(
        [["sub-A", "sub-B"], ["1", "2"], ["3", "4"]],
        style=TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]),
        colWidths=[40, 40],
    )
    outer = Table(
        [
            ["Outer-Col-1", "Outer-Col-2", "Outer-Col-3"],
            ["row-1-a", inner, "row-1-c"],
            ["row-2-a", "row-2-b", "row-2-c"],
        ],
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
        colWidths=[100, 100, 100],
    )
    story = [
        Paragraph("Nested Table Example", s["Heading1"]),
        Spacer(1, 12),
        outer,
    ]
    doc.build(story)


def build_03_page_spanning(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
    s = _styles()
    header = ["ID", "Description", "Value"]
    rows = [header] + [[str(i), f"Item number {i}", f"${i * 1.5:.2f}"] for i in range(1, 51)]
    t = Table(
        rows,
        repeatRows=1,
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]),
        colWidths=[60, 280, 80],
    )
    story = [Paragraph("Page-Spanning Table", s["Heading1"]), Spacer(1, 12), t]
    doc.build(story)


BUILDERS = {
    "01_simple_table": build_01_simple_table,
    "02_nested_table": build_02_nested_table,
    "03_page_spanning": build_03_page_spanning,
}


def build_all() -> None:
    for name, builder in BUILDERS.items():
        out_dir = GOLDEN_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        builder(out_dir / "source.pdf")


if __name__ == "__main__":
    build_all()
