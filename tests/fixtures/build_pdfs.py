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


BUILDERS = {
    "01_simple_table": build_01_simple_table,
}


def build_all() -> None:
    for name, builder in BUILDERS.items():
        out_dir = GOLDEN_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        builder(out_dir / "source.pdf")


if __name__ == "__main__":
    build_all()
