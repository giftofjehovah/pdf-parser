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
      - Annex A: ruled-header tables (open-body / framed-body / row-strips)
      - Annex B: fully borderless table (no vector lines anywhere)
      - Annex C: outer table with text between two nested sub-tables
      - Annex D: same idiom as C, tall enough to span a page break
      - Annex E: vertically merged column drawn with white "invisible" rules
    """
    import io
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
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

    # ----------------------------------------------------------------- annex A
    # Border-style variants on a single annex page.  Each table exercises a
    # different combination of ruled header vs unruled body that was carved
    # out as fixtures 18 / 19 / 20.

    annex_a_open_body = Table(
        [["Name",  "Score", "Grade"],
         ["Alice", "95",    "A"],
         ["Bob",   "82",    "B-"],
         ["Carol", "91",    "A-"],
         ["Dave",  "76",    "C+"]],
        colWidths=[120, 80, 80],
        style=TableStyle([
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 10),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            # Header-only frame: no internal verticals or rules in the body.
            ("GRID",          (0, 0), (-1, 0),  0.5, colors.black),
        ]),
    )

    annex_a_framed_body = Table(
        [["Region",  "Q1",  "Q2",  "Q3",  "Q4"],
         ["North",   "120", "135", "150", "162"],
         ["South",   "98",  "104", "111", "120"],
         ["East",    "87",  "92",  "101", "118"],
         ["West",    "143", "149", "156", "171"]],
        colWidths=[100, 60, 60, 60, 60],
        style=TableStyle([
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 10),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("BOX",           (0, 0), (-1, -1), 0.5, colors.black),
            ("GRID",          (0, 0), (-1, 0),  0.5, colors.black),
        ]),
    )

    annex_a_row_strips = Table(
        [["Item",   "Qty", "Price"],
         ["Apple",  "3",   "$1.00"],
         ["Banana", "6",   "$0.50"],
         ["Cherry", "12",  "$2.25"],
         ["Date",   "4",   "$3.10"]],
        colWidths=[120, 80, 80],
        style=TableStyle([
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 10),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("BOX",           (0, 0), (-1, -1), 0.5, colors.black),
            ("GRID",          (0, 0), (-1, 0),  0.5, colors.black),
            ("LINEBELOW",     (0, 1), (-1, -2), 0.5, colors.black),
        ]),
    )

    story += [
        PageBreak(),
        _p("9. Border Style Variants", "Heading2"),
        _sp(8),
        _p("9.1 Open-Body Table", "Heading3"),
        _sp(6),
        annex_a_open_body,
        _sp(12),
        _p("9.2 Framed-Body Table", "Heading3"),
        _sp(6),
        annex_a_framed_body,
        _sp(12),
        _p("9.3 Row-Strips Table", "Heading3"),
        _sp(6),
        annex_a_row_strips,
        PageBreak(),
    ]

    # ----------------------------------------------------------------- annex B
    # Fully borderless 4-row × 3-col table (fixture 14): only the text-strategy
    # fallback can recover the grid.

    annex_b_borderless = Table(
        [["Student", "Average", "Standing"],
         ["Ellie",   "94",      "A"],
         ["Finn",    "81",      "B"],
         ["Gwen",    "88",      "B+"]],
        colWidths=[120, 80, 80],
        style=TableStyle([
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 10),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            # Intentionally no GRID / BOX / LINE — no vector borders.
        ]),
    )

    # No intro paragraph: the text-strategy fallback rejects pseudo-tables
    # whose average cell-text length exceeds _MAX_CELL_TEXT_CHARS, so any prose
    # near the table merges into the same region and disqualifies the whole
    # block.  Heading + spacer + table mirrors the standalone fixture 14.
    story += [
        _p("10. Borderless Summary", "Heading2"),
        _sp(12),
        annex_b_borderless,
        PageBreak(),
    ]

    # ----------------------------------------------------------------- annex C
    # Outer table whose middle cell holds two sub-tables with a paragraph
    # between them (fixture 16).  Header signatures are unique so the test
    # suite can address them without colliding with annex D below.

    def _annex_sub(header: list[str], rows: list[list[str]]) -> Table:
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

    annex_c_sub_a = _annex_sub(["Part", "Count"],   [["Bolt", "12"], ["Screw", "30"]])
    annex_c_sub_b = _annex_sub(["Quarter", "Revenue"], [["Q1", "$1,200"], ["Q2", "$1,650"]])
    annex_c_between = _p(
        "NOTE: This paragraph sits between the two sub-tables and must be preserved."
    )

    annex_c_outer = Table(
        [
            ["Annex C Header"],
            [[annex_c_sub_a, annex_c_between, annex_c_sub_b]],
            ["Annex C Footer"],
        ],
        style=TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND",    (0, 0), (0, 0),   colors.lightgrey),
            ("FONTNAME",      (0, 0), (0, 0),   "Helvetica-Bold"),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]),
        colWidths=[300],
    )

    story += [
        _p("11. Notes Between Sub-Tables", "Heading2"),
        _sp(8),
        annex_c_outer,
        PageBreak(),
    ]

    # ----------------------------------------------------------------- annex D
    # Same idiom as annex C but tall enough to span a page break (fixture 17).
    # Each between-paragraph is its own outer-table row so the split happens
    # at a clean row boundary; the outer table draws horizontal lines only
    # around header and footer, so the table reads as one continuous frame
    # bridging the two pages.

    annex_d_sub_a = _annex_sub(["Code", "Total"], [["AX-1", "100"], ["AX-2", "250"]])
    annex_d_sub_b = _annex_sub(["Phase", "Status"], [["Init", "Done"], ["Build", "WIP"]])

    # Local style — do NOT mutate the shared BodyText (would leak elsewhere).
    annex_d_body = ParagraphStyle("AnnexDBody", parent=s["BodyText"], fontSize=9, leading=12)

    annex_d_paras: list = [
        Paragraph(
            "NOTE: This paragraph sits between the two sub-tables and must "
            "be preserved across the page break.",
            annex_d_body,
        ),
    ]
    for i in range(1, 28):
        annex_d_paras.append(Paragraph(
            f"Spanning-line {i:02d}: Lorem ipsum dolor sit amet, "
            "consectetur adipiscing elit, sed do eiusmod tempor "
            "incididunt ut labore et dolore magna aliqua.",
            annex_d_body,
        ))
    annex_d_paras.append(Paragraph(
        "END: This trailing sentence is the last paragraph between the two "
        "sub-tables.",
        annex_d_body,
    ))

    annex_d_rows: list[list] = [
        ["Spanning Header"],
        [[annex_d_sub_a]],
        *[[para] for para in annex_d_paras],
        [[annex_d_sub_b]],
        ["Spanning Footer"],
    ]
    _AD_HEADER = 0
    _AD_FOOTER = len(annex_d_rows) - 1

    annex_d_outer = Table(
        annex_d_rows,
        style=TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND",    (0, _AD_HEADER), (0, _AD_HEADER), colors.lightgrey),
            ("FONTNAME",      (0, _AD_HEADER), (0, _AD_HEADER), "Helvetica-Bold"),
            # Vertical sides span every row so the frame reads continuous
            # across the page boundary.
            ("LINEBEFORE", (0, 0),  (0, -1),  0.5, colors.black),
            ("LINEAFTER",  (-1, 0), (-1, -1), 0.5, colors.black),
            # Horizontal rules ONLY around header and footer.
            ("LINEABOVE", (0, _AD_HEADER), (-1, _AD_HEADER), 0.5, colors.black),
            ("LINEBELOW", (0, _AD_HEADER), (-1, _AD_HEADER), 0.5, colors.black),
            ("LINEABOVE", (0, _AD_FOOTER), (-1, _AD_FOOTER), 0.5, colors.black),
            ("LINEBELOW", (0, _AD_FOOTER), (-1, _AD_FOOTER), 0.5, colors.black),
        ]),
        colWidths=[400],
    )

    story += [
        _p("12. Extended Notes (Page Spanning)", "Heading2"),
        _sp(8),
        annex_d_outer,
        PageBreak(),
    ]

    # ----------------------------------------------------------------- annex E
    # Vertically merged column drawn with white "invisible" row separators
    # (fixture 21).  Col-0 separators at rows 1/2 and 2/3 are overdrawn with
    # white; a colour-aware parser must subtract those overdraws so col-0
    # collapses into one cell spanning rows 1..3.

    annex_e_table = Table(
        [["Zone",        "Jan",  "Feb",  "Mar"],
         ["Tropical",    "100",  "110",  "120"],
         ["Subtropical", "200",  "210",  "220"],
         ["Temperate",   "300",  "310",  "320"],
         ["Polar",       "400",  "410",  "420"]],
        colWidths=[100, 60, 60, 60],
        rowHeights=[20, 20, 20, 20, 20],
        style=TableStyle([
            ("FONTNAME",  (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",  (0, 0), (-1, -1), 10),
            ("VALIGN",    (0, 0), (-1, -1), "TOP"),
            ("GRID",      (0, 0), (-1, -1), 0.5, colors.black),
            ("LINEBELOW", (0, 1), (0, 1),   0.5, colors.white),
            ("LINEBELOW", (0, 2), (0, 2),   0.5, colors.white),
        ]),
    )

    story += [
        _p("13. Vertical Merge", "Heading2"),
        _sp(8),
        _p(
            "Inner row separators in column 0 are overdrawn with white; a "
            "reader sees one merged cell containing three lines of text."
        ),
        _sp(8),
        annex_e_table,
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

def build_14b_borderless_long_text(out: Path) -> None:
    """14b_borderless_long_text: borderless table whose avg cell length exceeds
    the legacy ``_MAX_CELL_TEXT_CHARS = 7`` heuristic.

    Mirrors the shape of 14_borderless_table (no GRID/BOX) but with descriptive
    headers and long-text status columns ("Annual subscription renewal" /
    "Awaiting reply"). The legacy text-strategy fallback rejects this shape on
    avg-cell-length; the experimental column-anchor detector recovers it.

    Used by ``tests/stages/test_detect_tables_anchor.py`` and by the default
    golden suite (anchor detector enabled by default since the flip).
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    data = [
        ["Customer Name",      "Order Description",            "Status Notes"],
        ["Acme Corporation",   "Annual subscription renewal",  "Paid in Q3"],
        ["Globex Industries",  "Hardware shipment delayed",    "Pending review"],
        ["Initech Holdings",   "Consulting engagement closed", "Invoice sent"],
        ["Umbrella Logistics", "Routine maintenance contract", "Awaiting reply"],
    ]
    t = Table(data, colWidths=[150, 200, 120])
    t.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # No GRID / BOX / LINEBELOW — fully borderless.
    ]))
    story = [
        Paragraph("Long-Cell Borderless Table", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)

def build_14c_borderless_long_text_spanning(out: Path) -> None:
    """14c_borderless_long_text_spanning: 14b's shape, scaled to span two pages.

    Same column structure as 14b (Customer / Order / Status) so the anchor
    detector produces matching column signatures on both pages.  ``repeatRows=1``
    re-emits the header on page 2, which the stitcher dedupes via header
    signature.  Validates that anchor candidates on consecutive pages get
    merged into a single ``DocNode(kind="table")`` with ``bbox: list[BBox]``.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
    s = _styles()
    header = ["Customer Name", "Order Description", "Status Notes"]
    # 50 rows is enough to overflow one LETTER page at 10pt + borderless padding.
    # Cell content uses hyphenated single tokens (no internal whitespace) so
    # pdfplumber's text-strategy fallback cannot fragment cells along word
    # boundaries (which would push the average cell length below the legacy
    # ``_MAX_CELL_TEXT_CHARS = 7`` cutoff and produce a wrong-shape table).
    # The anchor detector recovers the true 3-column structure.
    rows = [
        [
            f"Customer-{i:02d}-Inc",
            f"Order-{i:02d}-pending-review",
            f"Status-{i:02d}-followup",
        ]
        for i in range(1, 51)
    ]
    t = Table([header] + rows, colWidths=[150, 220, 110], repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # No GRID / BOX / LINEBELOW — fully borderless.
    ]))
    story = [
        Paragraph("Long-Cell Borderless Table (Spanning)", s["Heading1"]),
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


def build_17_text_between_subtables_spanning(out: Path) -> None:
    """17_text_between_subtables_spanning: page-spanning variant of fixture 16.

    Same idiom as 16 (an outer table whose middle region holds two sub-tables
    with text between them), but the outer table is tall enough that the
    paragraphs between sub-table A and sub-table B straddle the page break.

    Fixture structure (1-column outer table, partial borders):
      Row 0:       "Section Header"   ← plain-text header cell, page 1
      Row 1:       [ sub-table A ]    ← page 1
      Rows 2..N+1: one Paragraph each ← splits cleanly between any two of
                                       these rows at the page boundary
      Row N+2:     [ sub-table B ]    ← page 2
      Row N+3:     "Section Footer"   ← plain-text footer cell, page 2

    Border choices and the rationale:
      - Each between-paragraph is its own outer-table row so the table can
        split at a clean row boundary (ReportLab's default ``splitByRow=1``).
        That preserves the cell's TOPPADDING / BOTTOMPADDING on the rows
        adjacent to the page break — text does not crash into the page edge.
      - The outer table draws only the borders we *want* visible:
        LINEABOVE+LINEBELOW around the Header and Footer rows, plus
        LINEBEFORE+LINEAFTER down the left and right sides of every row.
        There is NO horizontal line around sub-table A's row, the
        paragraph rows, or sub-table B's row.  When the table splits, no
        closing border is drawn beneath the page-1 fragment and no opening
        border is drawn above the page-2 fragment, so the outer table reads
        visually as a single continuous box that bridges the two pages.
      - The sub-tables still live in their own outer rows (not in a single
        cell with the paragraphs) because ReportLab cannot split a cell
        whose flowable list contains nested Tables — :meth:`Table._splitCell`
        crashes on ``Table.height`` lookup.

    The fixture exercises:
      - a multi-page outer table with nested sub-tables on both pages;
      - between-paragraph text that lives inside the outer-table column on
        both pages and respects the cell padding at the page seam;
      - the parser's stitcher (the page-1 outer fragment now extends to the
        bottom of the page, so the bottom-margin heuristic does not bail).
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
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

    # Numbered, deterministic filler.  NOTE / END bookend the block so tests
    # can pin the start and end of the "between" region.
    body = s["BodyText"]
    body.fontSize = 9
    body.leading = 12
    between_paras: list = [
        Paragraph(
            "NOTE: This paragraph sits between the two sub-tables and must "
            "be preserved across the page break.",
            body,
        ),
    ]
    for i in range(1, 28):
        between_paras.append(Paragraph(
            f"Between-line {i:02d}: Lorem ipsum dolor sit amet, "
            "consectetur adipiscing elit, sed do eiusmod tempor "
            "incididunt ut labore et dolore magna aliqua.",
            body,
        ))
    between_paras.append(Paragraph(
        "END: This trailing sentence is the last paragraph between the two "
        "sub-tables.",
        body,
    ))

    # Row layout: header, sub_a, one row per between-paragraph, sub_b, footer.
    data: list[list] = [
        ["Section Header"],
        [[sub_a]],
        *[[para] for para in between_paras],
        [[sub_b]],
        ["Section Footer"],
    ]
    HEADER_ROW = 0
    FOOTER_ROW = len(data) - 1

    outer = Table(
        data,
        style=TableStyle([
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("BACKGROUND",    (0, HEADER_ROW), (0, HEADER_ROW), colors.lightgrey),
            ("FONTNAME",      (0, HEADER_ROW), (0, HEADER_ROW), "Helvetica-Bold"),
            # Left and right edges run down every row.  When the table
            # splits, these vertical lines extend to the page boundary on
            # both halves, making the outer table read as continuous.
            ("LINEBEFORE", (0, 0),  (0, -1),  0.5, colors.black),
            ("LINEAFTER",  (-1, 0), (-1, -1), 0.5, colors.black),
            # Horizontal lines ONLY around header and footer — no border
            # encloses sub-table A, the paragraph rows, or sub-table B.
            ("LINEABOVE", (0, HEADER_ROW), (-1, HEADER_ROW), 0.5, colors.black),
            ("LINEBELOW", (0, HEADER_ROW), (-1, HEADER_ROW), 0.5, colors.black),
            ("LINEABOVE", (0, FOOTER_ROW), (-1, FOOTER_ROW), 0.5, colors.black),
            ("LINEBELOW", (0, FOOTER_ROW), (-1, FOOTER_ROW), 0.5, colors.black),
        ]),
        colWidths=[400],
    )
    story = [
        Paragraph("Text Between Sub-Tables (Page Spanning)", s["Heading1"]),
        Spacer(1, 12),
        outer,
    ]
    doc.build(story)


def build_18_ruled_header_open_body(out: Path) -> None:
    """18_ruled_header_open_body: header has cell borders + a rule under it.

    Body rows have *no* borders at all — neither vertical separators between
    columns nor horizontal rules between rows.  Text in body rows is positioned
    by ``colWidths`` so it visually aligns under the header columns, but
    pdfplumber's line strategy sees no body cells: it can only recover the
    header.

    Visual::

        | Name    | Score | Grade |
        +---------+-------+-------+
          Alice     95      A
          Bob       82      B-
          Carol     91      A-
          Dave      76      C+

    Goal: parser still surfaces a four-row, three-column table.  Body cells
    must be reconstructed by snapping word positions to the header's column
    x-bounds.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    data = [
        ["Name",  "Score", "Grade"],
        ["Alice", "95",    "A"],
        ["Bob",   "82",    "B-"],
        ["Carol", "91",    "A-"],
        ["Dave",  "76",    "C+"],
    ]
    t = Table(data, colWidths=[120, 80, 80])
    t.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # Header-only frame: cells visible only on row 0.
        ("GRID",          (0, 0), (-1, 0), 0.5, colors.black),
        # Deliberately no LINE* / BOX / GRID on body rows.
    ]))
    story = [
        Paragraph("Ruled-Header, Open-Body Table", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)


def build_19_ruled_header_framed_body(out: Path) -> None:
    """19_ruled_header_framed_body: header has internal column separators,
    plus an outer box around the entire table.  Body has no internal verticals.

    Visual::

        +---------+-------+-------+
        | Name    | Score | Grade |
        +---------+-------+-------+
        | Alice     95      A      |
        | Bob       82      B-     |
        | Carol     91      A-     |
        | Dave      76      C+     |
        +-------------------------+

    pdfplumber's line strategy sees vertical edges at the outer frame plus the
    internal header dividers, and horizontal edges at the header rule plus the
    outer top/bottom.  The naive grid extraction merges the body into one
    column per row.  Goal: parser snaps body words to the header column bounds.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    data = [
        ["Region",  "Q1",   "Q2",   "Q3",   "Q4"],
        ["North",   "120",  "135",  "150",  "162"],
        ["South",   "98",   "104",  "111",  "120"],
        ["East",    "87",   "92",   "101",  "118"],
        ["West",    "143",  "149",  "156",  "171"],
    ]
    t = Table(data, colWidths=[100, 60, 60, 60, 60])
    t.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # Outer frame.
        ("BOX",           (0, 0), (-1, -1), 0.5, colors.black),
        # Header row: internal verticals + bottom rule.
        ("GRID",          (0, 0), (-1, 0), 0.5, colors.black),
    ]))
    story = [
        Paragraph("Ruled-Header, Framed-Body Table", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)


def build_20_ruled_header_row_strips(out: Path) -> None:
    """20_ruled_header_row_strips: outer frame + header column separators +
    a horizontal rule between every body row.  Each body row is one full-width
    bordered rectangle; no internal vertical separators in the body.

    Visual::

        +---------+-------+--------+
        | Item    | Qty   | Price  |
        +---------+-------+--------+
        | Apple     3       $1.00   |
        +--------------------------+
        | Banana    6       $0.50   |
        +--------------------------+
        | Cherry    12      $2.25   |
        +--------------------------+
        | Date      4       $3.10   |
        +--------------------------+

    pdfplumber's line strategy sees a complete grid of horizontal lines but
    only the header has internal verticals, so body rows extract as a single
    merged column.  Goal: parser snaps body words to header column bounds.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    data = [
        ["Item",   "Qty", "Price"],
        ["Apple",  "3",   "$1.00"],
        ["Banana", "6",   "$0.50"],
        ["Cherry", "12",  "$2.25"],
        ["Date",   "4",   "$3.10"],
    ]
    t = Table(data, colWidths=[120, 80, 80])
    t.setStyle(TableStyle([
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, -1), 10),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        # Outer frame.
        ("BOX",           (0, 0), (-1, -1), 0.5, colors.black),
        # Header: full grid (internal verticals + bottom rule).
        ("GRID",          (0, 0), (-1, 0), 0.5, colors.black),
        # Horizontal rules between every body row (excludes the outer bottom,
        # which BOX already draws).  This draws under rows 0..-2 in data-row
        # space; LINEBELOW on row N draws under that row.
        ("LINEBELOW",     (0, 1), (-1, -2), 0.5, colors.black),
    ]))
    story = [
        Paragraph("Ruled-Header, Row-Strips Table", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)


def build_21_vertical_merge_invisible_lines(out: Path) -> None:
    """21_vertical_merge_invisible_lines: a normal gridded table where the
    horizontal row separators in *one column* are overdrawn with white, making
    that column appear visually merged across three rows.

    Visual::

        +----------+-----+-----+-----+
        | Region   | Q1  | Q2  | Q3  |
        +----------+-----+-----+-----+
        | Pacific    100   110   120 |
        |            +-----+-----+-----+
        | Northwest  200   210   220 |
        |            +-----+-----+-----+
        | Division   300   310   320 |
        +----------+-----+-----+-----+
        | Marketing  400   410   420 |
        +----------+-----+-----+-----+

    pdfplumber's "lines" strategy sees full-width black row separators *and*
    the white overdraws as independent lines; without colour-aware filtering it
    splits the merged column into three single-line rows whose adjacent-column
    data leaks into the wrong row.  Goal: parser subtracts background-coloured
    overdraws from the visible edge set so the column collapses into one cell
    spanning rows 1–3, matching what the reader actually sees.
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()
    data = [
        ["Region",    "Q1",  "Q2",  "Q3"],
        ["Pacific",   "100", "110", "120"],
        ["Northwest", "200", "210", "220"],
        ["Division",  "300", "310", "320"],
        ["Marketing", "400", "410", "420"],
    ]
    t = Table(data, colWidths=[100, 60, 60, 60], rowHeights=[20, 20, 20, 20, 20])
    t.setStyle(TableStyle([
        ("FONTNAME",   (0, 0), (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",   (0, 0), (-1, -1), 10),
        ("VALIGN",     (0, 0), (-1, -1), "TOP"),
        ("GRID",       (0, 0), (-1, -1), 0.5, colors.black),
        # Overdraw the row-1/row-2 and row-2/row-3 separators in col 0 with
        # white: visually those lines disappear, but pdfplumber still sees them
        # as black + white edges at the same y.
        ("LINEBELOW",  (0, 1), (0, 1),   0.5, colors.white),
        ("LINEBELOW",  (0, 2), (0, 2),   0.5, colors.white),
    ]))
    story = [
        Paragraph("Vertically Merged Cells (Invisible Row Separators)", s["Heading1"]),
        Spacer(1, 12),
        Paragraph(
            "The first column visually merges three rows because the inner row "
            "dividers are drawn in the page background colour.  A reader sees "
            "one merged cell containing three lines of text; the PDF data, "
            "however, still encodes three separate rows split by white-on-white "
            "lines.  A colour-aware parser must honour the visual reading.",
            s["BodyText"],
        ),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)


def build_22_text_between_adjacent_tables(out: Path) -> None:
    """22_text_between_adjacent_tables: two sub-tables nested inside one outer
    cell with a rich mixed-content region between them (footnote, bullets,
    paragraph, heading, outline bullets).

    Extends fixtures 16/17 (which only had a single paragraph between the two
    sub-tables) with the full range of between-content seen in real documents:

      Outer table (1 col × 1 row, BOX border only — no horizontal divider
      between sections) holds, in one cell, this flowable sequence:
        Sub-table 1   ← 7 cols, 4 rows; first column visually merged (same
                        text every row); one cell wraps a 2-line value;
                        sub-category text carries asterisk footnote markers.
        Footnote      ← starts with "*" directly under sub-table 1.
        Dot bullets   ← two filled-disc list items, each multi-sentence.
        Paragraph     ← plain text, no bullet.
        Heading       ← bold inline section heading.
        o-bullets     ← two outline ("o") list items, indented under heading.
        Sub-table 2   ← 6 cols; headers wrap to two lines (label + "(%)");
                        last column intentionally empty.

    Content is synthetic (public-library collection metrics) and contains no
    confidential information.
    """
    from reportlab.platypus import ListFlowable, ListItem

    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()

    # ----- Table 1: 7 cols, merged first column visually repeated per row.
    t1_data = [
        ["Collection",   "Books",                 "32%", "28%", "30%", "28%", "or 50,000\nitems"],
        ["Collection",   "Periodicals",           "8%",  "7%",  "5%",  "7%",  "Max 25%"],
        ["Collection",   "Specialty Producers*",  "8%",  "10%", "8%",  "8%",  "NA"],
        ["Collection",   "Other Producers*",      "11%", "10%", "10%", "8%",  "Max 15%"],
    ]
    t1 = Table(
        t1_data,
        colWidths=[80, 110, 50, 50, 50, 50, 70],
        style=TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.black),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN",         (2, 0), (-1, -1), "CENTER"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]),
    )

    # ----- Between-region content (this is what disappeared in the real parse).
    body = s["BodyText"]
    body.fontSize = 9
    body.leading = 12

    footnote = Paragraph(
        "* Specialty Producers and Other Producers are adjusted to reflect the "
        "correct catalogue code at branch level. Reference works are aggregated "
        "to other producers.",
        body,
    )

    dot_bullets = ListFlowable(
        [
            ListItem(Paragraph(
                "Given the continued strong digital lending performance and "
                "regional opportunity, the Audio &amp; Digital cap is to be revised "
                "to 20% from 15% to support eBook program expansion and to offset "
                "the reduction in the overall Collection portfolio on the back of "
                "lower Books sub-category exposures.",
                body,
            )),
            ListItem(Paragraph(
                "In recognising the change in the collection composition, "
                "combined with the books category still grappling with print "
                "decline, weakened demand and elevated storage costs, the Books "
                "cap is being revised downward to a maximum of 40% (previously "
                "55%) or 50,000 items (previously 65,000).",
                body,
            )),
        ],
        bulletType="bullet",
        leftIndent=20,
    )

    plain_para = Paragraph(
        "The Other Producers and Other Media cap is at 15% primarily to support "
        "transition projects in Specialty Collections production not covered "
        "under other sub-segment caps and as detailed under the "
        "&ldquo;Preservation / Sustainability / Heritage Limits&rdquo; section8.",
        body,
    )

    region_heading = Paragraph(
        "<b>Branches (Breakdown by Service Area (&ldquo;SVC&rdquo;) Region):</b>",
        body,
    )

    o_bullets = ListFlowable(
        [
            ListItem(Paragraph(
                "North, Central, South are the top 3 areas in terms of circulation.",
                body,
            )),
            ListItem(Paragraph(
                "No specific area cap is proposed but the collection should "
                "continue to be managed in a balanced manner.",
                body,
            )),
        ],
        bulletType="bullet",
        start="o",
        leftIndent=40,
    )

    # ----- Table 2: 6 cols, two-line column headers, last column empty.
    t2_data = [
        ["SVC Region", "Dec-24\n(%)", "Mar-25\n(%)", "Jun-25\n(%)", "Sep-25\n(%)", "Proposed PS"],
        ["North",      "24%",         "26%",         "22%",         "22%",         ""],
        ["Central",    "16%",         "15%",         "15%",         "14%",         ""],
        ["South",      "13%",         "14%",         "13%",         "14%",         ""],
    ]
    t2 = Table(
        t2_data,
        colWidths=[90, 65, 65, 65, 65, 90],
        style=TableStyle([
            ("GRID",          (0, 0), (-1, -1), 0.5, colors.black),
            ("BACKGROUND",    (0, 0), (-1, 0),  colors.lightgrey),
            ("FONTNAME",      (0, 0), (-1, 0),  "Helvetica-Bold"),
            ("FONTSIZE",      (0, 0), (-1, -1), 9),
            ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
            ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]),
    )

    # Outer table is a single 1-row, 1-column cell.  All inner content —
    # sub-table 1, the mixed text region (footnote, bullets, paragraph,
    # heading, o-bullets), and sub-table 2 — sits as a flat list of
    # flowables inside that one cell.  There is no horizontal divider
    # between the sub-tables and the text; only the outer BOX border.
    inner_flowables = [
        t1,
        Spacer(1, 6),
        footnote,
        Spacer(1, 8),
        dot_bullets,
        Spacer(1, 8),
        plain_para,
        Spacer(1, 10),
        region_heading,
        Spacer(1, 4),
        o_bullets,
        Spacer(1, 10),
        t2,
    ]
    outer = Table(
        [[inner_flowables]],
        colWidths=[490],
        style=TableStyle([
            ("BOX",           (0, 0), (-1, -1), 0.75, colors.black),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 6),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 6),
            ("TOPPADDING",    (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]),
    )

    story = [
        Paragraph("Text Between Adjacent Tables (Nested in Outer Cell)", s["Heading1"]),
        Spacer(1, 12),
        outer,
    ]
    doc.build(story)


def build_23_bordered_cell_with_bulleted_prose(out: Path) -> None:
    """23_bordered_cell_with_bulleted_prose: bordered 1x2 outer table where
    the right cell contains a heading + intro + bulleted prose items.

    Mirrors a real-world credit-underwriting PDF pattern (Growth Strategy /
    Portfolio Thresholds row) that the text-strategy fallback used to shred
    into a fake many-column "nested table" of mid-word fragments.  The
    output must be a single 1x2 outer table — the right cell's content
    must be preserved as text/list, NOT promoted into a phantom inner
    table whose cells split inside individual words.

    Adversarial parameters chosen to defeat ``_MAX_CELL_TEXT_CHARS = 7``
    alone:

      * narrow right column (2.5 in)  -> many short wrapped fragments,
      * small justified font (Helvetica 8 pt) -> tight vertical lanes,
      * Lorem-ipsum bullets x 4       -> ~50 lines of wrapped prose.

    Together these push pdfplumber's text-strategy result to ~46x7 with
    average cell length ~6.8 chars (under the 7-char floor), so the only
    remaining signal that this is shredded prose is the dominant fraction
    of cells starting with a lowercase letter — the
    ``_MAX_LOWERCASE_START_RATIO`` check that the fixture pins.
    """
    from reportlab.lib.enums import TA_JUSTIFY
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch

    body = ParagraphStyle(
        "body23", fontName="Helvetica", fontSize=8, leading=10,
        alignment=TA_JUSTIFY,
    )
    bullet = ParagraphStyle(
        "bullet23", parent=body, leftIndent=12, bulletIndent=3,
        spaceBefore=2, alignment=TA_JUSTIFY,
    )
    long_text = (
        "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do "
        "eiusmod tempor incididunt ut labore et dolore magna aliqua Ut "
        "enim ad minim veniam quis nostrud exercitation ullamco laboris "
        "nisi ut aliquip ex ea commodo consequat Duis aute irure dolor in "
        "reprehenderit in voluptate velit esse cillum dolore eu fugiat "
        "nulla pariatur Excepteur sint occaecat cupidatat non proident "
        "sunt in culpa qui officia deserunt mollit anim id est laborum."
    )
    right_cell = [
        Paragraph("Section", body),
        Paragraph("Intro:", body),
    ]
    for _ in range(4):
        right_cell.append(Paragraph(long_text, bullet, bulletText="\u2022"))
    left_cell = Paragraph("Label", body)

    tbl = Table(
        [[left_cell, right_cell]],
        colWidths=[0.7 * inch, 2.5 * inch],
        style=TableStyle([
            ("BOX",           (0, 0), (-1, -1), 0.75, colors.black),
            ("INNERGRID",     (0, 0), (-1, -1), 0.5,  colors.black),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING",   (0, 0), (-1, -1), 4),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
            ("TOPPADDING",    (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]),
    )

    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    doc.build([
        Paragraph("Bordered Cell with Bulleted Prose", _styles()["Heading1"]),
        Spacer(1, 12),
        tbl,
    ])

def build_24_subtable_flush_outer_edges(out: Path) -> None:
    """24_subtable_flush_outer_edges: nested sub-tables whose top/bottom
    edges coincide exactly with the outer table's top/bottom edges.

    Real-world manifestation: when a multi-page outer table is split at a
    page boundary, the FIRST inner sub-table on a continuation page sits
    flush against the top of the outer frame (no header row or padding
    above it), and the LAST inner sub-table on a non-final page sits
    flush against the bottom of the outer frame.  This fixture models the
    worst case of that pattern in a single page so both edges can be
    exercised together:

      * outer.top_y    == sub_a.top_y        (shared horizontal edge)
      * outer.left_x   == sub_a.left_x       (shared left vertical rail)
      * outer.right_x  == sub_a.right_x      (shared right vertical rail)
      * outer.bottom_y == sub_b.bottom_y     (shared horizontal edge)
      * outer.left_x   == sub_b.left_x       (shared left vertical rail)
      * outer.right_x  == sub_b.right_x      (shared right vertical rail)

    Structure (single page):
      Outer table: 1 col × 1 row, BOX border, ZERO padding on all sides,
                   colWidth = 240 pt.
      Inner cell flowables, in order:
        Sub-table A   2 cols × 3 rows, GRID border, colWidths=[120, 120]
                      (Item / Qty header + 2 data rows)
        Paragraph     "NOTE: ..." between text
        Sub-table B   2 cols × 3 rows, GRID border, colWidths=[120, 120]
                      (Month / Sales header + 2 data rows)

    Expected parse:
      * Exactly one outer table at the top level.
      * Both sub-tables nest INSIDE the outer's content cell.  Neither is
        promoted to a top-level sibling.
      * The "NOTE:" paragraph survives as a paragraph node inside the
        outer cell, sandwiched between the two sub-table nodes.
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
            colWidths=[120, 120],
        )

    sub_a = _sub(["Item", "Qty"], [["Widget A", "10"], ["Widget B", "5"]])
    sub_b = _sub(["Month", "Sales"], [["Jan", "$500"], ["Feb", "$700"]])

    body = s["BodyText"]
    body.fontSize = 9
    body.leading = 12
    between_para = Paragraph(
        "NOTE: This paragraph sits between two sub-tables that are flush "
        "against the outer frame's top and bottom edges. It must survive "
        "parsing as a paragraph node inside the outer cell.",
        body,
    )

    inner_flowables = [sub_a, between_para, sub_b]

    outer = Table(
        [[inner_flowables]],
        # Outer column width matches the sub-tables' total width so that
        # the outer's left/right vertical rails coincide with the inner
        # sub-tables' left/right vertical rails — the second axis of
        # "flush against outer frame".
        colWidths=[240],
        style=TableStyle([
            ("BOX",           (0, 0), (-1, -1), 0.75, colors.black),
            ("VALIGN",        (0, 0), (-1, -1), "TOP"),
            # Zero padding everywhere so sub_a.top == outer.top and
            # sub_b.bottom == outer.bottom (the shared horizontal edges).
            ("LEFTPADDING",   (0, 0), (-1, -1), 0),
            ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
            ("TOPPADDING",    (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]),
    )

    story = [
        Paragraph("Sub-Tables Flush With Outer Frame Edges", s["Heading1"]),
        Spacer(1, 12),
        outer,
    ]
    doc.build(story)


def build_25_subtable_flush_outer_vertical_only(out: Path) -> None:
    """25_subtable_flush_outer_vertical_only: two-page fixture where each
    page hosts its own closed outer table.  Each outer has a sub-table
    flush with ONE vertical edge (bottom on page 1, top on page 2) while
    the sub-tables are inset HORIZONTALLY from the outer's rails (12 pt
    of left/right padding).

    This separates the two flush axes:

      * Vertical flush  — sub_b.bottom == outer.bottom (page 1)
                          sub_c.top    == outer.top    (page 2)
      * Horizontal NOT  — sub_*.left   >  outer.left
                          sub_*.right  <  outer.right

    Fixture 24 covers the case where both axes are flush simultaneously;
    this fixture covers the more common page-break manifestation where
    only the vertical axis is flush (the inner sub-tables don't usually
    fill the outer cell's full content width).

    Each outer is independently closed (BOX border, top/bottom horizontals
    drawn) — the two outers are NOT a single page-spanning table; they
    are two separate frames placed on consecutive pages and must surface
    as two top-level tables in the parsed tree.

    Page 1 outer cell content (top → bottom):
      paragraph     (intro, flush with outer.top)
      sub_a         (Item / Qty,  not flush)
      paragraph     (between)
      sub_b         (Month / Sales, FLUSH with outer.bottom)

    Page 2 outer cell content (top → bottom):
      sub_c         (Step / Owner, FLUSH with outer.top)
      paragraph     (between)
      sub_d         (City / Zone,  not flush)
      paragraph     (outro, flush with outer.bottom)
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = _styles()

    body = s["BodyText"]
    body.fontSize = 9
    body.leading = 12

    def _sub(header: list[str], rows: list[list[str]]) -> Table:
        return Table(
            [header] + rows,
            style=TableStyle([
                ("GRID",       (0, 0), (-1, -1), 0.5, colors.darkblue),
                ("BACKGROUND", (0, 0), (-1, 0),  colors.lightblue),
                ("FONTNAME",   (0, 0), (-1, 0),  "Helvetica-Bold"),
                ("FONTSIZE",   (0, 0), (-1, -1), 8),
            ]),
            colWidths=[110, 110],   # total 220 pt, inset from the 336 pt cell-content width
        )

    def _outer(inner_flowables: list) -> Table:
        return Table(
            [[inner_flowables]],
            colWidths=[360],
            style=TableStyle([
                ("BOX",           (0, 0), (-1, -1), 0.75, colors.black),
                ("VALIGN",        (0, 0), (-1, -1), "TOP"),
                # 12 pt horizontal padding so sub-tables are inset from the
                # outer's vertical rails (the "not flush horizontally" axis).
                ("LEFTPADDING",   (0, 0), (-1, -1), 12),
                ("RIGHTPADDING",  (0, 0), (-1, -1), 12),
                # Zero vertical padding so the first and last inner flowables
                # sit flush against the outer's top and bottom edges.
                ("TOPPADDING",    (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]),
        )

    # ----- Page 1: paragraph → sub_a → paragraph → sub_b (flush bottom)
    sub_a = _sub(["Item",  "Qty"],   [["Widget A", "10"], ["Widget B", "5"]])
    sub_b = _sub(["Month", "Sales"], [["Jan",      "$500"], ["Feb",     "$700"]])

    page1_intro = Paragraph(
        "NOTE-TOP: This paragraph sits at the top of the page-1 outer cell. "
        "Its top edge is flush with the outer frame's top edge; sub-table B "
        "below is flush with the outer frame's bottom edge.",
        body,
    )
    page1_between = Paragraph(
        "NOTE-MID1: Between paragraph on page 1, separating sub-table A "
        "from sub-table B.",
        body,
    )
    page1_outer = _outer([page1_intro, sub_a, page1_between, sub_b])

    # ----- Page 2: sub_c (flush top) → paragraph → sub_d → paragraph
    sub_c = _sub(["Step", "Owner"], [["1", "Alice"], ["2", "Bob"]])
    sub_d = _sub(["City", "Zone"],  [["NYC", "East"], ["LA",  "West"]])

    page2_between = Paragraph(
        "NOTE-MID2: Between paragraph on page 2, separating sub-table C "
        "(flush against the outer frame's top edge) from sub-table D.",
        body,
    )
    page2_outro = Paragraph(
        "NOTE-BOT: This paragraph sits at the bottom of the page-2 outer cell. "
        "Its bottom edge is flush with the outer frame's bottom edge; "
        "sub-table C above is flush with the outer frame's top edge.",
        body,
    )
    page2_outer = _outer([sub_c, page2_between, sub_d, page2_outro])

    story = [
        Paragraph(
            "Sub-Tables Flush With Outer Frame on One Vertical Edge — Page 1",
            s["Heading1"],
        ),
        Spacer(1, 12),
        page1_outer,
        PageBreak(),
        Paragraph(
            "Sub-Tables Flush With Outer Frame on One Vertical Edge — Page 2",
            s["Heading1"],
        ),
        Spacer(1, 12),
        page2_outer,
    ]
    doc.build(story)


def build_26_spanning_subtable_flush_at_break(out: Path) -> None:
    """26_spanning_subtable_flush_at_break: page-spanning outer table whose
    nested sub-table is ALSO split across the page break, with the inner
    halves sitting FLUSH against the outer's bottom edge on page n and
    against the outer's top edge on page n+1.

    The inner sub-table has 5 rows total:
      Row 0 — header  ("sub-H1", "sub-H2")
      Row 1 — data    ("a", "1")
      Row 2 — data    ("b", "2")
      Row 3 — data    ("c", "3")
      Row 4 — data    ("d", "4")

    Three rows (header + rows 1 + 2) render on page n; two rows
    (rows 3 + 4) render on page n+1.  ReportLab cannot split a single
    nested Table cleanly (``Table._splitCell`` crashes on ``Table.height``
    lookup), so the sub-table is authored as two halves placed in adjacent
    outer rows; both halves share identical column widths so the
    extractor recognises them as one continued sub-table by matching
    column anchors.

    Flush placement:
      * Outer row P (last row on page n) holds the 3-row top half.  The
        outer row's BOTTOMPADDING is 0, so inner_top.bottom == outer row
        P's bottom == outer's bottom edge on page n.
      * Outer row P+1 (first row on page n+1) holds the 2-row bottom
        half.  Its TOPPADDING is 0, so inner_bottom.top == outer row
        P+1's top == outer's top edge on page n+1.

    Together with the outer's GRID style, page n's last visible
    horizontal is shared between the inner top-half's bottom edge and
    the outer's bottom edge; page n+1's first visible horizontal is
    shared between the inner bottom-half's top edge and the outer's
    top edge.  The cell-builder must therefore detect inner sub-tables
    even when their edge coincides with the parent cell's edge — the
    1 pt shrink that normally guards against re-detecting the parent
    would otherwise crop those flush edges and the inner halves would
    lose their boundary rows.

    Row counts engineered so:
      * Total outer rows  = 35 (header + 34 data rows).
      * Inner halves      = rows 28 and 29 (0-indexed) of the outer.
      * Page break        = between rows 28 and 29.
      * Row 28 contains the 3-row top half (last on page n).
      * Row 29 contains the 2-row bottom half (first on page n+1).
    """
    doc = SimpleDocTemplate(str(out), pagesize=LETTER, topMargin=72, bottomMargin=72)
    s = _styles()

    def _inner(data: list[list[str]], with_header: bool) -> Table:
        body = ([["sub-H1", "sub-H2"]] if with_header else []) + data
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey)]
        if with_header:
            style.append(("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey))
        return Table(body, style=TableStyle(style), colWidths=[60, 60])

    # Outer row index where the split happens.  The two halves live in
    # consecutive outer rows.  Page-break occurs between them.
    SPLIT_ROW = 28  # 0-indexed; "row 28" of outer is last on page n.
    N_OUTER_ROWS = 35  # header + 34 data

    rows: list[list] = [["Step", "Detail", "Notes"]]
    for i in range(1, N_OUTER_ROWS):
        if i == SPLIT_ROW:
            # Top half: header + 2 data rows = 3 rows total on page n.
            rows.append([
                str(i),
                _inner([["a", "1"], ["b", "2"]], with_header=True),
                "ends pg n",
            ])
        elif i == SPLIT_ROW + 1:
            # Bottom half: 2 data rows on page n+1 (no header repeat).
            rows.append([
                str(i),
                _inner([["c", "3"], ["d", "4"]], with_header=False),
                "starts pg n+1",
            ])
        else:
            rows.append([str(i), f"plain {i}", f"n{i}"])

    # Per-row paddings.  Default padding stays at the ReportLab default
    # (6 pt) everywhere except the two halves' rows, where the relevant
    # vertical padding is zeroed so the inner sub-tables sit flush.
    style_ops = [
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        # Row SPLIT_ROW: bottom-flush inner half — zero bottom padding,
        # standard top padding (the inner top half is NOT flush to its
        # cell's top, only to the bottom).
        ("BOTTOMPADDING", (0, SPLIT_ROW), (-1, SPLIT_ROW), 0),
        # Row SPLIT_ROW+1: top-flush inner half — zero top padding,
        # standard bottom padding.
        ("TOPPADDING", (0, SPLIT_ROW + 1), (-1, SPLIT_ROW + 1), 0),
    ]

    t = Table(
        rows,
        style=TableStyle(style_ops),
        colWidths=[60, 200, 80],
    )
    story = [
        Paragraph("Spanning Sub-Table Flush At Page Break", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)



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
    "14b_borderless_long_text":           build_14b_borderless_long_text,
    "14c_borderless_long_text_spanning":  build_14c_borderless_long_text_spanning,
    "15_multicolumn_text": build_15_multicolumn_text,
    "16_text_between_subtables": build_16_text_between_subtables,
    "17_text_between_subtables_spanning": build_17_text_between_subtables_spanning,
    "18_ruled_header_open_body":          build_18_ruled_header_open_body,
    "19_ruled_header_framed_body":        build_19_ruled_header_framed_body,
    "20_ruled_header_row_strips":         build_20_ruled_header_row_strips,
    "21_vertical_merge_invisible_lines":  build_21_vertical_merge_invisible_lines,
    "22_text_between_adjacent_tables":    build_22_text_between_adjacent_tables,
    "23_bordered_cell_with_bulleted_prose": build_23_bordered_cell_with_bulleted_prose,
    "24_subtable_flush_outer_edges":      build_24_subtable_flush_outer_edges,
    "25_subtable_flush_outer_vertical_only": build_25_subtable_flush_outer_vertical_only,
    "26_spanning_subtable_flush_at_break": build_26_spanning_subtable_flush_at_break,
}

def build_all() -> None:
    for name, builder in BUILDERS.items():
        out_dir = GOLDEN_DIR / name
        out_dir.mkdir(parents=True, exist_ok=True)
        builder(out_dir / "source.pdf")


if __name__ == "__main__":
    build_all()
