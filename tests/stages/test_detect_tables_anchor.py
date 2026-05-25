"""Tests for the experimental column-anchor table detector.

Pins the three load-bearing invariants of the overlay:

* **No shadowing.** On fixtures the legacy cascade already handles, the
  experimental path MUST emit zero anchor tables.  Sub-region containment
  (not IoU) is the guard; a regression here would surface as duplicate
  tables in the output tree.
* **List rejection.** A bulleted or numbered list satisfies the column-
  anchor signature but is structurally text.  ``_is_list_shape`` rejects
  these before scoring; this test pins that pre-score filter.
* **True positive.** A borderless table whose average cell text exceeds
  the legacy ``_MAX_CELL_TEXT_CHARS = 7`` cutoff is invisible to the
  legacy cascade.  The anchor detector MUST find it.

Plus two structural tests:

* Anchor-emitted ``row`` nodes carry per-row bboxes (not the table bbox).
* ``_containment_of_anchor`` measures intersection-over-anchor-area, not
  IoU — a regression to IoU lets sub-region candidates slip through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.model import BBox, DocNode
from pdf_parser.pipeline import parse as parse_pdf
from pdf_parser.stages.detect_tables_anchor import (
    CONTAINMENT_DROP_THRESHOLD,
    _Candidate,
    _candidate_to_docnode,
    _containment_of_anchor,
    _is_list_shape,
    _overlaps_legacy,
    augment_with_anchor_tables,
)

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "synthetic"


def _all_tables(node: DocNode) -> list[DocNode]:
    out: list[DocNode] = []
    if node.kind == "table":
        out.append(node)
    for c in node.children:
        out.extend(_all_tables(c))
    return out


def _anchor_tables(node: DocNode) -> list[DocNode]:
    # Stitched anchor tables carry ``anchor+stitch``; ``startswith`` covers both.
    return [t for t in _all_tables(node)
            if t.provenance.get("extractor", "").startswith("anchor")]


# ---------------------------------------------------------------------------
# Invariant 1: no shadowing on fixtures the legacy cascade already handles.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture", [
    "01_simple_table",
    "05_sections_lists",
    "07_page_spanning_with_nested",
    "11_pl_statement",
    "13_comprehensive",
    "14_borderless_table",
    "15_multicolumn_text",
    "16_text_between_subtables",
    "17_text_between_subtables_spanning",
])
def test_anchor_does_not_shadow_legacy(fixture: str) -> None:
    """Anchor-enabled parse MUST yield the same table count as anchor-disabled
    on every fixture the legacy cascade already handles.

    Any extra table from the anchor path on a fixture legacy already handles
    is a duplicate sub-region (the IoU bug) or a list misidentified as a
    table (the list-shape bug).
    """
    pdf = GOLDEN / fixture / "source.pdf"
    if not pdf.exists():
        pytest.skip(f"missing fixture {fixture}")
    no_anchor_count = len(_all_tables(parse_pdf(pdf, use_anchor=False)))
    anchor_count    = len(_all_tables(parse_pdf(pdf)))  # anchor on by default
    assert anchor_count == no_anchor_count, (
        f"{fixture}: no_anchor={no_anchor_count}, anchor={anchor_count} — "
        f"anchor added phantom tables on a fixture the legacy cascade already handles."
    )


# ---------------------------------------------------------------------------
# Invariant 2: bulleted-list rejection happens before scoring.
# ---------------------------------------------------------------------------

def test_is_list_shape_detects_repeated_first_column() -> None:
    """The first-column-constant heuristic catches bullet-glyph patterns."""
    bulleted = [
        ["•", "First finding."],
        ["•", "Second finding."],
        ["•", "Third finding."],
    ]
    cid_bullets = [
        ["(cid:127)", "Alpha"],
        ["(cid:127)", "Beta"],
        ["(cid:127)", "Gamma"],
    ]
    dashes = [
        ["-", "one"],
        ["-", "two"],
        ["-", "three"],
    ]
    assert _is_list_shape(bulleted)
    assert _is_list_shape(cid_bullets)
    assert _is_list_shape(dashes)


def test_is_list_shape_keeps_real_two_column_table() -> None:
    """A genuine 2-col table with varying keys is NOT a list."""
    grid = [
        ["Alice", "95"],
        ["Bob",   "87"],
        ["Carol", "91"],
    ]
    assert not _is_list_shape(grid)


def test_is_list_shape_handles_edge_cases() -> None:
    assert not _is_list_shape([])
    assert not _is_list_shape([[]])


# ---------------------------------------------------------------------------
# Invariant 3: true positive on a borderless table with long-text cells.
# ---------------------------------------------------------------------------

def test_anchor_recovers_borderless_long_text_table_legacy_misses() -> None:
    """The detector's reason for existing: borderless tables with long-text
    cells that the legacy ``_MAX_CELL_TEXT_CHARS=7`` heuristic discards.

    Consumes the committed ``14b_borderless_long_text`` golden fixture so the
    detector is exercised against the exact PDF the golden suite pins.
    """
    pdf = GOLDEN / "14b_borderless_long_text" / "source.pdf"
    if not pdf.exists():
        pytest.skip("missing 14b_borderless_long_text fixture")

    legacy_tree = parse_pdf(pdf, use_anchor=False)
    experimental_tree = parse_pdf(pdf)  # anchor on by default

    legacy_tables = _all_tables(legacy_tree)
    anchor_extras = _anchor_tables(experimental_tree)

    # Whether legacy returns 0 or some count, experimental must add at
    # least one anchor-sourced table that legacy did not produce.
    assert len(anchor_extras) >= 1, (
        "Anchor detector failed to recover a borderless long-text table "
        f"(legacy found {len(legacy_tables)}, anchor added 0)."
    )
    # The recovered table should be the 3-col, 5-row shape from the
    # fixture.  Header row is the first emitted row.
    t = anchor_extras[0]
    assert t.attrs["n_cols"] == 3, t.attrs
    assert t.attrs["n_rows"] >= 4, t.attrs  # tolerant of header detection variance
    assert t.attrs["anchor_score"] >= 0.65


# ---------------------------------------------------------------------------
# Invariant 4: cross-page anchor stitching.
# ---------------------------------------------------------------------------

def test_anchor_candidates_on_consecutive_pages_get_stitched() -> None:
    """The 14c spanning fixture is a borderless long-text table that overflows
    onto a second page.  After the pipeline reorder (anchor before stitch),
    the two per-page anchor candidates MUST merge into a single table node:

    * exactly one anchor-sourced table in the tree (not two)
    * ``bbox`` is ``list[BBox]`` of length 2, one per spanned page
    * ``provenance.extractor == "anchor+stitch"`` (preserves source extractor)
    * ``attrs["spans_pages"] == [0, 1]``
    * the duplicate header row from page 2 (``repeatRows=1``) is deduped

    Without same-extractor stitching, two unrelated extractors would be free to
    merge whenever column anchors happened to match within 4 pt — the guard in
    :func:`pdf_parser.stages.stitch_pages._can_merge` prevents that.
    """
    pdf = GOLDEN / "14c_borderless_long_text_spanning" / "source.pdf"
    if not pdf.exists():
        pytest.skip("missing 14c_borderless_long_text_spanning fixture")

    tree = parse_pdf(pdf)  # anchor on by default
    anchor_extras = _anchor_tables(tree)

    # One table only — stitched, not two fragments.
    assert len(anchor_extras) == 1, (
        f"Expected one stitched anchor table; got {len(anchor_extras)} "
        f"(stitch did not merge consecutive-page anchor candidates)."
    )

    t = anchor_extras[0]
    assert isinstance(t.bbox, list) and len(t.bbox) == 2, (
        f"Expected list[BBox] of length 2 for cross-page stitch; got {t.bbox!r}"
    )
    assert [b.page for b in t.bbox] == [0, 1]
    assert t.provenance.get("extractor") == "anchor+stitch", t.provenance
    assert t.attrs.get("spans_pages") == [0, 1], t.attrs
    assert t.attrs["n_cols"] == 3
    # 50 data rows + 1 header (page-2 header deduped by _merge_two).
    assert t.attrs["n_rows"] == 51, t.attrs


def test_legacy_and_anchor_never_cross_stitch() -> None:
    """Stitch is intra-extractor.  Hand-build a legacy fragment on page 0 and
    an anchor fragment on page 1 whose column anchors deliberately match, then
    call ``stitch_tables`` directly.  They MUST NOT merge — different source
    extractors.
    """
    from pdf_parser.stages.stitch_pages import stitch_tables

    cell_a = DocNode(kind="cell", bbox=BBox(page=0, x0=72, y0=700, x1=148, y1=710),
                     attrs={"covered": False})
    cell_b = DocNode(kind="cell", bbox=BBox(page=0, x0=222, y0=700, x1=307, y1=710),
                     attrs={"covered": False})
    legacy = DocNode(
        kind="table",
        bbox=BBox(page=0, x0=72, y0=124, x1=503, y1=710),
        children=[DocNode(kind="row", bbox=BBox(page=0, x0=72, y0=700, x1=307, y1=710),
                          children=[cell_a, cell_b])],
        attrs={"page_height": 792.0, "header_signature": ("A", "B")},
        provenance={"extractor": "pdfplumber", "stage": "extract_tables"},
    )
    cell_c = DocNode(kind="cell", bbox=BBox(page=1, x0=72, y0=100, x1=148, y1=110),
                     attrs={"covered": False})
    cell_d = DocNode(kind="cell", bbox=BBox(page=1, x0=222, y0=100, x1=307, y1=110),
                     attrs={"covered": False})
    anchor = DocNode(
        kind="table",
        bbox=BBox(page=1, x0=72, y0=100, x1=503, y1=300),
        children=[DocNode(kind="row", bbox=BBox(page=1, x0=72, y0=100, x1=307, y1=110),
                          children=[cell_c, cell_d])],
        attrs={"page_height": 792.0, "header_signature": ("A", "B")},
        provenance={"extractor": "anchor", "stage": "detect_tables_anchor"},
    )

    out = stitch_tables([legacy, anchor])
    assert len(out) == 2, (
        "Stitch merged a legacy fragment with an anchor fragment across pages; "
        "intra-extractor guard is broken."
    )
    assert out[0].provenance["extractor"] == "pdfplumber"
    assert out[1].provenance["extractor"] == "anchor"


# ---------------------------------------------------------------------------
# Structural: row bboxes are per-row, not the table bbox.
# ---------------------------------------------------------------------------

def test_row_bboxes_are_per_row_not_table_bbox() -> None:
    """Regression guard: every anchor-emitted row used to inherit the table
    bbox.  Each row's bbox MUST be the union of its cell bboxes and MUST
    differ from sibling rows.
    """
    page_idx = 0
    cells = [
        [
            BBox(page=page_idx, x0=72, y0=100, x1=140, y1=110),
            BBox(page=page_idx, x0=200, y0=100, x1=260, y1=110),
        ],
        [
            BBox(page=page_idx, x0=72, y0=120, x1=140, y1=130),
            BBox(page=page_idx, x0=200, y0=120, x1=260, y1=130),
        ],
        [
            BBox(page=page_idx, x0=72, y0=140, x1=140, y1=150),
            BBox(page=page_idx, x0=200, y0=140, x1=260, y1=150),
        ],
    ]
    cand = _Candidate(
        page_index=page_idx,
        bbox=BBox(page=page_idx, x0=72, y0=100, x1=260, y1=150),
        grid=[["Alice", "95"], ["Bob", "87"], ["Carol", "91"]],
        cell_bboxes=cells,
        score=0.9,
        signals={},
    )
    table = _candidate_to_docnode(cand, page_height=792.0)
    row_bboxes = [r.bbox for r in table.children]

    # Each row bbox spans only its own cells, not the full table height.
    assert row_bboxes[0] == BBox(page=page_idx, x0=72, y0=100, x1=260, y1=110)
    assert row_bboxes[1] == BBox(page=page_idx, x0=72, y0=120, x1=260, y1=130)
    assert row_bboxes[2] == BBox(page=page_idx, x0=72, y0=140, x1=260, y1=150)
    # And sibling rows differ.
    assert len({rb for rb in row_bboxes}) == len(row_bboxes)


# ---------------------------------------------------------------------------
# Structural: containment, not IoU.
# ---------------------------------------------------------------------------

def test_containment_drops_small_sub_region_of_large_legacy_table() -> None:
    """A 3-row band inside a 34-row legacy table has IoU << 0.30 but is
    fully contained.  The augmenter MUST drop it.  This is the exact
    regression that produced shadow tables on ``11_pl_statement`` before
    the IoU→containment fix.
    """
    legacy_table = DocNode(
        kind="table",
        bbox=BBox(page=0, x0=82, y0=90, x1=530, y1=532),  # 448 × 442
        children=[],
    )
    sub_region_bbox = BBox(page=0, x0=90, y0=263, x1=526, y1=296)  # 436 × 33, fully inside

    # Containment metric reports the sub-region as inside the legacy table.
    assert _containment_of_anchor(sub_region_bbox, legacy_table.bbox) > CONTAINMENT_DROP_THRESHOLD

    # End-to-end: the augmenter drops a candidate at this sub-region.
    sub_cand_as_doc = DocNode(  # build a fake "legacy" list so augmenter has context
        kind="table",
        bbox=legacy_table.bbox,
        children=[],
    )
    # No PDF needed — augment_with_anchor_tables opens its own.  Use a
    # one-page fixture and assert that even if the anchor detector finds
    # candidates on that page, none whose bbox falls inside the legacy
    # rectangle survive.
    pdf = GOLDEN / "11_pl_statement" / "source.pdf"
    if not pdf.exists():
        pytest.skip("missing 11_pl_statement fixture")
    out = augment_with_anchor_tables([sub_cand_as_doc], pdf)
    survivors = [t for t in out if t.provenance.get("extractor") == "anchor"]
    for t in survivors:
        # Anything that survived must NOT be majority-contained in the legacy bbox.
        bb = t.bbox if isinstance(t.bbox, BBox) else t.bbox[0]
        assert _containment_of_anchor(bb, legacy_table.bbox) <= CONTAINMENT_DROP_THRESHOLD, (
            f"Candidate at {bb} slipped through containment filter "
            f"(containment={_containment_of_anchor(bb, legacy_table.bbox):.2f})."
        )


def test_containment_does_not_drop_adjacent_disjoint_table() -> None:
    """Two tables that share a page edge but no area should not interact."""
    a = BBox(page=0, x0=72, y0=100, x1=300, y1=200)
    b = BBox(page=0, x0=72, y0=400, x1=300, y1=500)
    assert _containment_of_anchor(a, b) == 0.0
    assert _containment_of_anchor(b, a) == 0.0


def test_containment_returns_zero_for_different_pages() -> None:
    a = BBox(page=0, x0=72, y0=100, x1=300, y1=200)
    b = BBox(page=1, x0=72, y0=100, x1=300, y1=200)
    assert _containment_of_anchor(a, b) == 0.0

def test_overlaps_legacy_fires_on_anchor_inside_legacy() -> None:
    """``anchor ⊂ legacy`` direction — the sub-region-shadow case."""
    legacy = [DocNode(
        kind="table",
        bbox=BBox(page=0, x0=72, y0=100, x1=540, y1=700),  # large outer
        children=[],
    )]
    cand = _Candidate(
        page_index=0,
        bbox=BBox(page=0, x0=100, y0=300, x1=500, y1=360),  # small sub-region
        grid=[["a", "b"]], cell_bboxes=[[]], score=0.9, signals={},
    )
    assert _overlaps_legacy(cand, legacy) is True


def test_overlaps_legacy_fires_on_legacy_inside_anchor() -> None:
    """``legacy ⊂ anchor`` direction — the borderless-outer + bordered-inner
    nesting case.  Anchor spans the whole region; legacy emits the bordered
    inner table.  Without symmetric containment the anchor would survive and
    the inner table would appear twice in the tree.
    """
    legacy = [DocNode(
        kind="table",
        bbox=BBox(page=0, x0=200, y0=350, x1=400, y1=420),  # small inner
        children=[],
    )]
    cand = _Candidate(
        page_index=0,
        bbox=BBox(page=0, x0=72, y0=200, x1=540, y1=600),  # large outer
        grid=[["a", "b"]], cell_bboxes=[[]], score=0.9, signals={},
    )
    assert _overlaps_legacy(cand, legacy) is True


def test_overlaps_legacy_keeps_disjoint_legacy_table_on_same_page() -> None:
    """A legacy table sitting in a different y-band on the same page MUST NOT
    suppress an anchor candidate.  Guards against over-eager rejection now
    that the check is symmetric.
    """
    legacy = [DocNode(
        kind="table",
        bbox=BBox(page=0, x0=72, y0=100, x1=540, y1=200),  # top of page
        children=[],
    )]
    cand = _Candidate(
        page_index=0,
        bbox=BBox(page=0, x0=72, y0=400, x1=540, y1=600),  # bottom of page
        grid=[["a", "b"]], cell_bboxes=[[]], score=0.9, signals={},
    )
    assert _overlaps_legacy(cand, legacy) is False


def test_augmenter_drops_anchor_that_encloses_legacy_table() -> None:
    """End-to-end ``legacy ⊂ anchor`` regression.

    Use the synthesized long-text borderless PDF where the anchor detector
    is known to emit a candidate.  Pass in a fake "legacy" table whose bbox
    sits inside that candidate's expected region.  The augmenter MUST drop
    the anchor candidate — otherwise the inner table's content would be
    duplicated in the final tree (once as legacy, once flattened inside the
    anchor's cell).
    """
    pdf = GOLDEN / "14b_borderless_long_text" / "source.pdf"
    if not pdf.exists():
        pytest.skip("missing 14b_borderless_long_text fixture")

    # Baseline: anchor produces at least one candidate when no legacy
    # competitor exists.  Pins the precondition of the test.
    baseline = augment_with_anchor_tables([], pdf)
    baseline_anchors = [t for t in baseline if t.provenance.get("extractor") == "anchor"]
    assert baseline_anchors, "precondition: anchor must emit at least one candidate"

    outer_bbox = baseline_anchors[0].bbox
    outer = outer_bbox if isinstance(outer_bbox, BBox) else outer_bbox[0]

    # Place a fake legacy table inside that anchor's region (small
    # rectangle in the middle).  Any anchor candidate whose bbox
    # encloses this rectangle must be dropped.
    cx0 = outer.x0 + (outer.x1 - outer.x0) * 0.25
    cx1 = outer.x0 + (outer.x1 - outer.x0) * 0.75
    cy0 = outer.y0 + (outer.y1 - outer.y0) * 0.25
    cy1 = outer.y0 + (outer.y1 - outer.y0) * 0.75
    fake_inner = DocNode(
        kind="table",
        bbox=BBox(page=outer.page, x0=cx0, y0=cy0, x1=cx1, y1=cy1),
        children=[],
    )

    out = augment_with_anchor_tables([fake_inner], pdf)
    survivors = [t for t in out if t.provenance.get("extractor") == "anchor"]
    for t in survivors:
        bb = t.bbox if isinstance(t.bbox, BBox) else t.bbox[0]
        assert _containment_of_anchor(fake_inner.bbox, bb) <= CONTAINMENT_DROP_THRESHOLD, (
            f"Anchor candidate at {bb} survived despite enclosing the legacy "
            f"table at {fake_inner.bbox} "
            f"(reverse containment="
            f"{_containment_of_anchor(fake_inner.bbox, bb):.2f})."
        )
