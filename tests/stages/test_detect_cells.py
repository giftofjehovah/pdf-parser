"""Cell dataclass shape: bbox, text, source, confidence."""
from pathlib import Path

import pdfplumber

from pdf_parser.model import BBox
from pdf_parser.stages.detect_cells import Cell, _line_cells, _group_words_into_lines, _find_column_gutters, _gutter_cells, _frame_cells, detect_cells



def test_cell_holds_bbox_text_source_confidence():
    bb = BBox(page=0, x0=0, y0=0, x1=10, y1=10)
    c = Cell(bbox=bb, text="x", source="line", confidence=1.0)
    assert c.bbox == bb
    assert c.text == "x"
    assert c.source == "line"
    assert c.confidence == 1.0


def test_cell_source_is_constrained():
    bb = BBox(page=0, x0=0, y0=0, x1=10, y1=10)
    for src in ("line", "gutter", "text"):
        Cell(bbox=bb, text="", source=src, confidence=0.5)


def test_line_cells_on_01_simple_table():
    pdf_path = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _line_cells(page, page_index=0)
    # 01_simple_table = 3 rows × 3 cols = 9 line-bounded cells.
    assert len(cells) == 9
    assert all(c.source == "line" for c in cells)
    assert all(c.confidence == 1.0 for c in cells)
    # Header row contains "Name"/"Quantity"/"Price" (any order in detected set).
    texts = {c.text for c in cells}
    assert {"Name", "Quantity", "Price"} <= texts

def test_word_lines_y_bucketed():
    words = [
        {"x0": 10, "x1": 30, "top": 100, "bottom": 110, "text": "Hello"},
        {"x0": 40, "x1": 60, "top": 101, "bottom": 111, "text": "world"},
        {"x0": 10, "x1": 30, "top": 130, "bottom": 140, "text": "Next"},
    ]
    lines = _group_words_into_lines(words, tol=2.0)
    assert len(lines) == 2
    assert [w["text"] for w in lines[0]] == ["Hello", "world"]
    assert [w["text"] for w in lines[1]] == ["Next"]


def test_word_lines_keeps_third_line_intact_after_line_break():
    """After a line break, cur_y must reset so the next word in the new line
    is bucketed correctly. Regression for the running-average bug where the
    update fired in both branches and contaminated the new-line centroid."""
    words = [
        {"x0": 10, "x1": 30, "top": 100, "bottom": 100, "text": "A"},
        {"x0": 10, "x1": 30, "top": 130, "bottom": 130, "text": "B1"},
        {"x0": 40, "x1": 60, "top": 131, "bottom": 131, "text": "B2"},
    ]
    lines = _group_words_into_lines(words, tol=2.0)
    assert len(lines) == 2
    assert [w["text"] for w in lines[0]] == ["A"]
    assert [w["text"] for w in lines[1]] == ["B1", "B2"]


def test_word_lines_handles_identical_y_midpoints():
    """Two words sharing top/bottom must not raise TypeError from a dict
    comparison falling through the sort key. Regression for the
    (ymid, dict) sort key."""
    words = [
        {"x0": 40, "x1": 60, "top": 100, "bottom": 110, "text": "B"},
        {"x0": 10, "x1": 30, "top": 100, "bottom": 110, "text": "A"},
    ]
    lines = _group_words_into_lines(words, tol=2.0)
    assert len(lines) == 1
    assert [w["text"] for w in lines[0]] == ["A", "B"]

def test_gutters_three_columns():
    """Three text columns with consistent inter-column whitespace.

    Each row's words: [Name        Score   Grade] at fixed x-ranges.
    """
    line_words: list[list[dict]] = []
    for y in (100.0, 120.0, 140.0, 160.0):
        line_words.append([
            {"x0": 50, "x1": 90, "top": y, "bottom": y + 8, "text": "Alice"},
            {"x0": 150, "x1": 170, "top": y, "bottom": y + 8, "text": "95"},
            {"x0": 220, "x1": 230, "top": y, "bottom": y + 8, "text": "A"},
        ])
    gutters = _find_column_gutters(line_words, min_run=3, min_gap_pt=8.0)
    # Two inter-column gutters → 3 column ranges.
    assert len(gutters) == 2

def test_gutter_cells_on_14_borderless_table():
    pdf_path = Path("tests/golden/synthetic/14_borderless_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _gutter_cells(page, page_index=0)
    assert cells, "gutter detector must find cells on a borderless table"
    assert all(c.source == "gutter" for c in cells)

def test_gutter_cells_reject_multicolumn_prose():
    """15_multicolumn_text is body prose; gutter detector must NOT see a table."""
    pdf_path = Path("tests/golden/synthetic/15_multicolumn_text/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _gutter_cells(page, page_index=0)
    assert cells == [], (
        f"Multi-column prose was misclassified as table cells: "
        f"{[c.text[:30] for c in cells[:5]]}"
    )

def test_text_fallback_not_invoked_when_line_or_gutter_succeed():
    """01_simple_table is line-bounded; text fallback never runs."""
    pdf_path = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)
    assert all(c.source == "line" for c in cells)


def test_frame_cells_emits_header_and_content_on_fixture_17_page_0():
    """Fixture 17 page 0: vertical rails at x=106..506 with two top caps at
    y=118 and y=138 ("Section Header" band).  pdfplumber's line strategy
    does NOT emit this outer wrapper because the side rails don't intersect
    inner sub-table grid lines.  _frame_cells must synthesise it from the
    rail+cap geometry so _carve_container_frames + _build_single_col_wrapper
    can build the 1xN wrapper downstream.
    """
    pdf_path = Path("tests/golden/synthetic/17_text_between_subtables_spanning/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _frame_cells(page, page_index=0)
    # Two cells: header band + content.
    assert len(cells) == 2, [
        (c.bbox.x0, c.bbox.y0, c.bbox.x1, c.bbox.y1, c.text) for c in cells
    ]
    header = next(c for c in cells if c.text)
    content = next(c for c in cells if not c.text)
    assert header.text == "Section Header"
    # Header band: x = full rails (106..506), y = top_caps[0]..top_caps[1] (118..138).
    assert header.bbox.x0 == 106.0 and header.bbox.x1 == 506.0
    assert header.bbox.y0 == 118.0 and header.bbox.y1 == 138.0
    # Content cell: spans below header to rail bottom (712 in top-relative coords).
    assert content.bbox.x0 == 106.0 and content.bbox.x1 == 506.0
    assert content.bbox.y0 == 138.0 and content.bbox.y1 == 712.0
    assert all(c.source == "line" for c in cells)


def test_frame_cells_emits_content_and_footer_on_fixture_17_page_1():
    """Fixture 17 page 1: rails at x=106..506 with two bot caps at y=544
    and y=564 ("Section Footer" band).  Symmetric to page 0."""
    pdf_path = Path("tests/golden/synthetic/17_text_between_subtables_spanning/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[1]
        cells = _frame_cells(page, page_index=1)
    assert len(cells) == 2
    footer = next(c for c in cells if c.text)
    content = next(c for c in cells if not c.text)
    assert footer.text == "Section Footer"
    assert footer.bbox.x0 == 106.0 and footer.bbox.x1 == 506.0
    assert footer.bbox.y0 == 544.0 and footer.bbox.y1 == 564.0
    assert content.bbox.y0 == 78.0 and content.bbox.y1 == 544.0


def test_frame_cells_skips_when_internal_rail_present():
    """Fixture 03 has rails at x=96, 156, 436, 516 (column dividers, not a
    frame).  The outer pair (96, 516) has internal rails between it, so
    _frame_cells must NOT promote it to a frame — that would wrongly wrap
    the 3-column table.
    """
    pdf_path = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        for pidx, page in enumerate(pdf.pages):
            cells = _frame_cells(page, page_index=pidx)
            assert cells == [], f"page {pidx} promoted spurious frame: {cells}"


def test_frame_cells_skips_when_existing_cell_spans_rails():
    """Fixture 19 (ruled_header_framed_body): pdfplumber's line strategy
    already emits an outer cell at the rail pair (136, 476).  _frame_cells
    must not duplicate it — the existing-side check skips the frame when
    any line cell already spans the candidate rail pair.
    """
    pdf_path = Path("tests/golden/synthetic/19_ruled_header_framed_body/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _frame_cells(page, page_index=0)
    assert cells == [], f"fixture 19 promoted spurious frame: {cells}"


def test_frame_cells_skips_pure_closed_rect():
    """Fixture 25 page 0: rails with only 1 top and 1 bot cap (pure closed
    rectangle).  Without header/footer band evidence we cannot build a
    multi-row wrapper, so _frame_cells stays a no-op for now.  Fixture 25
    parity remains a documented Phase-10+ residual.
    """
    pdf_path = Path("tests/golden/synthetic/25_subtable_flush_outer_vertical_only/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _frame_cells(page, page_index=0)
    assert cells == [], f"fixture 25 promoted unexpected frame: {cells}"


def test_frame_cells_no_op_on_multicolumn_prose():
    """Fixture 15 has zero vector lines; _frame_cells must stay a no-op."""
    pdf_path = Path("tests/golden/synthetic/15_multicolumn_text/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = _frame_cells(page, page_index=0)
    assert cells == []


def test_detect_cells_emits_body_grid_on_open_body_ruled_header():
    """Fixture 18 (open body): line strategy emits 3 header cells but zero
    body cells.  detect_cells must re-bin the body words into the header's
    column ranges so aggregate_tables can build the 5x3 grid that legacy's
    text-strategy extractor produces.
    """
    pdf_path = Path("tests/golden/synthetic/18_ruled_header_open_body/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)
    texts = {c.text for c in cells}
    # Header (line cells)
    assert {"Name", "Score", "Grade"} <= texts
    # Body cells re-extracted from words and bound to header columns
    assert {"Alice", "95", "A", "Bob", "82", "B-", "Carol", "91", "A-",
            "Dave", "76", "C+"} <= texts
    # Body cells inherit the header's column x-ranges (shared style).
    # Column 0: 166..286, column 1: 286..366, column 2: 366..446.
    body_cells = [c for c in cells if c.text in {"Alice", "Bob", "Carol", "Dave"}]
    assert body_cells and all(c.bbox.x0 == 166.0 and c.bbox.x1 == 286.0
                              for c in body_cells), body_cells


def test_detect_cells_replaces_monster_body_line_with_rebinned_cells():
    """Fixture 19 (framed body): pdfplumber's line strategy collapses the
    whole body into ONE full-width line cell ("North 120 135 150 162\\n..."),
    masking the 5x5 grid.  detect_cells must drop that monster cell and
    re-bin its words into the 5 header columns.
    """
    pdf_path = Path("tests/golden/synthetic/19_ruled_header_framed_body/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)
    texts = [c.text for c in cells]
    # The monster body cell text must NOT appear.
    assert not any("\n" in t for t in texts), \
        f"monster body cell leaked into output: {texts!r}"
    # Each Region body word must surface as its own cell.
    for word in ("North", "South", "East", "West",
                 "120", "135", "150", "162",
                 "98", "104", "111", "143", "149", "156", "171"):
        assert word in texts, f"missing body word {word!r}: {texts!r}"


def test_detect_cells_replaces_row_strip_monsters_with_word_cells():
    """Fixture 20 (row strips): each body row is a single full-width line
    cell ("Apple 3 $1.00").  detect_cells must drop all 4 row-strip monsters
    and re-bin their words into the 3 header columns.
    """
    pdf_path = Path("tests/golden/synthetic/20_ruled_header_row_strips/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)
    texts = [c.text for c in cells]
    # Row-strip concatenated text must NOT appear ("Apple 3 $1.00" etc.).
    for monster in ("Apple 3 $1.00", "Banana 6 $0.50",
                    "Cherry 12 $2.25", "Date 4 $3.10"):
        assert monster not in texts, \
            f"row-strip monster leaked into output: {monster!r}"
    # Each body word must surface as its own cell, with '$' attached to price.
    assert {"Apple", "Banana", "Cherry", "Date"} <= set(texts)
    assert {"3", "6", "12", "4"} <= set(texts)
    assert {"$1.00", "$0.50", "$2.25", "$3.10"} <= set(texts)


def test_detect_cells_leaves_column_structured_body_alone():
    """Fixture 01 (simple_table): header AND body line cells already form
    the column structure.  detect_cells must NOT re-extract — otherwise it
    duplicates body cells and breaks column-anchor clustering.
    """
    pdf_path = Path("tests/golden/synthetic/01_simple_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)
    # Exactly the 9 line cells (3 header + 6 body), no re-binned duplicates.
    assert len(cells) == 9, [c.text for c in cells]
    assert all(c.source == "line" for c in cells)


def test_ruled_header_body_skips_re_extraction_on_wrapped_prose():
    """Fixture 27: 5-column ruled header above a single full-width bordered
    cell containing a wrapped justified bulleted paragraph.

    Reproduces the bug where the prose paragraph rendered with vertical
    column lines slicing through the text: `_ruled_header_body_cells` saw
    the multi-column header + one full-width monster body cell pattern
    (matching fixtures 19 / 20) and re-binned the paragraph's words into
    the header's column x-ranges, producing a synthetic mini-table.  The
    prose guard (multi-word-cell ratio > 0.5) skips re-extraction so the
    monster cell remains intact and the paragraph renders as a single
    full-width spanning row.
    """
    pdf_path = Path("tests/golden/synthetic/27_ruled_header_prose_body_cell/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)

    # Header should still be 5 side-by-side cells.
    header_texts = {c.text for c in cells if c.text in {"A", "B", "C", "D", "data"}}
    assert header_texts == {"A", "B", "C", "D", "data"}, [c.text for c in cells]

    # Body must remain as the single full-width monster cell, NOT five binned
    # cells.  The bug would have split the paragraph's words into separate
    # narrow cells holding sentence fragments.
    forum_cells = [c for c in cells if "LTSPW" in c.text]
    assert len(forum_cells) == 1, [c.text for c in cells]
    monster = forum_cells[0]
    # Monster spans the full header width (within snap tolerance), proving
    # no re-binned per-column cells replaced it.
    assert monster.bbox.x1 - monster.bbox.x0 > 350.0, monster.bbox

    # Cross-check: no cell carries the bbox of a single header column with
    # a multi-word prose fragment as text.  (The bug emitted such cells.)
    for c in cells:
        if c.text in header_texts:
            continue
        if c.bbox.x1 - c.bbox.x0 < 100.0 and len(c.text.split()) >= 2:
            raise AssertionError(
                f"prose got re-binned into a header column: {c!r}"
            )


def test_text_strategy_rejects_table_that_slices_words():
    """Fixture 28: title cover page with three meta lines.

    pdfplumber's text strategy projects vertical edges from the wide
    inter-word gaps in the large-font cover heading across every other
    line on the page.  Without the word-boundary guard the body words
    below get sliced mid-syllable (e.g. "Phoenix" → "P" + "hoeni" + "x",
    "January" → "Jan" + "uary"), surfacing as a spurious table with
    mid-word fragment cells.

    The guard inside :func:`_text_cells` rejects any candidate table
    whose column edges slice through one or more page words.  After
    rejection, no text-strategy cell remains on this page — the cover
    parses as three headings and three paragraphs, with no table.
    """
    pdf_path = Path("tests/golden/synthetic/28_title_cover_with_meta_lines/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)

    # Cover page is borderless prose-only: no detector should fire.
    assert cells == [], [
        (c.source, c.text, c.bbox.x0, c.bbox.x1) for c in cells
    ]

def test_detect_cells_emits_3x2_grid_on_headerless_keyvalue_table():
    """Fixture 29 (headerless key-value listing): the new
    :func:`pdf_parser.stages.table_validation.validate` predicate must
    accept this borderless 3-row x 2-col table even though no row carries
    a header signature (same font / size across all rows).

    Proves the validator's headerless acceptance path is live and that
    real key-value listings clear the higher 0.75 homogeneity bar — col 0
    is uniformly ``short`` labels, col 1 is uniformly ``currency`` values.
    The gutter detector is the path under test (line / frame yield nothing
    on a borderless table).
    """
    pdf_path = Path("tests/golden/synthetic/29_headerless_keyvalue_table/source.pdf")
    with pdfplumber.open(str(pdf_path)) as pdf:
        page = pdf.pages[0]
        cells = detect_cells(page, page_index=0)
    texts = {c.text for c in cells}
    # 3 rows x 2 cols = 6 cells, all from the gutter detector.
    assert len(cells) == 6, [c.text for c in cells]
    assert all(c.source == "gutter" for c in cells)
    assert {"Revenue", "Expenses", "Net", "$1000", "$800", "$200"} == texts
