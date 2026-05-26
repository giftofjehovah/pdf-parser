"""Direct unit tests on :func:`pdf_parser.stages.table_validation.validate`.

These tests exercise each signal in isolation by passing minimal inputs.
End-to-end coverage of the three detectors lives in ``test_detect_cells.py``.
"""
from __future__ import annotations

from pdf_parser.stages.table_validation import (
    _cell_kind,
    TableEvidence,
    validate,
)


def _w(text: str, x0: float, x1: float, y: float = 100.0) -> dict:
    """Minimal pdfplumber-style word dict."""
    return {"text": text, "x0": x0, "x1": x1, "top": y, "bottom": y + 10}


def test_validate_rejects_when_word_crosses_boundary():
    """Signal 1 (hard): a word straddling a column edge fails the predicate.

    Cover-page-style projected boundaries (fixture 28) land between the
    syllables of words below; reject as soon as any word fails to fit
    in a single column expanded by the boundary tolerance.
    """
    cols = [(0.0, 50.0), (50.0, 100.0)]
    grid = [["Alice", "95"], ["Bob", "82"]]
    sliced = [_w("AliceX", 40.0, 60.0)]  # crosses x=50 boundary
    ev = validate(words=sliced, col_ranges=cols, grid=grid)
    assert ev.words_respect_columns is False
    assert ev.is_likely_table() is False


def test_validate_rejects_low_homogeneity_no_header():
    """Signal 2 (soft, headerless): mixed-kind columns fail the 0.75 bar.

    No two columns share a kind in ≥80% of rows; ``has_header_row=False``
    keeps the threshold high at 0.75.
    """
    cols = [(0.0, 50.0), (50.0, 100.0), (100.0, 150.0)]
    grid = [
        ["alpha", "1234", "longer fragment here"],
        ["beta this is", "ninety", "5"],
        ["1234", "two more words", "another"],
    ]
    ev = validate(words=[], col_ranges=cols, grid=grid, chars_by_row=None)
    assert ev.has_header_row is False
    assert ev.column_homogeneity < 0.75
    assert ev.is_likely_table() is False


def test_validate_accepts_lower_homogeneity_with_header():
    """Signal 3 (soft modulator): header presence drops the bar 0.75 → 0.60.

    Same grid; the only thing that changes is ``chars_by_row[0]`` having
    a bold fontname (header signature).  ``column_homogeneity`` recomputes
    on body rows only (``skip_first=True``), and the lowered threshold
    flips the verdict.
    """
    cols = [(0.0, 50.0), (50.0, 100.0), (100.0, 150.0)]
    # Header row + 5 body rows.
    # Body: col 0 = 5 short ✓; col 1 = 3 short ("high","low","mid") + 2
    # numeric → 60 % top-kind ratio, NOT ✗; col 2 = 5 short ✓.
    # → 2/3 cols homogeneous = 0.667.
    grid = [
        ["Name",  "Score", "Status"],
        ["Alice", "95.5",  "active"],
        ["Bob",   "high",  "active"],
        ["Carol", "91",    "active"],
        ["Dave",  "low",   "active"],
        ["Eve",   "mid",   "active"],
    ]
    head = [{"fontname": "Helvetica-Bold", "size": 12.0}]
    body = [{"fontname": "Helvetica",      "size": 10.0}]

    ev_none = validate(words=[], col_ranges=cols, grid=grid, chars_by_row=None)
    assert ev_none.has_header_row is False
    assert abs(ev_none.column_homogeneity - 2 / 3) < 1e-9
    assert ev_none.is_likely_table() is False

    ev_hdr = validate(
        words=[], col_ranges=cols, grid=grid,
        chars_by_row=[head, body, body, body, body, body],
    )
    assert ev_hdr.has_header_row is True
    assert abs(ev_hdr.column_homogeneity - 2 / 3) < 1e-9
    assert ev_hdr.is_likely_table() is True


def test_validate_requires_min_2x2_shape():
    """A degenerate 1×N or N×1 candidate is rejected before any other gate."""
    cols2 = [(0.0, 50.0), (50.0, 100.0)]
    # 1 row × 2 cols.
    ev = validate(words=[], col_ranges=cols2, grid=[["a", "b"]])
    assert ev.n_rows == 1 and ev.is_likely_table() is False
    # 2 rows × 1 col.
    ev = validate(words=[], col_ranges=[(0.0, 50.0)], grid=[["a"], ["b"]])
    assert ev.n_cols == 1 and ev.is_likely_table() is False
    # Empty grid.
    ev = validate(words=[], col_ranges=cols2, grid=[])
    assert ev.n_rows == 0 and ev.is_likely_table() is False


def test_validate_classifies_numeric_currency_short_prose_correctly():
    """Direct unit test on the kind classifier the homogeneity check uses."""
    assert _cell_kind("123")                              == "numeric"
    assert _cell_kind("1,234.56")                         == "numeric"
    assert _cell_kind("-42")                              == "numeric"
    assert _cell_kind("12 34")                            == "numeric"  # whitespace-grouped digits
    assert _cell_kind("$1.00")                            == "currency"
    assert _cell_kind("95%")                              == "currency"
    assert _cell_kind("Alice")                            == "short"
    assert _cell_kind("Customer Name")                    == "short"
    assert _cell_kind("this is a longer prose fragment")  == "prose"
    assert _cell_kind("")                                 == "empty"
    assert _cell_kind("   ")                              == "empty"


def test_validate_rejects_oversized_avg_cell_length():
    """Length-variance guard: multi-column body prose binned by x-midpoint
    yields long per-cell strings; reject above 30 chars/cell average.

    Mirrors the rejection the legacy ``_GUTTER_MAX_AVG_CELL_CHARS = 30``
    encoded against fixture 15-style multi-column paragraph layouts.
    """
    cols = [(0.0, 50.0), (50.0, 100.0)]
    long_fragment_a = "Lorem ipsum dolor sit amet, consectetur adipiscing"
    long_fragment_b = "sed do eiusmod tempor incididunt ut labore et dolore"
    grid = [
        [long_fragment_a, long_fragment_b],
        [long_fragment_a, long_fragment_b],
        [long_fragment_a, long_fragment_b],
    ]
    ev = validate(words=[], col_ranges=cols, grid=grid, chars_by_row=None)
    assert ev.avg_cell_chars > 30.0
    assert ev.is_likely_table() is False
