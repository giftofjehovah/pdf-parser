"""Single-source-of-truth predicate: does a candidate (col_ranges + grid +
words + chars) look like a real table, or like prose that incidentally
aligns?  Called from every cell detector inside :mod:`detect_cells`.

Four signals combine in :meth:`TableEvidence.is_likely_table`:

* :func:`_words_respect_columns` — hard requirement.  Real table columns
  separate, never split, words.
* :func:`_column_type_homogeneity` — soft.  A column's non-empty cells
  share a kind (numeric / currency / short label) in ≥ 80 % of rows.
* :func:`_has_header_row` — positive evidence used only to lower the
  homogeneity bar from 0.75 to 0.60.  Headerless tables (financial
  statements, key-value listings) still clear the higher bar.
* :func:`_avg_cell_chars` — length-variance guard.  Multi-column body
  prose binned by x-midpoint produces long per-cell wrap fragments;
  above 30 chars/cell the candidate is rejected regardless of kind
  homogeneity (preserves the legacy ``_GUTTER_MAX_AVG_CELL_CHARS``
  discriminator).

Only :func:`validate` and :class:`TableEvidence` are intended for
detector use; private helpers are exposed for direct unit testing.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass


_WORD_BOUNDARY_TOL                  = 1.5
_HOMOGENEITY_KIND_THRESHOLD         = 0.80
_HOMOGENEITY_HEADERED_THRESHOLD     = 0.60
_HOMOGENEITY_HEADERLESS_THRESHOLD   = 0.75
_HEADER_SIZE_DIFF_PT                = 0.5
# Avg non-empty cell length above which the candidate looks like
# multi-column body prose (long wrapped fragments) rather than tabular
# data.  Matched to the legacy ``_GUTTER_MAX_AVG_CELL_CHARS = 30``
# discriminator with a small headroom so wide-but-data borderless
# tables (14b, 14c) still clear the bar.
_MAX_AVG_CELL_CHARS                 = 30.0
_NUMERIC_RE                         = re.compile(r"-?[\d,.\s]+")
_BOLD_SUFFIXES                      = ("-Bold", "-Bd", ",Bold", " Bold")


def _cell_kind(c: str) -> str:
    """Classify a stripped cell into one of {numeric, currency, short, prose, empty}.

    Order matters: ``currency`` is checked before ``numeric`` so ``$1.00``
    and ``95%`` don't fall through.  ``prose`` covers ≥ 3 whitespace-split
    tokens; the remainder is ``short``.
    """
    c = c.strip()
    if not c:
        return "empty"
    if c.startswith("$") or c.endswith("%"):
        return "currency"
    if _NUMERIC_RE.fullmatch(c):
        return "numeric"
    if len(c.split()) >= 3:
        return "prose"
    return "short"


def _words_respect_columns(
    words: list[dict],
    col_ranges: list[tuple[float, float]],
    tol: float = _WORD_BOUNDARY_TOL,
) -> bool:
    """True when every word fits entirely inside some column expanded by ``tol``.

    A real table's columns separate, never split, words.  A column edge
    that slices through one or more page words indicates the candidate
    is body prose with projected boundaries (fixture 28 cover page) or
    a multi-column layout, not a table.
    """
    if not col_ranges:
        return False
    for w in words:
        wx0, wx1 = w["x0"], w["x1"]
        if not any(cx0 - tol <= wx0 and wx1 <= cx1 + tol for cx0, cx1 in col_ranges):
            return False
    return True


def _avg_cell_chars(grid: list[list[str]]) -> float:
    """Average length of non-empty stripped cells across ``grid``.

    Returns ``0.0`` for empty input.  Used as a soft length-variance
    guard against wrapped multi-column body prose, where binning by
    x-midpoint produces long per-cell fragments.
    """
    lengths = [len(c.strip()) for row in grid for c in row if c.strip()]
    return sum(lengths) / len(lengths) if lengths else 0.0


def _column_type_homogeneity(grid: list[list[str]], skip_first: bool = False) -> float:
    """Fraction of columns whose non-empty cells share a kind in ≥ 80 % of rows.

    ``skip_first`` excludes ``grid[0]`` when the caller has detected a
    header row whose kind typically differs from body kinds (header
    labels vs numeric data).  All-empty columns are excluded from both
    numerator and denominator.
    """
    rows = grid[1:] if skip_first and len(grid) >= 2 else grid
    if not rows:
        return 0.0
    n_cols = max((len(r) for r in rows), default=0)
    if n_cols == 0:
        return 0.0
    homogeneous = 0
    considered = 0
    for j in range(n_cols):
        non_empty = [
            r[j].strip() for r in rows
            if j < len(r) and r[j].strip()
        ]
        if not non_empty:
            continue
        considered += 1
        kinds = [_cell_kind(c) for c in non_empty]
        top_count = Counter(kinds).most_common(1)[0][1]
        if top_count / len(non_empty) >= _HOMOGENEITY_KIND_THRESHOLD:
            homogeneous += 1
    if considered == 0:
        return 0.0
    return homogeneous / considered


def _has_header_row(chars_by_row: list[list[dict]] | None) -> bool:
    """True when row-0 chars differ from row-1+ chars on font, size, or bold.

    Compares dominant ``fontname`` and mean ``size`` from pdfplumber char
    dicts; any ``_BOLD_SUFFIXES`` substring on row 0's dominant fontname
    counts regardless of row 1+.  Returns ``False`` when ``chars_by_row``
    cannot supply at least two rows to compare.
    """
    if not chars_by_row or len(chars_by_row) < 2:
        return False
    head = chars_by_row[0]
    body = [c for row in chars_by_row[1:] for c in row]
    if not head or not body:
        return False

    head_font = Counter(c.get("fontname", "") for c in head).most_common(1)[0][0]
    body_font = Counter(c.get("fontname", "") for c in body).most_common(1)[0][0]
    if head_font and any(s in head_font for s in _BOLD_SUFFIXES):
        return True
    if head_font and body_font and head_font != body_font:
        return True

    def _mean_size(chars: list[dict]) -> float:
        sizes = [c.get("size", 0.0) for c in chars if c.get("size")]
        return sum(sizes) / len(sizes) if sizes else 0.0

    if abs(_mean_size(head) - _mean_size(body)) > _HEADER_SIZE_DIFF_PT:
        return True
    return False


@dataclass(frozen=True)
class TableEvidence:
    """Aggregated table-shape signals.

    Detectors call :func:`validate` then :meth:`is_likely_table`; the
    dataclass is also handy for diagnostic dumps during gate triage.
    """

    words_respect_columns: bool
    column_homogeneity: float
    has_header_row: bool
    avg_cell_chars: float
    n_rows: int
    n_cols: int

    def is_likely_table(self) -> bool:
        if not self.words_respect_columns:
            return False
        if self.n_rows < 2 or self.n_cols < 2:
            return False
        if self.avg_cell_chars > _MAX_AVG_CELL_CHARS:
            return False
        threshold = (
            _HOMOGENEITY_HEADERED_THRESHOLD
            if self.has_header_row
            else _HOMOGENEITY_HEADERLESS_THRESHOLD
        )
        return self.column_homogeneity >= threshold


def validate(
    words: list[dict],
    col_ranges: list[tuple[float, float]],
    grid: list[list[str]],
    chars_by_row: list[list[dict]] | None = None,
) -> TableEvidence:
    """Compute :class:`TableEvidence` for one detector's candidate.

    * ``words`` — page words whose y-extent overlaps the candidate.
      Each is a pdfplumber word-dict with ``x0`` / ``x1`` keys.
    * ``col_ranges`` — column x-ranges as ``(x0, x1)`` pairs, left to right.
    * ``grid`` — text grid (one row per visual line).  Place any header
      row at ``grid[0]`` and pass aligned ``chars_by_row`` so the
      homogeneity calc excludes it from the per-column kind tally.
    * ``chars_by_row`` — optional pdfplumber char dicts per grid row,
      used only for header detection.  ``None`` → ``has_header_row=False``.
    """
    has_header = _has_header_row(chars_by_row)
    return TableEvidence(
        words_respect_columns=_words_respect_columns(words, col_ranges),
        column_homogeneity=_column_type_homogeneity(grid, skip_first=has_header),
        has_header_row=has_header,
        avg_cell_chars=_avg_cell_chars(grid),
        n_rows=len(grid),
        n_cols=max((len(r) for r in grid), default=0),
    )
