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
from reportlab.platypus.flowables import BalancedColumns

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


def build_04_multi_column(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    long_text = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 8
    flow = [Paragraph(long_text, s["BodyText"]) for _ in range(4)]
    story = [
        Paragraph("Two-Column Layout", s["Heading1"]),
        Spacer(1, 12),
        BalancedColumns(flow, nCols=2),
    ]
    doc.build(story)


def build_05_sections_lists(out: Path) -> None:
    from reportlab.platypus import ListFlowable, ListItem
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    story = [
        Paragraph("Sections And Lists", s["Heading1"]),
        Spacer(1, 8),
        Paragraph("1. Background", s["Heading2"]),
        Paragraph("This is the background paragraph.", s["BodyText"]),
        Spacer(1, 6),
        Paragraph("2. Findings", s["Heading2"]),
        ListFlowable(
            [ListItem(Paragraph(t, s["BodyText"])) for t in ("First finding.", "Second finding.", "Third finding.")],
            bulletType="bullet",
        ),
        Spacer(1, 8),
        Paragraph("2.1 Detail Table", s["Heading3"]),
        Table(
            [["A", "B"], ["1", "2"], ["3", "4"]],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]),
        ),
    ]
    doc.build(story)


def build_06_page_spanning_no_header_repeat(out: Path) -> None:
    """Multi-page table whose continuation pages have NO repeated header row.

    Mirrors build_03 except `repeatRows` is omitted, so the header appears
    only on page 1; page-2+ start directly with a data row. Stitcher must
    merge by column-anchor alone and keep every continuation row.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
    s = _styles()
    header = ["ID", "Description", "Value"]
    rows = [header] + [[str(i), f"Item number {i}", f"${i * 1.5:.2f}"] for i in range(1, 51)]
    t = Table(
        rows,
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]),
        colWidths=[60, 280, 80],
    )
    story = [Paragraph("Page-Spanning Table (no header repeat)", s["Heading1"]), Spacer(1, 12), t]
    doc.build(story)


def build_07_page_spanning_with_nested(out: Path) -> None:
    """Multi-page outer table with nested sub-tables on BOTH pages, no header repeat.

    Layout:
      - 3 outer columns: Step, Inputs, Notes.
      - Header on page 1 only (no repeatRows).
      - Two of the data rows embed a small 2x2 sub-table in the "Inputs" cell:
        one positioned early enough to land on page 1, one late enough to land
        on page 2. Stitcher must merge the two per-page outer tables, keep all
        rows, and the extractor must still surface both nested sub-tables.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
    s = _styles()

    def _sub(label: str) -> Table:
        return Table(
            [[f"{label}-A", f"{label}-B"], ["1", "2"]],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]),
            colWidths=[50, 50],
        )

    rows: list[list] = [["Step", "Inputs", "Notes"]]
    # Plain rows 1..50; inject a nested table at row 5 (page 1) and row 45 (page 2).
    for i in range(1, 51):
        if i == 5:
            rows.append([str(i), _sub("p1"), "has nested"])
        elif i == 45:
            rows.append([str(i), _sub("p2"), "has nested"])
        else:
            rows.append([str(i), f"plain input {i}", f"note {i}"])
    t = Table(
        rows,
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
        colWidths=[70, 280, 150],
    )
    story = [
        Paragraph("Page-Spanning Table With Nested Sub-Tables", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)


def build_08_page_spanning_subtable_split(out: Path) -> None:
    """Multi-page outer table whose nested sub-table also straddles the page break.

    The outer table has no repeated header on page 2. A nested sub-table is
    laid out as two halves placed in adjacent outer rows tuned so that:
      - the first half (header + 3 data rows) is the last data row on page 1;
      - the second half (3 data rows, no header) is the first data row on page 2;
      - both halves share identical column widths, so their column anchors
        match and the extractor can recognise them as one continued sub-table.

    Exercises the line-based outer-column detection: with no repeated outer
    header on page 2, the page-2 outer column anchors must be derived from
    full-table-height vertical edges, otherwise the inner sub-table's vertical
    edges would split the outer row into 6 spurious columns.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
    s = _styles()

    def _inner(data: list[list[str]], with_header: bool) -> Table:
        body = ([["sub-H1", "sub-H2"]] if with_header else []) + data
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]
        if with_header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey))
        return Table(body, style=TableStyle(style), colWidths=[60, 60])

    rows: list[list] = [["Step", "Detail", "Notes"]]
    for i in range(1, 50):
        if i == 28:
            rows.append([str(i), _inner([["a", "1"], ["b", "2"], ["c", "3"]], with_header=True),
                         "ends pg1"])
        elif i == 29:
            rows.append([str(i), _inner([["d", "4"], ["e", "5"], ["f", "6"]], with_header=False),
                         "starts pg2"])
        else:
            rows.append([str(i), f"plain {i}", f"n{i}"])

    t = Table(
        rows,
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]),
        colWidths=[60, 200, 80],
    )
    story = [
        Paragraph("Sub-Table Spanning Pages", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)


def build_09_mixed_toc_and_spanning_table(out: Path) -> None:
    """Realistic document: prose + table of contents + multi-page data table.

    Layout:
      - Page 1: title heading, two intro paragraphs, then a 2-column
        "Contents" table (section name / page) with grid lines so the
        parser recognises it as a table rather than running text.
      - Page 2: section headings + prose paragraphs for the first two
        sections referenced by the TOC.
      - Page 3+: a 4-column data table with ~60 rows that overflows into
        a second page (no `repeatRows`, so the continuation page has no
        header — exercises both the page-spanning stitcher and the
        section/prose interleaving on the surrounding pages).
      - Final page: closing prose for the Appendix section.

    Exercises the full mix the parser is supposed to handle end-to-end:
    headings of multiple levels, body paragraphs, a small structured table,
    and a large page-spanning table, all in one document.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
    s = _styles()

    # TOC rendered as plain numbered lines with dot-leader fill + page number,
    # NOT a bordered table. Exercises the parser on tabular-looking prose
    # that must not be misclassified as a table.
    toc_entries = [
        ("1. Executive Summary", "2"),
        ("2. Methodology", "2"),
        ("3. Detailed Results", "3"),
        ("4. Appendix", "5"),
    ]
    LINE_WIDTH = 72  # characters; aligns visually in the default body font
    toc_lines = [
        Paragraph(
            f"{title} {'.' * max(3, LINE_WIDTH - len(title) - len(page) - 2)} {page}",
            s["BodyText"],
        )
        for title, page in toc_entries
    ]

    data_header = ["ID", "Region", "Metric", "Value"]
    data_rows = [data_header] + [
        [str(i), f"Region-{(i % 4) + 1}", f"metric-{i}", f"{i * 3.25:.2f}"]
        for i in range(1, 61)
    ]
    data_table = Table(
        data_rows,
        style=TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ]),
        colWidths=[50, 110, 200, 80],
    )

    prose = (
        "This report summarises the deterministic synthetic corpus used to "
        "exercise the parser end-to-end. The text below is intentionally "
        "long enough to fill several lines so paragraph segmentation has "
        "real input to operate on."
    )

    story = [
        Paragraph("Annual Report 2025", s["Heading1"]),
        Spacer(1, 12),
        Paragraph(prose, s["BodyText"]),
        Spacer(1, 12),
        Paragraph(prose, s["BodyText"]),
        Spacer(1, 18),
        Paragraph("Contents", s["Heading2"]),
        Spacer(1, 8),
        *toc_lines,
        PageBreak(),

        Paragraph("1. Executive Summary", s["Heading2"]),
        Paragraph(prose, s["BodyText"]),
        Spacer(1, 12),
        Paragraph("2. Methodology", s["Heading2"]),
        Paragraph(prose, s["BodyText"]),
        PageBreak(),

        Paragraph("3. Detailed Results", s["Heading2"]),
        Spacer(1, 8),
        Paragraph(
            "The table below lists every observation. It is large enough "
            "to overflow onto the next page so the stitcher must merge the "
            "two halves back into one table.",
            s["BodyText"],
        ),
        Spacer(1, 8),
        data_table,
        Spacer(1, 18),

        Paragraph("4. Appendix", s["Heading2"]),
        Paragraph(prose, s["BodyText"]),
    ]
    doc.build(story)



def build_10_merged_cells(out: Path) -> None:
    """Table with merged cells: one colspan spanning all columns, one rowspan.

    Layout (3 cols × 5 rows):
      Row 0: "Quarterly Report" colspan across all 3 columns.
      Row 1: Normal sub-header "Region", "Q1", "Q2".
      Row 2: "North" rowspan over rows 2–3 in col 0; data "100", "200".
      Row 3: col 0 is the rowspan continuation (empty data cell); "120", "180".
      Row 4: Normal row "South", "300", "400".

    Exercises both horizontal (colspan) and vertical (rowspan) merged cells in
    a single table so the extractor must place each cell's text exactly once in
    the upper-left logical cell of the span.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    data = [
        ["Quarterly Report", "",    ""   ],
        ["Region",           "Q1",  "Q2" ],
        ["North",            "100", "200"],
        ["",                 "120", "180"],
        ["South",            "300", "400"],
    ]
    t = Table(
        data,
        colWidths=[150, 80, 80],
        style=TableStyle([
            ("SPAN", (0, 0), (2, 0)),   # colspan: "Quarterly Report" over all 3 cols
            ("SPAN", (0, 2), (0, 3)),   # rowspan: "North" over rows 2–3 in col 0
            ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
            ("BACKGROUND", (0, 1), (-1, 1), colors.lightgrey),
            ("ALIGN",      (0, 0), (2, 0), "CENTER"),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ]),
    )
    story = [
        Paragraph("Merged Cells Example", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)


def build_11_pl_statement(out: Path) -> None:
    """4-year P&L statement: 34 rows × 5 tight columns (label + FY2021–FY2024).

    Exercises the parser on a dense financial table:
      - 7.5 pt font in 63 pt-wide numeric columns ("very small cells")
      - Mixed positive / negative values; negatives in parentheses
      - Section-header rows with distinct background across all columns
      - Subtotal / total rows differentiated by bold font and thin rules
      - Percentage rows interspersed with absolute-value rows
      - Right-aligned numeric columns, left-aligned label column
      - Realistic section structure: Revenue → COGS → OpEx → Other → EPS
    """

    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=54, rightMargin=54, topMargin=54, bottomMargin=54,
    )
    s = _styles()

    # ------------------------------------------------------------------ data
    # Each row is (label, FY2021, FY2022, FY2023, FY2024).
    # Row-type tags drive styling below; they are NOT in the data list.
    # Types: H=column header, S=section header, I=indent item,
    #        T=subtotal, K=key total, P=percentage/rate, B=blank separator
    rows_tagged = [
        # type, label, fy21, fy22, fy23, fy24
        ("H",  "Income Statement",               "FY2021",   "FY2022",   "FY2023",   "FY2024"),
        ("S",  "Revenue",                        "",         "",         "",         ""),
        ("I",  "  Product Revenue",              "12,450",   "15,320",   "18,760",   "22,140"),
        ("I",  "  Service Revenue",              "2,890",    "3,450",    "4,120",    "5,380"),
        ("I",  "  Other Revenue",                "480",      "620",      "710",      "850"),
        ("T",  "Total Revenue",                  "15,820",   "19,390",   "23,590",   "28,370"),
        ("S",  "Cost of Revenue",                "",         "",         "",         ""),
        ("I",  "  Cost of Goods Sold",           "(8,240)",  "(9,950)",  "(11,820)", "(13,760)"),
        ("I",  "  Depreciation & Amort.",        "(320)",    "(380)",    "(430)",    "(480)"),
        ("T",  "Total Cost of Revenue",          "(8,560)",  "(10,330)", "(12,250)", "(14,240)"),
        ("K",  "Gross Profit",                   "7,260",    "9,060",    "11,340",   "14,130"),
        ("P",  "  Gross Margin %",               "45.9%",    "46.7%",    "48.1%",    "49.8%"),
        ("S",  "Operating Expenses",             "",         "",         "",         ""),
        ("I",  "  Research & Development",       "(2,140)",  "(2,660)",  "(3,180)",  "(3,720)"),
        ("I",  "  Sales & Marketing",            "(3,280)",  "(3,950)",  "(4,840)",  "(5,600)"),
        ("I",  "  General & Administrative",     "(1,120)",  "(1,280)",  "(1,460)",  "(1,640)"),
        ("I",  "  Stock-Based Compensation",     "(890)",    "(1,020)",  "(1,190)",  "(1,380)"),
        ("T",  "Total Operating Expenses",       "(7,430)",  "(8,910)",  "(10,670)", "(12,340)"),
        ("K",  "Operating Income (EBIT)",        "(170)",    "150",      "670",      "1,790"),
        ("P",  "  EBIT Margin %",                "-1.1%",    "0.8%",     "2.8%",     "6.3%"),
        ("S",  "Other Items",                    "",         "",         "",         ""),
        ("I",  "  Interest Income",              "85",       "110",      "190",      "340"),
        ("I",  "  Interest Expense",             "(420)",    "(390)",    "(350)",    "(290)"),
        ("I",  "  Other Income (Expense), net",  "40",       "25",       "(30)",     "60"),
        ("T",  "Total Other Items",              "(295)",    "(255)",    "(190)",    "110"),
        ("T",  "Pre-Tax Income (Loss)",          "(465)",    "(105)",    "480",      "1,900"),
        ("I",  "  Income Tax Provision",         "\u2014",   "\u2014",   "(96)",     "(418)"),
        ("K",  "Net Income (Loss)",              "(465)",    "(105)",    "384",      "1,482"),
        ("P",  "  Net Margin %",                 "-2.9%",    "-0.5%",    "1.6%",     "5.2%"),
        ("S",  "Per Share Data",                 "",         "",         "",         ""),
        ("I",  "  Basic EPS",                    "$(0.47)",  "$(0.11)",  "$0.38",    "$1.49"),
        ("I",  "  Diluted EPS",                  "$(0.47)",  "$(0.11)",  "$0.37",    "$1.44"),
        ("I",  "  Wtd-Avg Shares, Basic (M)",    "99.1",     "99.8",     "100.2",    "99.5"),
        ("I",  "  Wtd-Avg Shares, Diluted (M)",  "103.4",    "103.9",    "104.1",    "103.8"),
    ]

    data = [[row[1], row[2], row[3], row[4], row[5]] for row in rows_tagged]
    types = [row[0] for row in rows_tagged]
    n = len(data)

    # ---------------------------------------------------------------- palette
    _GREY_HEADER  = colors.HexColor("#D0D0D0")
    _GREY_SECTION = colors.HexColor("#E8E8E8")
    _GREY_TOTAL   = colors.HexColor("#F0F0F0")
    _GREY_KEY     = colors.HexColor("#DDEEFF")
    _WHITE        = colors.white

    # ----------------------------------------------------------------- styles
    cmd: list = [
        ("FONTNAME",    (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 0), (-1, -1), 7.5),
        ("LEADING",     (0, 0), (-1, -1), 9),
        ("TOPPADDING",  (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",(0, 0), (-1, -1), 3),
        # Grid
        ("GRID",        (0, 0), (-1, -1), 0.4, colors.HexColor("#AAAAAA")),
        # Numeric columns: right-align
        ("ALIGN",       (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN",       (0, 0), (0, -1),  "LEFT"),
    ]

    for i, t in enumerate(types):
        if t == "H":
            cmd += [
                ("BACKGROUND",  (0, i), (-1, i), _GREY_HEADER),
                ("FONTNAME",    (0, i), (-1, i), "Helvetica-Bold"),
                ("ALIGN",       (0, i), (-1, i), "CENTER"),
            ]
        elif t == "S":
            cmd += [
                ("BACKGROUND",  (0, i), (-1, i), _GREY_SECTION),
                ("FONTNAME",    (0, i), (-1, i), "Helvetica-Bold"),
                ("FONTSIZE",    (0, i), (-1, i), 7.0),
            ]
        elif t == "T":
            cmd += [
                ("BACKGROUND",  (0, i), (-1, i), _GREY_TOTAL),
                ("FONTNAME",    (0, i), (-1, i), "Helvetica-Bold"),
                ("LINEABOVE",   (0, i), (-1, i), 0.5, colors.black),
            ]
        elif t == "K":
            cmd += [
                ("BACKGROUND",  (0, i), (-1, i), _GREY_KEY),
                ("FONTNAME",    (0, i), (-1, i), "Helvetica-Bold"),
                ("LINEABOVE",   (0, i), (-1, i), 0.8, colors.black),
                ("LINEBELOW",   (0, i), (-1, i), 0.8, colors.black),
            ]
        elif t == "P":
            cmd += [
                ("FONTNAME",    (0, i), (-1, i), "Helvetica-Oblique"),
                ("TEXTCOLOR",   (0, i), (-1, i), colors.HexColor("#444444")),
            ]

    col_widths = [195, 63, 63, 63, 63]
    t = Table(data, colWidths=col_widths, style=TableStyle(cmd), repeatRows=1)
    story = [
        Paragraph("Consolidated Income Statement ($000s unless noted)", s["Heading2"]),
        Spacer(1, 6),
        t,
    ]
    doc.build(story)



def _make_chart_png() -> bytes:
    """Generate a deterministic 420×260 bar chart PNG using Pillow only."""
    import io
    from PIL import Image, ImageDraw, ImageFont

    W, H = 420, 260
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    # Revenue data: (quarter, product_rev, service_rev) in $000s
    data = [
        ("Q1", 12_450, 2_890),
        ("Q2", 15_320, 3_450),
        ("Q3", 18_760, 4_120),
        ("Q4", 22_140, 5_380),
    ]
    BLUE   = (70, 130, 180)
    ORANGE = (220, 120, 60)
    BLACK  = (0, 0, 0)
    GRAY   = (180, 180, 180)

    ml, mr, mt, mb = 35, 15, 25, 30
    x0, x1, y0, y1 = ml, W - mr, mt, H - mb
    cw, ch = x1 - x0, y1 - y0
    max_v = max(p + s for _, p, s in data)
    gw = cw // len(data)
    bw = gw // 3

    # Horizontal grid lines at 25 % intervals
    for frac in (0.25, 0.50, 0.75, 1.0):
        gy = y1 - int(frac * ch)
        draw.line([x0, gy, x1, gy], fill=GRAY, width=1)

    for i, (lbl, prod, svc) in enumerate(data):
        gx = x0 + i * gw + gw // 6
        # Product bar (blue)
        bh = int(prod / max_v * ch)
        draw.rectangle([gx, y1 - bh, gx + bw, y1], fill=BLUE)
        # Service bar (orange), immediately to the right
        gx2 = gx + bw + 2
        bh2 = int(svc / max_v * ch)
        draw.rectangle([gx2, y1 - bh2, gx2 + bw, y1], fill=ORANGE)
        # Quarter label below the group
        lx = x0 + i * gw + gw // 2 - 6
        draw.text((lx, y1 + 3), lbl, fill=BLACK, font=font)

    # Axes
    draw.line([x0, y0, x0, y1], fill=BLACK, width=1)
    draw.line([x0, y1, x1, y1], fill=BLACK, width=1)

    # Legend (top-right)
    lx, ly = x1 - 130, y0
    draw.rectangle([lx, ly, lx + 10, ly + 8], fill=BLUE)
    draw.text((lx + 13, ly), "Product Rev", fill=BLACK, font=font)
    draw.rectangle([lx, ly + 12, lx + 10, ly + 20], fill=ORANGE)
    draw.text((lx + 13, ly + 12), "Service Rev", fill=BLACK, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def build_12_image_chart(out: Path) -> None:
    """Single page: heading + intro paragraph + embedded bar-chart PNG + analysis text.

    Exercises the image pipeline end-to-end:
      - raster PNG embedded via reportlab Image flowable
      - figure node appears in the DocNode tree between text blocks
      - HTML renderer inlines the image as a base64 data URI
    """
    import io
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Image as RLImage
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    s = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=72, rightMargin=72, topMargin=72, bottomMargin=72,
    )

    chart_bytes = _make_chart_png()
    chart_img = RLImage(io.BytesIO(chart_bytes), width=4.5 * inch, height=2.79 * inch)

    story = [
        Paragraph("Quarterly Revenue Report", s["Heading1"]),
        Spacer(1, 10),
        Paragraph(
            "The chart below shows product and service revenue for each quarter of the "
            "current fiscal year. Product revenue (blue) consistently outpaces service "
            "revenue (orange) across all four quarters.",
            s["BodyText"],
        ),
        Spacer(1, 12),
        chart_img,
        Spacer(1, 12),
        Paragraph(
            "Product revenue grew 77.8 % year-over-year, reaching $22.14 M in Q4. "
            "Service revenue expanded 86.2 % over the same period to $5.38 M. "
            "Combined Q4 revenue of $27.52 M represents a 74.0 % increase over Q1.",
            s["BodyText"],
        ),
    ]
    doc.build(story)


def _make_line_chart_png() -> bytes:
    """Generate a deterministic 420x260 two-series monthly line chart (Pillow only)."""
    import io
    from PIL import Image, ImageDraw, ImageFont

    W, H = 420, 260
    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()

    months   = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    series_a = [42, 45, 41, 48, 53, 58, 62, 60, 67, 71, 78, 85]
    series_b = [38, 36, 40, 43, 45, 47, 50, 52, 55, 58, 60, 63]

    BLUE  = (70, 130, 180)
    GREEN = (60, 160, 80)
    BLACK = (0, 0, 0)
    GRAY  = (180, 180, 180)

    ml, mr, mt, mb = 40, 15, 20, 30
    x0, x1, y0, y1 = ml, W - mr, mt, H - mb
    cw, ch = x1 - x0, y1 - y0
    n = len(months)
    max_v = max(max(series_a), max(series_b))

    for frac in (0.25, 0.50, 0.75, 1.0):
        gy = y1 - int(frac * ch)
        draw.line([x0, gy, x1, gy], fill=GRAY, width=1)

    def x_for(i: int) -> int:
        return x0 + i * cw // (n - 1)

    def y_for(v: int) -> int:
        return y1 - int(v / max_v * ch)

    pts_a = [(x_for(i), y_for(v)) for i, v in enumerate(series_a)]
    pts_b = [(x_for(i), y_for(v)) for i, v in enumerate(series_b)]

    for i in range(n - 1):
        draw.line([pts_a[i], pts_a[i + 1]], fill=BLUE, width=2)
        draw.line([pts_b[i], pts_b[i + 1]], fill=GREEN, width=2)

    # Axes
    draw.line([x0, y0, x0, y1], fill=BLACK, width=1)
    draw.line([x0, y1, x1, y1], fill=BLACK, width=1)

    # X labels every 3rd month
    for i, lbl in enumerate(months):
        if i % 3 == 0:
            draw.text((x_for(i) - 6, y1 + 3), lbl, fill=BLACK, font=font)

    # Legend
    lx, ly = x1 - 135, y0
    draw.line([lx, ly + 4, lx + 15, ly + 4], fill=BLUE, width=2)
    draw.text((lx + 18, ly), "Revenue ($M)", fill=BLACK, font=font)
    draw.line([lx, ly + 16, lx + 15, ly + 16], fill=GREEN, width=2)
    draw.text((lx + 18, ly + 12), "Costs ($M)", fill=BLACK, font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def build_13_comprehensive(out: Path) -> None:
    """Omnibus document: every structural use-case in a single multi-page PDF.

    Covers in one document:
      - Heading levels 1, 2, 3
      - Body paragraphs
      - Bullet lists (×2)
      - Two-column balanced layout (BalancedColumns)
      - Simple grid table (TOC + regional summary)
      - Nested table (table inside a cell)
      - Merged cells — colspan spanning all columns + rowspan over two rows
      - Page-spanning table WITH header repeat (repeatRows=1)
      - Page-spanning table WITHOUT header repeat
      - Page-spanning table with nested sub-tables on both pages (no header repeat)
      - Dense financial table (income statement style, small font, section rows)
      - Three embedded raster PNG images (bar chart ×2, line chart ×1)
    """
    import io
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        Image as RLImage, ListFlowable, ListItem,
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak,
    )
    from reportlab.platypus.flowables import BalancedColumns

    s = getSampleStyleSheet()
    doc = SimpleDocTemplate(
        str(out), pagesize=LETTER,
        leftMargin=72, rightMargin=72, topMargin=72, bottomMargin=72,
    )

    def _p(text: str, style: str = "BodyText") -> Paragraph:
        return Paragraph(text, s[style])

    def _sp(n: int = 8) -> Spacer:
        return Spacer(1, n)

    def _img(data: bytes, w: float = 4.5, h: float = 2.79) -> RLImage:
        return RLImage(io.BytesIO(data), width=w * inch, height=h * inch)

    PROSE = (
        "This section provides an in-depth analysis of the operational data "
        "collected during the assessment period. The findings are presented "
        "in structured form to facilitate comparison across regions and time."
    )

    bar_png  = _make_chart_png()
    line_png = _make_line_chart_png()

    # ------------------------------------------------------------------ page 1
    # Title + intro paragraph + TOC as a real GRID table + bar chart

    toc_data = [
        ["Section", "Page"],
        ["1. Executive Summary", "2"],
        ["2. Performance Analysis", "3"],
        ["3. Data Summary", "4"],
        ["4. Transaction Log", "5"],
        ["5. Operations Register", "7"],
        ["6. Project Tracking", "9"],
        ["7. Financial Results", "11"],
        ["8. Conclusions", "12"],
    ]
    toc_table = Table(
        toc_data,
        colWidths=[370, 60],
        style=TableStyle([
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0),  colors.lightgrey),
            ("FONTNAME",   (0, 0), (-1, 0),  "Helvetica-Bold"),
        ]),
    )

    story: list = [
        _p("Technology Assessment Report 2025", "Heading1"),
        _sp(12),
        _p(PROSE),
        _sp(18),
        _p("Table of Contents", "Heading2"),
        _sp(8),
        toc_table,
        _sp(16),
        _img(bar_png),
        _p("Figure 1: Quarterly revenue by product line."),
        PageBreak(),
    ]

    # ------------------------------------------------------------------ page 2
    # H2 + H3 headings, paragraphs, bullet list, two-column layout, simple table

    findings = [
        "Product revenue grew 77.8% year-over-year to $22.14M in Q4.",
        "Service revenue expanded 86.2% over the same period to $5.38M.",
        "Combined operating expenses decreased as a percentage of revenue.",
    ]

    story += [
        _p("1. Executive Summary", "Heading2"),
        _sp(8),
        _p(PROSE),
        _sp(8),
        _p("1.1 Key Findings", "Heading3"),
        _sp(6),
        ListFlowable(
            [ListItem(_p(t)) for t in findings],
            bulletType="bullet",
        ),
        _sp(10),
        _p("1.2 Regional Overview", "Heading3"),
        _sp(6),
        BalancedColumns(
            [
                _p(
                    "North America accounts for the largest share of total revenue, "
                    "driven by strong product adoption and enterprise service contracts. "
                    "Growth in this region is expected to continue at a compound annual "
                    "rate of approximately 18 percent over the next three years."
                ),
                _p(
                    "Europe and Asia-Pacific together represent the fastest-growing "
                    "segments, with combined year-over-year growth exceeding 35 percent. "
                    "Investments in regional distribution and localised product variants "
                    "are the primary drivers of this expansion."
                ),
            ],
            nCols=2,
        ),
        _sp(12),
        _p("1.3 Regional Summary", "Heading3"),
        _sp(6),
        Table(
            [
                ["Region",         "Revenue ($M)", "YoY Growth"],
                ["North America",  "14.2",         "22%"],
                ["Europe",         "8.5",          "31%"],
                ["Asia-Pacific",   "5.6",          "41%"],
                ["Other",          "2.1",          "18%"],
            ],
            colWidths=[180, 120, 120],
            style=TableStyle([
                ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
                ("BACKGROUND", (0, 0), (-1, 0),  colors.lightgrey),
                ("FONTNAME",   (0, 0), (-1, 0),  "Helvetica-Bold"),
            ]),
        ),
        PageBreak(),
    ]

    # ------------------------------------------------------------------ page 3
    # Line chart, nested table, merged-cells table

    def _sub(label: str) -> Table:
        return Table(
            [[f"{label}-X", f"{label}-Y"], ["val-1", "val-2"]],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]),
            colWidths=[50, 50],
        )

    nested_outer = Table(
        [
            ["Component",  "Specifications", "Notes"  ],
            ["CPU",        _sub("cpu"),       "4-core" ],
            ["Memory",     "16 GB",           "DDR5"   ],
            ["Storage",    _sub("disk"),      "NVMe"   ],
        ],
        colWidths=[120, 180, 120],
        style=TableStyle([
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0),  colors.lightgrey),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ]),
    )

    merged_table = Table(
        [
            ["Quarterly Report", "",     ""   ],
            ["Region",           "Q1",   "Q2" ],
            ["North",            "100",  "200"],
            ["",                 "120",  "180"],
            ["South",            "300",  "400"],
        ],
        colWidths=[150, 80, 80],
        style=TableStyle([
            ("SPAN",       (0, 0), (2, 0)),
            ("SPAN",       (0, 2), (0, 3)),
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0),  colors.lightgrey),
            ("BACKGROUND", (0, 1), (-1, 1),  colors.lightgrey),
            ("ALIGN",      (0, 0), (2, 0),   "CENTER"),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ]),
    )

    story += [
        _p("2. Performance Analysis", "Heading2"),
        _sp(8),
        _p("2.1 Revenue Trends", "Heading3"),
        _sp(6),
        _img(line_png),
        _p("Figure 2: Monthly revenue and cost trends."),
        _sp(12),
        _p(PROSE),
        _sp(16),
        _p("3. Data Summary", "Heading2"),
        _sp(8),
        _p("3.1 Hardware Inventory", "Heading3"),
        _sp(6),
        nested_outer,
        _sp(12),
        _p("3.2 Quarterly Performance", "Heading3"),
        _sp(6),
        merged_table,
        PageBreak(),
    ]

    # --------------------------------------------------------------- pages 4-6
    # Page-spanning table WITH header repeat (repeatRows=1)

    span_with_header = Table(
        [["ID", "Description", "Value"]] + [
            [str(i), f"Transaction item {i}", f"${i * 2.75:.2f}"]
            for i in range(1, 51)
        ],
        repeatRows=1,
        colWidths=[60, 300, 80],
        style=TableStyle([
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0),  colors.lightgrey),
        ]),
    )

    story += [
        _p("4. Transaction Log", "Heading2"),
        _sp(8),
        _p("All 50 transactions; header row repeats on each continuation page."),
        _sp(8),
        span_with_header,
        PageBreak(),
    ]

    # --------------------------------------------------------------- pages 6-8
    # Page-spanning table WITHOUT header repeat

    span_no_header = Table(
        [["ID", "Operation", "Cost"]] + [
            [str(i), f"Operation step {i}", f"${i * 1.50:.2f}"]
            for i in range(1, 51)
        ],
        colWidths=[60, 300, 80],
        style=TableStyle([
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0),  colors.lightgrey),
        ]),
    )

    story += [
        _p("5. Operations Register", "Heading2"),
        _sp(8),
        _p("Operations log; header appears on page 1 only (no repeatRows)."),
        _sp(8),
        span_no_header,
        PageBreak(),
    ]

    # --------------------------------------------------------------- pages 8-10
    # Page-spanning table with nested sub-tables on BOTH pages (no header repeat)

    def _nested_sub(label: str) -> Table:
        return Table(
            [[f"{label}-A", f"{label}-B"], ["1", "2"]],
            style=TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]),
            colWidths=[50, 50],
        )

    project_rows: list = [["Step", "Inputs", "Notes"]]
    for i in range(1, 51):
        if i == 5:
            project_rows.append([str(i), _nested_sub("p1"), "nested on pg1"])
        elif i == 45:
            project_rows.append([str(i), _nested_sub("p2"), "nested on pg2"])
        else:
            project_rows.append([str(i), f"input {i}", f"note {i}"])

    project_table = Table(
        project_rows,
        colWidths=[70, 280, 150],
        style=TableStyle([
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (-1, 0),  colors.lightgrey),
            ("VALIGN",     (0, 0), (-1, -1), "MIDDLE"),
        ]),
    )

    story += [
        _p("6. Project Tracking", "Heading2"),
        _sp(8),
        _p("Project log with nested input tables on both continuation pages."),
        _sp(8),
        project_table,
        PageBreak(),
    ]

    # -------------------------------------------------------------- page 10-11
    # Dense financial table: income statement style (small font, section rows)

    pl_tagged = [
        # type, label,                          FY21,       FY22,       FY23,       FY24
        ("H", "Income Statement",               "FY2021",   "FY2022",   "FY2023",   "FY2024"),
        ("S", "Revenue",                        "",         "",         "",         ""),
        ("I", "  Product Revenue",              "12,450",   "15,320",   "18,760",   "22,140"),
        ("I", "  Service Revenue",              "2,890",    "3,450",    "4,120",    "5,380"),
        ("I", "  Other Revenue",                "480",      "620",      "710",      "850"),
        ("T", "Total Revenue",                  "15,820",   "19,390",   "23,590",   "28,370"),
        ("S", "Cost of Revenue",                "",         "",         "",         ""),
        ("I", "  Cost of Goods Sold",           "(8,240)",  "(9,950)",  "(11,820)", "(13,760)"),
        ("I", "  Depreciation & Amort.",        "(320)",    "(380)",    "(430)",    "(480)"),
        ("T", "Total Cost of Revenue",          "(8,560)",  "(10,330)", "(12,250)", "(14,240)"),
        ("K", "Gross Profit",                   "7,260",    "9,060",    "11,340",   "14,130"),
        ("P", "  Gross Margin %",               "45.9%",    "46.7%",    "48.1%",    "49.8%"),
        ("S", "Operating Expenses",             "",         "",         "",         ""),
        ("I", "  Research & Development",       "(2,140)",  "(2,660)",  "(3,180)",  "(3,720)"),
        ("I", "  Sales & Marketing",            "(3,280)",  "(3,950)",  "(4,840)",  "(5,600)"),
        ("I", "  General & Administrative",     "(1,120)",  "(1,280)",  "(1,460)",  "(1,640)"),
        ("T", "Total Operating Expenses",       "(6,540)",  "(7,890)",  "(9,480)",  "(10,960)"),
        ("K", "Operating Income (EBIT)",        "720",      "1,170",    "1,860",    "3,170"),
        ("P", "  EBIT Margin %",                "4.6%",     "6.0%",     "7.9%",     "11.2%"),
        ("S", "Other Items",                    "",         "",         "",         ""),
        ("I", "  Interest Income",              "85",       "110",      "190",      "340"),
        ("I", "  Interest Expense",             "(420)",    "(390)",    "(350)",    "(290)"),
        ("T", "Pre-Tax Income",                 "385",      "890",      "1,700",    "3,220"),
        ("I", "  Income Tax Provision",         "(78)",     "(178)",    "(340)",    "(644)"),
        ("K", "Net Income",                     "307",      "712",      "1,360",    "2,576"),
        ("P", "  Net Margin %",                 "1.9%",     "3.7%",     "5.8%",     "9.1%"),
        ("S", "Per Share Data",                 "",         "",         "",         ""),
        ("I", "  Basic EPS",                    "$0.31",    "$0.71",    "$1.36",    "$2.59"),
        ("I", "  Diluted EPS",                  "$0.30",    "$0.69",    "$1.32",    "$2.51"),
    ]

    _GH = colors.HexColor("#D0D0D0")
    _GS = colors.HexColor("#E8E8E8")
    _GT = colors.HexColor("#F0F0F0")
    _GK = colors.HexColor("#DDEEFF")

    pl_cmd: list = [
        ("FONTNAME",      (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE",      (0, 0), (-1, -1), 7.5),
        ("LEADING",       (0, 0), (-1, -1), 9),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#AAAAAA")),
        ("ALIGN",         (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN",         (0, 0), (0, -1),  "LEFT"),
    ]
    for i, (t, *_) in enumerate(pl_tagged):
        if t == "H":
            pl_cmd += [("BACKGROUND", (0, i), (-1, i), _GH),
                       ("FONTNAME",   (0, i), (-1, i), "Helvetica-Bold"),
                       ("ALIGN",      (0, i), (-1, i), "CENTER")]
        elif t == "S":
            pl_cmd += [("BACKGROUND", (0, i), (-1, i), _GS),
                       ("FONTNAME",   (0, i), (-1, i), "Helvetica-Bold"),
                       ("FONTSIZE",   (0, i), (-1, i), 7.0)]
        elif t == "T":
            pl_cmd += [("BACKGROUND", (0, i), (-1, i), _GT),
                       ("FONTNAME",   (0, i), (-1, i), "Helvetica-Bold"),
                       ("LINEABOVE",  (0, i), (-1, i), 0.5, colors.black)]
        elif t == "K":
            pl_cmd += [("BACKGROUND", (0, i), (-1, i), _GK),
                       ("FONTNAME",   (0, i), (-1, i), "Helvetica-Bold"),
                       ("LINEABOVE",  (0, i), (-1, i), 0.8, colors.black),
                       ("LINEBELOW",  (0, i), (-1, i), 0.8, colors.black)]
        elif t == "P":
            pl_cmd += [("FONTNAME",   (0, i), (-1, i), "Helvetica-Oblique"),
                       ("TEXTCOLOR",  (0, i), (-1, i), colors.HexColor("#444444"))]

    pl_table = Table(
        [[r[1], r[2], r[3], r[4], r[5]] for r in pl_tagged],
        colWidths=[195, 63, 63, 63, 63],
        style=TableStyle(pl_cmd),
        repeatRows=1,
    )

    story += [
        _p("7. Financial Results", "Heading2"),
        _sp(8),
        _p("Consolidated income statement ($000s unless noted)."),
        _sp(6),
        pl_table,
        PageBreak(),
    ]

    # ----------------------------------------------------------------- page 12
    # Closing section: prose + bullet list + third image

    conclusions = [
        "Revenue growth of 79.6% over four fiscal years demonstrates sustained demand.",
        "Operating leverage improved as fixed costs spread over a larger revenue base.",
        "Continued R&D investment is expected to drive further margin expansion.",
        "Regional diversification reduces concentration risk and opens new corridors.",
    ]

    story += [
        _p("8. Conclusions", "Heading2"),
        _sp(8),
        _p(PROSE),
        _sp(8),
        ListFlowable(
            [ListItem(_p(t)) for t in conclusions],
            bulletType="bullet",
        ),
        _sp(16),
        _img(bar_png),
        _p("Figure 3: Full-year revenue summary."),
    ]

    doc.build(story)

def build_14_borderless_table(out: Path) -> None:
    """14_borderless_table: 4-row x 3-col table with no vector-line borders.

    The line detection strategy finds no tables on this page; only the text
    fallback can reconstruct the grid from word positions.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    data = [
        ["Name",  "Score", "Grade"],
        ["Alice", "95",    "A"],
        ["Bob",   "82",    "B"],
        ["Carol", "91",    "A-"],
    ]
    t = Table(data, colWidths=[120, 80, 80])
    t.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # Deliberately no GRID / BOX / LINEBELOW — no vector borders.
    ]))
    story = [
        Paragraph("Borderless Table Example", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)

def build_15_multicolumn_text(out: Path) -> None:
    """15_multicolumn_text: two-column body text with no tables.

    The text strategy must NOT misidentify this layout as a table.
    Used as a negative fixture for the text-strategy fallback guard.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    body = (
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
        "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris."
    )
    paragraphs = [Paragraph(body, s["BodyText"]) for _ in range(6)]
    story = [
        Paragraph("Multi-Column Text Layout", s["Heading1"]),
        Spacer(1, 12),
        BalancedColumns(paragraphs, nCols=2, needed=72),
    ]
    doc.build(story)


def build_16_text_between_subtables(out: Path) -> None:
    """16_text_between_subtables: outer table whose middle cell contains two
    sub-tables with a plain-text paragraph between them.

    Fixture structure:
      Outer table (1 column, GRID borders):
        Row 0: "Section Header"              ← plain-text header cell
        Row 1: [sub-table A, paragraph, sub-table B]  ← cell with 2 nested tables + text
        Row 2: "Section Footer"              ← plain-text footer cell

    This fixture tests that text situated between nested sub-tables inside a
    single outer cell is preserved in the parsed output.  Before the fix, the
    cell's ``text`` attribute was set to ``None`` when any ``children`` were
    present, silently dropping the paragraph.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()

    def _sub(header: list[str], rows: list[list[str]]) -> Table:
        return Table(
            [header] + rows,
            style=TableStyle([
                ("GRID",       (0, 0), (-1, -1), 0.5, colors.darkblue),
                ("BACKGROUND", (0, 0), (-1, 0),  colors.lightblue),
                ("FONTNAME",   (0, 0), (-1, 0),  "Helvetica-Bold"),
                ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ]),
            colWidths=[90, 90],
        )

    sub_a = _sub(["Item", "Qty"], [["Widget A", "10"], ["Widget B", "5"]])
    sub_b = _sub(["Month", "Sales"], [["Jan", "$500"], ["Feb", "$700"]])

    # List of flowables in one cell: sub-table, paragraph, sub-table.
    between_para = Paragraph(
        "NOTE: This paragraph sits between the two sub-tables and must be preserved.",
        s["BodyText"],
    )
    cell_content = [sub_a, between_para, sub_b]

    outer = Table(
        [
            ["Section Header"],
            [cell_content],
            ["Section Footer"],
        ],
        style=TableStyle([
            ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND", (0, 0), (0, 0),  colors.lightgrey),
            ("FONTNAME",   (0, 0), (0, 0),  "Helvetica-Bold"),
            ("VALIGN",     (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]),
        colWidths=[300],
    )
    story = [
        Paragraph("Text Between Sub-Tables", s["Heading1"]),
        Spacer(1, 12),
        outer,
    ]
    doc.build(story)


def build_17_subtable_spanning_pages(out: Path) -> None:
    """17_subtable_spanning_pages: outer table whose data row genuinely spans
    a page break, containing an inner sub-table split across the same break.

    Constructed with the reportlab canvas API (not the Table layout engine) so
    that the outer row can extend to the physical page edge — y=0 in PDF
    coordinates (= pdfplumber y=page_height) on page 1, and y=page_height on
    page 2 (= pdfplumber y=0).  This satisfies the SPLIT_ROW_EDGE_FRAC
    threshold (3%) that distinguishes genuine split rows from normal row
    boundaries within a multi-page table.

    Layout (all coordinates in PDF points, y=0=bottom):
      Page 1:
        Outer header row : y 760..780
        Outer data row   : y 0..760  (extends to physical bottom)
        Inner sub-table  : x 77..377, y 0..756, 25 rows × 15 pt
      Page 2:
        Outer data row   : y 432..792  (starts at physical top)
        Inner sub-table  : x 77..377, y 432..792, 10 rows × 15 pt
        Closing line     : y 432

    Expected after parsing + split-row fix:
      - 2 tables pre-stitch (one per page); 1 table post-stitch.
      - Post-stitch: 2 rows (header + 1 merged data row).
      - Merged data row's cell has one stitched sub-table with spans_pages set.
      - Stitched sub-table has >= 30 data rows.
    """
    from reportlab.pdfgen import canvas as rl_canvas
    W, H = LETTER   # 612, 792

    c = rl_canvas.Canvas(str(out), pagesize=LETTER)
    x0, x1 = 72.0, 540.0       # outer table left/right
    ix0, ix1, ix_sep = 77.0, 377.0, 277.0  # inner table left, right, col-sep
    ROW_H = 15.0                # inner table row height

    # ── PAGE 1 ──────────────────────────────────────────────────────────────
    hdr_top, hdr_bot = 780.0, 760.0
    # Outer header row
    c.setFont("Helvetica-Bold", 11)
    c.rect(x0, hdr_bot, x1 - x0, hdr_top - hdr_bot, stroke=1, fill=0)
    c.drawString(x0 + 5, hdr_bot + 4, "Inventory Listing")

    # Outer data row: reaches physical bottom (y=0)
    c.line(x0, 0.0, x0, hdr_bot)   # left wall
    c.line(x1, 0.0, x1, hdr_bot)   # right wall

    # Inner sub-table on page 1: 25 data rows
    N1 = 25
    inner_top_p1 = hdr_bot - 4   # just inside the data row cell
    c.setFont("Helvetica-Bold", 9)
    c.line(ix0, 0.0, ix0, inner_top_p1)
    c.line(ix_sep, 0.0, ix_sep, inner_top_p1)
    c.line(ix1, 0.0, ix1, inner_top_p1)
    # Header row of inner table
    c.drawString(ix0 + 3, inner_top_p1 - 11, "Item")
    c.drawString(ix_sep + 3, inner_top_p1 - 11, "Value")
    c.line(ix0, inner_top_p1, ix1, inner_top_p1)  # top of inner table
    c.line(ix0, inner_top_p1 - ROW_H, ix1, inner_top_p1 - ROW_H)  # below header
    c.setFont("Helvetica", 9)
    for i in range(N1):
        y_top = inner_top_p1 - (i + 1) * ROW_H
        y_bot = y_top - ROW_H
        c.line(ix0, y_bot, ix1, y_bot)
        c.drawString(ix0 + 3, y_bot + 3, f"Item {i + 1:02d}")
        c.drawString(ix_sep + 3, y_bot + 3, str((i + 1) * 7))

    c.showPage()

    # ── PAGE 2 ──────────────────────────────────────────────────────────────
    N2 = 10
    inner_top_p2 = H   # starts at physical top (pdfplumber y=0)
    data_bot_p2  = inner_top_p2 - (N2 + 1) * ROW_H  # closing y for the row

    # Outer data row continuation
    c.line(x0, data_bot_p2, x0, H)
    c.line(x1, data_bot_p2, x1, H)
    c.line(x0, data_bot_p2, x1, data_bot_p2)  # closing line

    # Inner sub-table on page 2
    c.setFont("Helvetica", 9)
    c.line(ix0, data_bot_p2, ix0, inner_top_p2)
    c.line(ix_sep, data_bot_p2, ix_sep, inner_top_p2)
    c.line(ix1, data_bot_p2, ix1, inner_top_p2)
    c.line(ix0, inner_top_p2 - ROW_H, ix1, inner_top_p2 - ROW_H)  # below fake header
    for i in range(N2):
        y_top = inner_top_p2 - (i + 1) * ROW_H
        y_bot = y_top - ROW_H
        c.line(ix0, y_bot, ix1, y_bot)
        row_num = N1 + i + 1
        c.drawString(ix0 + 3, y_bot + 3, f"Item {row_num:02d}")
        c.drawString(ix_sep + 3, y_bot + 3, str(row_num * 7))

    c.save()


def build_18_text_and_subtables_spanning_pages(out: Path) -> None:
    """18_text_and_subtables_spanning_pages: outer table whose data row spans a
    page break; the cell contains sub-table A, a paragraph, and sub-table B.

    Same canvas approach as fixture 17.  Sub-tables A and B use different
    column structures (Item/Qty vs Month/Revenue) so the stitcher cannot
    accidentally merge them.

    Layout (PDF coords, y=0=bottom):
      Page 1:
        Outer header : y 760..780
        Outer data   : y 0..760
        Sub-table A  : x 77..377, y 280..756, 20 rows × ~22 pt
        Paragraph    : x 77, y 250..276
      Page 2:
        Outer data   : y 270..792
        Sub-table B  : x 77..377, y 270..786, 20 rows × ~22 pt
        Closing line : y 270

    Expected after parsing + split-row fix:
      - 2 tables pre-stitch; 1 table post-stitch.
      - Post-stitch: 2 rows (header + 1 merged data row).
      - Merged cell children: [sub-table A, paragraph(s), sub-table B] in order.
      - "NOTE:" present in paragraph text.
      - Sub-table A and B are distinct (different column anchors).
    """
    from reportlab.pdfgen import canvas as rl_canvas
    W, H = LETTER

    c = rl_canvas.Canvas(str(out), pagesize=LETTER)
    x0, x1 = 72.0, 540.0
    ROW_H = 22.0

    def _draw_subtable(c, n_rows, tag, cols, ix0, ix1, ix_sep, top_y, bot_y, start_row=1):
        """Draw an inner sub-table with n_rows data rows between top_y and bot_y."""
        c.line(ix0, bot_y, ix0, top_y)
        c.line(ix_sep, bot_y, ix_sep, top_y)
        c.line(ix1, bot_y, ix1, top_y)
        c.setFont("Helvetica-Bold", 9)
        c.drawString(ix0 + 3, top_y - 14, cols[0])
        c.drawString(ix_sep + 3, top_y - 14, cols[1])
        c.line(ix0, top_y, ix1, top_y)
        c.line(ix0, top_y - ROW_H, ix1, top_y - ROW_H)
        c.setFont("Helvetica", 9)
        for i in range(n_rows):
            y_top = top_y - (i + 1) * ROW_H
            y_bot = y_top - ROW_H
            if y_bot < bot_y - 1:
                break
            c.line(ix0, y_bot, ix1, y_bot)
            c.drawString(ix0 + 3, y_bot + 4, f"{tag}-row-{start_row + i}")
            c.drawString(ix_sep + 3, y_bot + 4, str((start_row + i) * 3))

    ix0, ix1, ix_sep = 77.0, 377.0, 277.0
    N = 20

    # ── PAGE 1 ──────────────────────────────────────────────────────────────
    hdr_top, hdr_bot = 780.0, 760.0
    c.setFont("Helvetica-Bold", 11)
    c.rect(x0, hdr_bot, x1 - x0, hdr_top - hdr_bot, stroke=1, fill=0)
    c.drawString(x0 + 5, hdr_bot + 4, "Combined Report")

    # Outer data row walls
    c.line(x0, 0.0, x0, hdr_bot)
    c.line(x1, 0.0, x1, hdr_bot)

    # Sub-table A: 20 rows, top just inside data cell, bottom at y=280
    sub_a_top = hdr_bot - 4
    sub_a_bot = 280.0
    _draw_subtable(c, N, "A", ["Item", "Qty"], ix0, ix1, ix_sep, sub_a_top, sub_a_bot)

    # Paragraph text below sub-table A (still on page 1, near page bottom)
    c.setFont("Helvetica", 10)
    c.drawString(ix0, 260.0, "NOTE: paragraph between sub-table A and sub-table B.")

    c.showPage()

    # ── PAGE 2 ──────────────────────────────────────────────────────────────
    # Sub-table B: starts at physical page top (y=792), 20 rows
    sub_b_top = H
    sub_b_bot = sub_b_top - (N + 1) * ROW_H
    data_bot_p2 = sub_b_bot - 4

    # Outer data row continuation
    c.line(x0, data_bot_p2, x0, H)
    c.line(x1, data_bot_p2, x1, H)
    c.line(x0, data_bot_p2, x1, data_bot_p2)

    _draw_subtable(c, N, "B", ["Month", "Revenue"], ix0, ix1, ix_sep, sub_b_top, sub_b_bot)

    c.save()

BUILDERS = {
    "01_simple_table": build_01_simple_table,
    "02_nested_table": build_02_nested_table,
    "03_page_spanning": build_03_page_spanning,
    "04_multi_column": build_04_multi_column,
    "05_sections_lists": build_05_sections_lists,
    "06_page_spanning_no_header_repeat": build_06_page_spanning_no_header_repeat,
    "07_page_spanning_with_nested": build_07_page_spanning_with_nested,
    "08_page_spanning_subtable_split": build_08_page_spanning_subtable_split,
    "09_mixed_toc_and_spanning_table": build_09_mixed_toc_and_spanning_table,
    "10_merged_cells": build_10_merged_cells,
    "11_pl_statement": build_11_pl_statement,
    "12_image_chart":   build_12_image_chart,
    "13_comprehensive": build_13_comprehensive,
    "14_borderless_table": build_14_borderless_table,
    "15_multicolumn_text": build_15_multicolumn_text,
    "16_text_between_subtables": build_16_text_between_subtables,
    "17_subtable_spanning_pages": build_17_subtable_spanning_pages,
    "18_text_and_subtables_spanning_pages": build_18_text_and_subtables_spanning_pages,
}






def build_all() -> None:
    for name, builder in BUILDERS.items():
        out_dir = GOLDEN_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        builder(out_dir / "source.pdf")


if __name__ == "__main__":
    build_all()
