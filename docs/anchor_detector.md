# Column-Anchor Table Detector

**Module:** `pdf_parser/stages/detect_tables_anchor.py`
**Status:** Experimental — opt-in via `--table-detector experimental`
**Default path:** Unaffected (`--table-detector legacy`).

## 1. Purpose

The legacy table cascade (`detect_tables` → `extract_tables`) recovers nearly
every table shape we care about, but it has one structural blind spot:

> **Borderless tables whose cell text is too long for the legacy heuristic to
> recognize as tabular data.**

The legacy text-strategy fallback uses two signals to reject prose that
pdfplumber mis-detects as a table:

1. `_MAX_CELL_TEXT_CHARS = 7` — avg cell length above 7 chars → prose.
2. `_MAX_LOWERCASE_START_RATIO = 0.40` — most cells start with lowercase
   letters → mid-word splits → prose.

Together those reject most prose, but they also reject any **legitimate**
borderless table whose cell text averages more than ~7 characters
(e.g., `"Annual subscription renewal"` / `"Awaiting reply"` style status
columns). The anchor detector is the specialist that recovers those.

It is **additive**: it runs after the legacy cascade has produced its
`list[DocNode]` of tables, looks for additional candidates, and emits any
that survive scoring + overlap checks. On every existing synthetic fixture
(01 – 23) it contributes zero new tables — by design, since the legacy
cascade already handles those shapes.

## 2. How to enable it

### CLI

```
pdf-parser parse <path> --table-detector experimental
```

### Python

```python
from pdf_parser.pipeline import parse
tree = parse(pdf_path, table_detector="experimental")
```

The flag accepts `"legacy"` (default) or `"experimental"`. Any other value
raises `ValueError`.

## 3. Algorithm

### 3.1 Per-line cell extraction

For every page, the detector calls `page.extract_words(use_text_flow=True)`
and groups words into lines by y-center (2 pt buckets). Each line is then
gap-clustered into cells: consecutive words whose horizontal gap exceeds
`_GAP_THRESHOLD_PT = 8.0` start a new cell. A line with fewer than
`_MIN_COLS = 2` cells contributes no signature and cannot start a candidate.

### 3.2 Column signature

Each line's signature is a bucketed left-edge tuple:

```python
def _signature(cells):
    return tuple(int(round(cell.x0 / _ANCHOR_TOL_PT)) for cell in cells)
```

with `_ANCHOR_TOL_PT = 4.0`. Two lines whose cell starts agree within 4 pt
share a signature.

### 3.3 Run detection

The detector scans lines top-to-bottom and emits one candidate per maximal
run of `≥ _MIN_RUN_LINES = 3` consecutive lines that share a signature. A
signature break terminates the current run.

### 3.4 List-shape pre-filter

Before scoring, candidates whose first column collapses to a single unique
value are rejected (`_is_list_shape`). This catches bulleted and numbered
lists — `•`, `(cid:127)`, `*`, `-`, `▪` — without enumerating glyphs.

### 3.5 Scoring

A surviving candidate is scored on five signals. Weights sum to 1.0 before
the fill penalty.

| Signal | Weight | What it measures |
|---|---|---|
| `rows_norm` | 0.25 | `min(n_rows / 5, 1.0)` — bias toward longer runs |
| `cols_norm` | 0.20 | `min(n_cols / 3, 1.0)` — bias toward wider grids |
| `stability` | 0.25 | `1 − mean(per-column x0 stdev) / _ANCHOR_TOL_PT` |
| `spacing` | 0.15 | `1 − coefficient_of_variation(line_top_gaps)` |
| `numeric` | 0.15 | fraction of non-empty cells matching `^[\s\d.,$%()+\-/]*\d[…]*$` |

The numeric regex requires at least one digit; without that requirement
single-token cells like `.`, `,`, `$`, `()` would inflate the signal.

A **fill penalty** is then subtracted:

```
fill_penalty = clip((fill - _FILL_KNEE) * _FILL_RAMP, 0, 1)
score       = score_pre - _W_FILL_PENALTY * fill_penalty
```

with `_FILL_KNEE = 0.65`, `_FILL_RAMP = 5.0`, `_W_FILL_PENALTY = 0.40`.

`fill` is the average per-cell `text_width / allocated_slot_width` where
slot widths come from **column anchors** (gaps between successive
`mean(cell.x0)` values), not observed text spans. This distinguishes
"column is mostly whitespace with short text" (table) from "text wraps
right to the next column boundary" (prose). The last column borrows the
max observed `x1` as its right edge.

Candidates below `MIN_SCORE = 0.65` are dropped. The calibration run
recorded the worst real-table score at 0.83 and the worst prose
false-positive at 0.54, so 0.65 leaves ~0.18 headroom on both sides.

### 3.6 Containment check vs. legacy tables

Surviving candidates are filtered against every legacy table on the same
page. The check is **symmetric containment**, not IoU:

```python
def _overlaps_legacy(c, legacy_tables):
    for lt in legacy_tables:
        for lb in lt.bbox (or [lt.bbox]):
            if _containment_of_anchor(c.bbox, lb) > 0.50: return True
            if _containment_of_anchor(lb, c.bbox) > 0.50: return True
    return False
```

`_containment_of_anchor(a, b)` returns `intersection_area / area(a)`.
The two directions catch two distinct shapes:

* **`anchor ⊂ legacy`** — the anchor candidate is a sub-region of a known
  legacy table (e.g. the OpEx rows inside a full P&L statement). Without
  this, IoU shrinks toward zero whenever the legacy box is much larger
  than the candidate, and the candidate slips through as a duplicate.
* **`legacy ⊂ anchor`** — the anchor candidate fully encloses a known
  legacy table (borderless-outer + bordered-inner nesting). Without this,
  the inner table's text gets flattened into one of the anchor's cells
  and ends up duplicated in the tree.

### 3.7 DocNode emission

Each survivor becomes a flat `table → row → cell` subtree:

* Cell bbox = the cell's gap-clustered `(x0, top, x1, bottom)`.
* Row bbox = the union of its cell bboxes (computed per-row; not the
  table bbox).
* Table bbox = the union of all cell bboxes.
* `attrs.anchor_score` and `attrs.anchor_signals` are preserved on every
  emitted table for post-hoc inspection.
* `provenance.extractor = "anchor"` distinguishes these from legacy
  pdfplumber-emitted tables in the tree.

The augmenter appends survivors to the legacy list in document reading
order `(page, then y0)`. Legacy tables retain their original order.

## 4. Edge cases

### 4.1 True positives — what the detector recovers

| Shape | Why legacy misses | Anchor signal that recovers |
|---|---|---|
| Borderless table with long-text cells (`"Annual subscription renewal"`) | Avg cell > 7 chars → text-strategy result rejected | Column anchors stable across rows even when text is long |
| Borderless table with descriptive headers (`"Order Description"`) | Headers push avg above 7 | Same |
| Multi-row data block with consistent column starts, no rules | Line strategy finds nothing; text strategy rejected | Run of ≥3 lines sharing a signature |

### 4.2 False positives — what gets rejected

| Trap | Mechanism |
|---|---|
| Bulleted lists (`•`, `(cid:127)`, `*`, `-`, `▪` + prose) | `_is_list_shape` rejects first column = single unique value |
| Multi-column body text (newspaper layout) | `fill_ratio_vs_slots` penalty: prose fills its column edge-to-edge |
| Punctuation-heavy rows (solo `.`, `,`, `$`, `()`) | `_NUMERIC_RE` requires at least one digit |
| Column signatures that drift across rows (wrapping prose) | `_anchor_stability` ≈ 0 when stdev approaches `_ANCHOR_TOL_PT` |
| Irregular line spacing | `_spacing_regularity` low → score drops |
| Runs shorter than 3 lines | `_MIN_RUN_LINES` filter |
| Single-column lines | `_MIN_COLS = 2` filter |

### 4.3 Integration edges with legacy

| Edge case | Mechanism |
|---|---|
| Anchor candidate is a sub-region of a legacy table | `anchor ⊂ legacy` containment ≥ 0.50 → dropped |
| Anchor candidate encloses a legacy table | `legacy ⊂ anchor` containment ≥ 0.50 → dropped |
| Cross-page legacy table (`bbox` is `list[BBox]`) | `_overlaps_legacy` iterates every page bbox |
| Anchor candidate and legacy table share a page edge but no area | Containment 0.0 → kept |
| Anchor candidate on page 0, legacy table on page 1 | `_containment_of_anchor` returns 0.0 when pages differ |
| Anchor candidate fed to `build_tree` alongside legacy | Works unchanged — both produce `DocNode(kind="table")` with single-BBox `bbox` |
| Anchor candidate fed to `stitch_tables` | Never happens — `stitch_tables` runs **before** the augmenter |

### 4.4 What the detector deliberately does NOT do

* **Nested tables.** Anchor candidates always emit flat `table → row → cell`.
  Word geometry alone has no signal for "this cell contains a sub-structure":
  if the inner table aligns to the outer's columns, the detector treats both
  as one run; if it doesn't, the run fragments. To detect nesting you need a
  second signal (borders, font shift, indentation) — and at that point
  you've reimplemented the legacy cascade.
* **Single-row tables.** Rejected by `_MIN_RUN_LINES = 3`. Intentional: a
  one-line column alignment is indistinguishable from a heading + tagline.
* **Rotated tables.** Word extraction goes by horizontal lines; rotated
  text would never form a signature.
* **Wrapped logical rows.** Each visual line is treated as one row. A
  logical row that wraps to two visual lines is either accepted as two
  rows (same signature, OK) or rejected (different signatures, fragment).

## 5. Tunables

All thresholds live at the top of `detect_tables_anchor.py`:

| Constant | Default | Effect of raising |
|---|---|---|
| `MIN_SCORE` | 0.65 | More candidates rejected; fewer false positives, fewer true positives |
| `CONTAINMENT_DROP_THRESHOLD` | 0.50 | Less aggressive overlap rejection; more risk of duplicates |
| `_MIN_RUN_LINES` | 3 | Longer runs required; rejects more short candidates |
| `_MIN_COLS` | 2 | Wider grids required |
| `_GAP_THRESHOLD_PT` | 8.0 | Larger gap required to split cells; fewer columns detected |
| `_ANCHOR_TOL_PT` | 4.0 | Looser column alignment; more lines match a signature |
| `_FILL_KNEE` / `_FILL_RAMP` / `_W_FILL_PENALTY` | 0.65 / 5.0 / 0.40 | More aggressive prose penalty |

Weights `_W_ROWS / _W_COLS / _W_STAB / _W_SPACING / _W_NUMERIC` sum to 1.0
before the fill penalty. Re-tuning any of them will shift the `MIN_SCORE`
threshold needed for separation. The calibration script
`scripts/explore_anchor_detector.py` runs the detector against the
synthetic fixture corpus and prints per-fixture scores; rerun after any
weight change.

## 6. Testing

Tests live in `tests/stages/test_detect_tables_anchor.py` (21 tests):

* **No-shadowing parametrized over 9 fixtures.** On every synthetic
  fixture the legacy cascade already handles, experimental table count
  MUST equal legacy table count. This pins both directions of the
  containment regression and the list-shape pre-filter.
* **List-shape unit tests.** `•`, `(cid:127)`, `-` + prose all rejected;
  real 2-col data not rejected; empty-grid edge cases handled.
* **True-positive recovery.** A reportlab-synthesized borderless
  long-text PDF that the legacy `_MAX_CELL_TEXT_CHARS = 7` heuristic
  rejects is parsed; experimental must add ≥ 1 anchor table with the
  expected shape and `score ≥ 0.65`.
* **Row bbox structural test.** Hand-built `_Candidate` round-trips
  through `_candidate_to_docnode`; each row's bbox is the union of its
  cells and differs from sibling rows.
* **Containment unit tests.** Both directions of `_overlaps_legacy`
  with hand-built bboxes; disjoint same-page case (not rejected);
  cross-page case (returns 0.0).
* **End-to-end nesting regression.** The synthesized long-text PDF is
  parsed twice: once with no legacy competitor (baseline anchor table
  exists), once with a fake legacy table placed inside the anchor's
  expected region (no anchor table survives).

## 7. Limitations and known holes

* **No tests against real-world PDFs.** Every test today uses synthetic
  fixtures. The calibration thresholds were tuned on the same corpus,
  so this is a self-referential loop until a real-world fixture set is
  built.
* **Re-opens the PDF.** `augment_with_anchor_tables` opens `pdfplumber.PDF`
  a second time per parse, after `extract_tables` already opened it. One
  extra open per parse, only when the flag is set. Acceptable for now;
  the right fix is to thread a shared `pdfplumber.PDF` handle through
  both stages (which requires changing `extract_tables`' signature).
* **Single-page only by construction.** Anchor candidates never span
  pages because `_column_anchor_detector` is called per page. Cross-page
  anchor tables are not stitched — `stitch_pages` runs before the
  augmenter.
* **Calibration is brittle to font / spacing changes.** `_GAP_THRESHOLD_PT`
  and `_ANCHOR_TOL_PT` are tuned to LETTER-page 10 pt body fonts. Tight
  layouts (8 pt) or wide layouts (14 pt) may need re-tuning.

## 8. Extending

To add a new signal:

1. Compute it in `_column_anchor_detector` and add it to `_W_*` /
   `signals` dict.
2. Update `MIN_SCORE` if the addition shifts the separation.
3. Rerun `scripts/explore_anchor_detector.py` against the synthetic
   corpus and confirm prose ≤ MIN_SCORE ≤ table.
4. Add a unit test if the signal has an isolated discriminating
   behavior.

To support a new false-positive shape (analogous to bulleted lists):

1. Add a `_is_<shape>` predicate next to `_is_list_shape`.
2. Call it before scoring inside `_column_anchor_detector`.
3. Add unit tests for the predicate alone (positive + negative + edge).
4. Add a no-shadowing fixture covering the shape.

To support a new true-positive shape:

1. Add the fixture under `tests/golden/synthetic/`.
2. Run with `--table-detector experimental` and inspect the candidate
   scores / signals.
3. Tune weights or thresholds only after confirming the calibration run
   keeps the prose / table separation.

## 9. Related code

| File | Role |
|---|---|
| `pdf_parser/stages/detect_tables_anchor.py` | This module. Public entry: `augment_with_anchor_tables`. |
| `pdf_parser/pipeline.py` | Dispatch on `table_detector` arg; wires the augmenter in after `stitch_tables`. |
| `pdf_parser/cli.py` | `--table-detector` flag. |
| `pdf_parser/stages/detect_tables.py` | Legacy cascade. Anchor consumes its output as a black box. |
| `tests/stages/test_detect_tables_anchor.py` | All anchor tests (21). |
| `scripts/explore_anchor_detector.py` | Calibration / per-fixture score dump. |
