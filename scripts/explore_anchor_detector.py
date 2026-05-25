"""Prototype: column-anchor table detector + side-by-side comparison.

Standalone exploration. Imports `detect_tables` read-only for the baseline;
writes nothing under `pdf_parser/`, `tests/fixtures/`, or `tests/golden/`.

Run::

    .venv/bin/python scripts/explore_anchor_detector.py

For each fixture (and an adversarial borderless-with-long-cells PDF generated
in /tmp), prints:

  - what the current `detect_tables` stack returns
  - what the column-anchor prototype returns
  - whether they agree, complement, or disagree

Goal: show whether the anchor signal can replace the brittle
`_MAX_CELL_TEXT_CHARS = 7` filter and slot cleanly into an evidence-accumulation
adjudicator.
"""

from __future__ import annotations

import os
import re
import statistics
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Deterministic reportlab output for the adversarial fixture.
os.environ.setdefault("SOURCE_DATE_EPOCH", "1700000000")

import pdfplumber
from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Read-only import. The other agent is editing this module, but the public
# signature is stable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pdf_parser.stages.detect_tables import detect_tables  # noqa: E402


# ---------------------------------------------------------------------------
# Candidate model (lightweight; only for this experiment).
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    page_index: int
    x0: float
    y0: float
    x1: float
    y1: float
    n_rows: int
    n_cols: int
    grid: list[list[str]]
    signals: dict[str, float]
    score: float
    source: str = "anchor"

    def bbox_tuple(self) -> tuple[float, float, float, float]:
        return (self.x0, self.y0, self.x1, self.y1)


# ---------------------------------------------------------------------------
# Column-anchor detector.
# ---------------------------------------------------------------------------

_LINE_GROUP_TOL = 2.0      # pt; words within this y-span are on the same line
_GAP_THRESHOLD_PT = 8.0    # pt; horizontal gap above this splits a line into cells
_ANCHOR_TOL_PT = 4.0       # pt; cell.x0 within this bucket = same column anchor
_MIN_RUN_LINES = 3         # min consecutive lines sharing a signature → candidate
_MIN_COLS = 2              # signature must have ≥ this many cells (= ≥1 gap)

_NUMERIC_RE = re.compile(r"^[\s\d.,$%()+\-/]+$")


def _group_words_into_lines(words: list[dict]) -> list[list[dict]]:
    """y-bucket words into lines. Same idea as the production helper but
    reimplemented locally so the experiment doesn't reach into the WIP module."""
    if not words:
        return []
    words = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[list[dict]] = []
    for w in words:
        cy = (w["top"] + w["bottom"]) / 2
        if lines:
            last_cy = (lines[-1][0]["top"] + lines[-1][0]["bottom"]) / 2
            if abs(cy - last_cy) < _LINE_GROUP_TOL:
                lines[-1].append(w)
                continue
        lines.append([w])
    return lines


def _line_to_cells(line_words: list[dict]) -> list[tuple[float, float, str]]:
    """Gap-cluster a single line into (cell_x0, cell_x1, text) triples.

    Words separated by a horizontal gap > _GAP_THRESHOLD_PT start a new cell.
    """
    if not line_words:
        return []
    sorted_words = sorted(line_words, key=lambda w: w["x0"])
    cells: list[list[dict]] = [[sorted_words[0]]]
    for w in sorted_words[1:]:
        prev_x1 = max(cw["x1"] for cw in cells[-1])
        if w["x0"] - prev_x1 > _GAP_THRESHOLD_PT:
            cells.append([w])
        else:
            cells[-1].append(w)
    return [
        (
            min(w["x0"] for w in c),
            max(w["x1"] for w in c),
            " ".join(w["text"] for w in c),
        )
        for c in cells
    ]


def _signature(cells: list[tuple[float, float, str]]) -> tuple[int, ...]:
    """Anchor signature: tuple of bucketed cell.x0 values."""
    return tuple(int(round(c[0] / _ANCHOR_TOL_PT)) for c in cells)


def _numeric_ratio(grid: list[list[str]]) -> float:
    cells = [c.strip() for row in grid for c in row if c.strip()]
    if not cells:
        return 0.0
    return sum(1 for c in cells if _NUMERIC_RE.match(c)) / len(cells)


def _spacing_regularity(line_tops: list[float]) -> float:
    """1 - CV of line-to-line gaps, clamped to [0, 1]. Higher = more regular."""
    if len(line_tops) < 3:
        return 1.0
    gaps = [line_tops[i + 1] - line_tops[i] for i in range(len(line_tops) - 1)]
    mean = statistics.fmean(gaps)
    if mean <= 0:
        return 0.0
    std = statistics.pstdev(gaps)
    cv = std / mean
    return max(0.0, min(1.0, 1.0 - cv))


def _anchor_stability(runs_cells: list[list[tuple[float, float, str]]]) -> float:
    """Mean per-column x0 std-dev across rows, mapped to [0, 1].

    0pt drift → 1.0; 4pt drift → 0.0 (linear).
    """
    if len(runs_cells) < 2:
        return 1.0
    n_cols = len(runs_cells[0])
    drifts: list[float] = []
    for col in range(n_cols):
        xs = [row[col][0] for row in runs_cells]
        if len(xs) >= 2:
            drifts.append(statistics.pstdev(xs))
    if not drifts:
        return 1.0
    avg_drift = statistics.fmean(drifts)
    return max(0.0, min(1.0, 1.0 - avg_drift / _ANCHOR_TOL_PT))

def _fill_ratio_vs_slots(
    cells_per_row: list[list[tuple[float, float, str]]]
) -> float:
    """Average per-cell ``text_width / slot_width``.

    Slot widths come from column **anchors** (next anchor - this anchor), not
    from observed text spans. That distinguishes "column is mostly whitespace
    with short text in it" (table) from "text wraps right to the next column"
    (prose). The last column's slot uses the max observed ``x1`` across its
    rows as its right edge — there's no next anchor to bound it.
    """
    if not cells_per_row or len(cells_per_row[0]) < 2:
        return 0.0
    n_cols = len(cells_per_row[0])
    col_starts = [
        statistics.fmean(row[c][0] for row in cells_per_row) for c in range(n_cols)
    ]
    slot_widths: list[float] = []
    for c in range(n_cols - 1):
        slot_widths.append(col_starts[c + 1] - col_starts[c])
    last_right = max(row[-1][1] for row in cells_per_row)
    slot_widths.append(last_right - col_starts[-1])

    fills: list[float] = []
    for row in cells_per_row:
        for c in range(n_cols):
            slot = slot_widths[c]
            if slot <= 0:
                continue
            cell_w = row[c][1] - row[c][0]
            fills.append(min(1.0, cell_w / slot))
    if not fills:
        return 0.0
    return statistics.fmean(fills)


def column_anchor_detector(
    page: pdfplumber.page.Page, page_index: int
) -> list[Candidate]:
    """Find runs of ≥ _MIN_RUN_LINES consecutive lines sharing a column signature.

    Each run becomes a Candidate. No filtering by score here — caller decides.
    """
    words = page.extract_words(use_text_flow=True)
    lines = _group_words_into_lines(words)

    # Decorate each line with its cells + signature.
    decorated = []
    for line in lines:
        cells = _line_to_cells(line)
        sig = _signature(cells) if len(cells) >= _MIN_COLS else None
        top = min(w["top"] for w in line)
        bottom = max(w["bottom"] for w in line)
        decorated.append((line, cells, sig, top, bottom))

    candidates: list[Candidate] = []
    i = 0
    while i < len(decorated):
        sig = decorated[i][2]
        if sig is None:
            i += 1
            continue
        # Greedy run: extend while signature matches.
        j = i + 1
        while j < len(decorated) and decorated[j][2] == sig:
            j += 1
        run = decorated[i:j]
        if len(run) >= _MIN_RUN_LINES:
            cells_per_row = [r[1] for r in run]
            grid = [[text for (_, _, text) in cells] for cells in cells_per_row]
            line_tops = [r[3] for r in run]

            xs0 = [c[0] for row in cells_per_row for c in row]
            xs1 = [c[1] for row in cells_per_row for c in row]
            tops = [r[3] for r in run]
            bots = [r[4] for r in run]

            n_rows = len(run)
            n_cols = len(sig)

            rows_norm = min(n_rows / 5.0, 1.0)
            cols_norm = min(n_cols / 3.0, 1.0)
            stability = _anchor_stability(cells_per_row)
            spacing = _spacing_regularity(line_tops)
            numeric = _numeric_ratio(grid)
            fill = _fill_ratio_vs_slots(cells_per_row)
            # Anti-signal: prose fills its column edge-to-edge; tables don't.
            # Knee at 0.65 (top of observed table range); full at 0.85.
            fill_penalty = max(0.0, min(1.0, (fill - 0.65) * 5.0))

            score_pre = (
                0.25 * rows_norm
                + 0.20 * cols_norm
                + 0.25 * stability
                + 0.15 * spacing
                + 0.15 * numeric
            )
            score = max(0.0, score_pre - 0.40 * fill_penalty)

            candidates.append(
                Candidate(
                    page_index=page_index,
                    x0=min(xs0),
                    y0=min(tops),
                    x1=max(xs1),
                    y1=max(bots),
                    n_rows=n_rows,
                    n_cols=n_cols,
                    grid=grid,
                    signals={
                        "rows_norm": round(rows_norm, 3),
                        "cols_norm": round(cols_norm, 3),
                        "stability": round(stability, 3),
                        "spacing": round(spacing, 3),
                        "numeric": round(numeric, 3),
                        "fill": round(fill, 3),
                        "fill_penalty": round(fill_penalty, 3),
                    },
                    score=round(score, 3),
                )
            )
        i = j
    return candidates


# ---------------------------------------------------------------------------
# Adversarial fixture: borderless table with long-text cells (avg cell > 7 chars).
# Mirrors fixture 14's shape, but designed to defeat _MAX_CELL_TEXT_CHARS=7.
# ---------------------------------------------------------------------------

def build_adversarial_long_borderless(out: Path) -> None:
    doc = SimpleDocTemplate(str(out), pagesize=LETTER)
    s = getSampleStyleSheet()
    data = [
        ["Customer Name",        "Order Description",            "Status Notes"],
        ["Acme Corporation",     "Annual subscription renewal",  "Paid in Q3"],
        ["Globex Industries",    "Hardware shipment delayed",    "Pending review"],
        ["Initech Holdings",     "Consulting engagement closed", "Invoice sent"],
        ["Umbrella Logistics",   "Routine maintenance contract", "Awaiting reply"],
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
        Paragraph("Adversarial: Long-Cell Borderless Table", s["Heading1"]),
        Spacer(1, 12),
        t,
    ]
    doc.build(story)


# ---------------------------------------------------------------------------
# Comparison runner.
# ---------------------------------------------------------------------------

@dataclass
class RegionView:
    """Flattened view of a TableRegion or Candidate for printing."""
    source: str
    page: int
    bbox: tuple[float, float, float, float]
    n_rows: int
    n_cols: int
    header: list[str]
    extra: str = ""


def _bbox_str(b: tuple[float, float, float, float]) -> str:
    return f"({b[0]:6.1f}, {b[1]:6.1f}, {b[2]:6.1f}, {b[3]:6.1f})"


def _overlap_iou(a: tuple, b: tuple) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    a_area = (ax1 - ax0) * (ay1 - ay0)
    b_area = (bx1 - bx0) * (by1 - by0)
    union = a_area + b_area - inter
    return inter / union if union > 0 else 0.0


def run_fixture(name: str, pdf_path: Path) -> None:
    print(f"\n=== {name} ===")
    if not pdf_path.exists():
        print(f"  [skip] {pdf_path} not found")
        return

    # Current detector.
    current_views: list[RegionView] = []
    try:
        regions = detect_tables(pdf_path=pdf_path)
        for r in regions:
            grid = r.grid
            header = grid[0] if grid else []
            current_views.append(
                RegionView(
                    source="current",
                    page=r.page_index,
                    bbox=(r.bbox.x0, r.bbox.y0, r.bbox.x1, r.bbox.y1),
                    n_rows=len(grid),
                    n_cols=len(grid[0]) if grid else 0,
                    header=[(c or "")[:20] for c in header],
                    extra="redistributed" if r.redistributed else "",
                )
            )
    except Exception as exc:  # WIP module could theoretically be mid-edit
        print(f"  [current detector raised: {exc!r}]")

    # Anchor detector.
    anchor_candidates: list[Candidate] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for idx, page in enumerate(pdf.pages):
            anchor_candidates.extend(column_anchor_detector(page, idx))
    anchor_views = [
        RegionView(
            source="anchor",
            page=c.page_index,
            bbox=c.bbox_tuple(),
            n_rows=c.n_rows,
            n_cols=c.n_cols,
            header=[h[:20] for h in c.grid[0]],
            extra=f"score={c.score:.2f} sig={c.signals}",
        )
        for c in anchor_candidates
    ]

    print(f"  Current: {len(current_views)} table(s)")
    for v in current_views:
        tag = f" [{v.extra}]" if v.extra else ""
        print(f"    p{v.page} bbox={_bbox_str(v.bbox)}  {v.n_rows}x{v.n_cols}{tag}")
        print(f"           header={v.header}")

    print(f"  Anchor:  {len(anchor_views)} candidate(s)")
    for v in anchor_views:
        print(f"    p{v.page} bbox={_bbox_str(v.bbox)}  {v.n_rows}x{v.n_cols}  {v.extra}")
        print(f"           header={v.header}")

    # Cross-reference: for each anchor candidate, find IoU with current regions.
    if anchor_views and current_views:
        print("  Overlap matrix (IoU anchor↔current, same page):")
        for ai, av in enumerate(anchor_views):
            for ci, cv in enumerate(current_views):
                if av.page != cv.page:
                    continue
                iou = _overlap_iou(av.bbox, cv.bbox)
                if iou > 0.05:
                    print(f"    anchor[{ai}] ↔ current[{ci}]: IoU={iou:.2f}")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

FIXTURES = [
    ("02_nested_table",                  "nested gridded — anchor must not duplicate"),
    ("14_borderless_table",              "borderless short cells — text-strategy works today"),
    ("18_ruled_header_open_body",        "header ruled only — current uses redistribution"),
    ("21_vertical_merge_invisible_lines", "overdraw cleanup — gridded, anchor sanity"),
    ("22_text_between_adjacent_tables",  "adjacent nested tables (other agent's new fixture)"),
    ("15_multicolumn_text",              "NEGATIVE: 2-col prose — anchor must NOT fire"),
]

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "golden" / "synthetic"


def main() -> None:
    for fname, _desc in FIXTURES:
        run_fixture(fname, FIXTURE_ROOT / fname / "source.pdf")

    # Adversarial PDF: generated in tempdir, not committed.
    with tempfile.TemporaryDirectory() as td:
        adversarial = Path(td) / "adversarial_long_borderless.pdf"
        build_adversarial_long_borderless(adversarial)
        run_fixture("ADVERSARIAL_long_borderless", adversarial)


if __name__ == "__main__":
    main()
