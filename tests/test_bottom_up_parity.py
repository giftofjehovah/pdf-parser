# tests/test_bottom_up_parity.py
"""Per-fixture parity: parse(use_bottom_up=False) == parse(use_bottom_up=True).

Each fixture is marked ``xfail(strict=False)`` until its phase reaches parity.
When a fixture starts passing (xpassed) the developer removes its xfail in the
same commit as the implementation change, so future regressions surface as
plain failures rather than silent xpasses.

After all 28 cases pass, Phase 10 deletes this file and flips the pipeline
default.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from pdf_parser.pipeline import parse
from pdf_parser.model import DocNode
from scripts.update_goldens import _load_parser_config

CASES_DIR = Path("tests/golden/synthetic")

# Fixtures move OUT of this set in the same commit that brings them to parity.
# Phase 10 deletes the set (and this file) once it is empty.
#
# Phase 5 residual (18/19/20/23): the ruled-header / framed-body fixtures
# remain xfailed.  ``detect_cells`` short-circuits to the first non-empty
# source (line > gutter > text), so line-bounded header cells consume the
# page and gutter body cells are never produced.  A naive line+gutter union
# also fails: the header row carries both line and gutter cells at slightly
# offset bboxes, so ``_dedupe_cells`` keeps both and the header row's
# column count diverges from the body's.  Real fix lives one step deeper:
# either filter gutter cells inside any line cell's bbox, or run the
# gutter detector below the header band only.  Left as Phase-5+ residual.
#
# Phase 6 residual (10/21): the merged-cell fixtures remain xfailed.  Tasks
# 6.1-6.3 added column-anchor alignment, union-clustered anchors, and
# legacy-faithful covered-cell bboxes for in-row colspans, but vertical
# merges (rowspan) need a separate detector that bottom-up does not yet
# have.  A rowspan cell ("North" at y=154..190 in fixture 10, "Pacific
# Northwest Division" at y=204..264 in fixture 21) has a y-midpoint that
# diverges from its neighbours' (which are short, half-height cells), so
# ``_row_cluster`` puts it in its own single-cell row.  Legacy emits it
# once in the first visual row of the span and adds ``covered`` slots in
# the remaining visual rows; bottom-up has no equivalent step yet, so the
# table splits at the rowspan boundary.  Real fix lives one step deeper:
# detect tall cells whose y-extent covers multiple visual rows formed by
# their shorter neighbours, place them once in the first such row, and
# emit covered entries in the remaining rows under the same column anchor.
# Left as Phase-6+ residual.
#
# Phase 7 residual (24/25/26): the flush-edge sub-table fixtures remain
# xfailed.  Task 7.1 verified that ``_cells_inside`` already accepts flush
# edges (the ``_CONTAIN_TOL`` slack covers anti-aliasing, and strict-smaller
# is per-cell on at least one axis -- always true for legitimate inner-table
# cells), so unit-level containment is green.  The fixture-level gap is one
# step deeper: legacy emits an outer 1x1 frame wrapping {paragraph + 2
# nested tables}, while bottom-up fuses everything into a single multi-row
# table because (a) ``_rows_to_celltable`` rejects single-row / single-col
# candidates so the outer wrapper can never be emitted as a CellTable, and
# (b) the outer rectangle's left/right vertical lines extend through the
# inter-subtable paragraph band, so the line/gutter detector synthesises an
# artificial cell-row for the paragraph at the parent's full width -- which
# `_split_into_tables` then merges with the inner-table rows above and below
# (same page, same x0, gap-under-threshold).  Result: a flat 7-row table on
# 24, a flat 6-row table per page on 25, with the paragraph absorbed as a
# row.  Fixture 26 layers two extra issues on top: the sub-table's column
# boundaries (x0 = 202, 262, 322) pollute the main table's union-clustered
# anchors (Task 6.2 side effect), splitting the parent cell at row 28 into
# multiple narrow slots so the sub-table cluster no longer fits inside any
# one parent cell for nesting; plus the cross-page continuation is not yet
# stitched (Phase 8).  Real fix lives one step deeper: detect outer-frame
# rectangles whose interior has no horizontal lines beyond the corners and
# emit them as 1x1 wrappers hosting the detected inner sub-clusters; or
# carve out sub-clusters via recursive containment BEFORE union-clustering
# column anchors so the main table's anchor set stays clean.  Left as
# Phase-7+ residual; 26's stitching half is for Phase 8.
#
# Phase 8 residual (07/08/17/26): cross-page fixtures whose per-page output
# never matches legacy, so ``stitch_pages`` (verified extractor-agnostic in
# ``tests/stages/test_stitch_pages_bottom_up.py``) has nothing to stitch.
# Task 8.2 confirmed only 09 and 14c reach parity at the phase boundary:
#
#  * 07_page_spanning_with_nested -- legacy emits one 51x3 main table
#    p0 y=118..700 -> p1 carrying two nested 2x2 sub-tables (p0 y=210..248
#    at row[5]; p1 y=332..370 at row[45]).  Bottom-up's gap-split fires
#    at every tall row: the row containing a nested sub-table is taller
#    than its neighbours, so ``aggregate_tables._split_into_tables``
#    breaks the main table after y=248 on p0 (header drifts to
#    "6/plain input 6/note 6") and again after y=370 on p1 (header
#    "46/plain input 46/note 46").  Three orphan tables with diverging
#    header signatures -- ``_can_merge``'s anchor+signature check
#    rejects every adjacent pair, so stitching can't reassemble them.
#    Real fix lives one step deeper: make the row-gap split threshold
#    aware of nested sub-table heights (or carve sub-clusters out via
#    recursive containment BEFORE gap-splitting the parent rows).
#    Left as Phase-8+ residual.
#
#  * 08_page_spanning_subtable_split -- legacy emits one 50x3 main
#    p0 y=118..698 -> p1 plus a nested 4x2 sub-table cut by the page
#    break (p0 y=624..698 + p1 y=80..136).  Bottom-up emits four
#    siblings instead of one nested span: main p0 29x3, sub-table p0
#    y=643..698 (header 'a','1', emitted as sibling not nested),
#    sub-table p1 y=80..136 (4 cols not 2 -- column-anchor pollution
#    flowing in from union-clustered main anchors at x0=202/262/322),
#    main p1 20x3 with header drifting to "30/plain 30/n30".  The
#    interleaved sibling sub-table sits between the two main fragments
#    in ``stitch_tables``'s adjacency walk, so ``_can_merge`` never
#    even compares p0-main with p1-main.  Real fix lives one step
#    deeper: nest sub-tables inside parent cells (Phase 7's outer-frame
#    work) so they don't appear as same-level siblings between main
#    fragments.  Left as Phase-8+ residual; subset of the Phase-7
#    1xN-wrapper residual.
#
#  * 17_text_between_subtables_spanning -- legacy emits one 4x1 outer
#    wrapper p0 y=118..712 -> p1 (header "Section Header") hosting both
#    inner sub-tables and the inter-table paragraphs as its four cells.
#    Bottom-up correctly emits the two inner sub-tables (p0 'Item','Qty'
#    y=142..196 and p1 'Month','Sales' y=486..540) and the paragraphs,
#    but lacks the 1xN outer-frame reconstruction (same root cause as
#    24/25/26 -- ``_rows_to_celltable`` rejects single-column
#    candidates).  Cross-page aspect is incidental: nothing per-page
#    forms the outer wrapper, so stitching is moot.  Real fix is the
#    Phase-7+ outer-frame work; this fixture is a pure Phase-7
#    residual that happened to span pages.  Left as Phase-8+ residual.
#
#  * 26_spanning_subtable_flush_at_break -- already documented as a
#    Phase-7 residual; Phase 8 adds two empirical findings.  (1) The
#    continuation-page sub-table (legacy p1 y=78..116 header 'c','3')
#    is NOT detected at all by bottom-up on p1 -- the would-be cell
#    contents surface as loose paragraphs ("3", "c", "29",
#    "starts pg n+1", "d", "4") because the flush-top edge of the
#    sub-table abutting page-top y=78 leaves no horizontal evidence
#    for ``detect_cells`` to anchor on.  (2) Header signatures of the
#    two main fragments diverge identically to fixture 07
#    ("Step/Detail/Notes" vs "30/plain 30/n30"), so even if (1) were
#    fixed, ``_can_merge`` would still reject the main pair.  Left as
#    Phase-8+ residual; superset of Phase-7's residual on this fixture.
_XFAIL_CASES: set[str] = {
    "02_nested_table",
    "07_page_spanning_with_nested",
    "08_page_spanning_subtable_split",
    "10_merged_cells",
    "13_comprehensive",
    "16_text_between_subtables",
    "17_text_between_subtables_spanning",
    "18_ruled_header_open_body",
    "19_ruled_header_framed_body",
    "20_ruled_header_row_strips",
    "21_vertical_merge_invisible_lines",
    "22_text_between_adjacent_tables",
    "23_bordered_cell_with_bulleted_prose",
    "24_subtable_flush_outer_edges",
    "25_subtable_flush_outer_vertical_only",
    "26_spanning_subtable_flush_at_break",
}


def _all_ids(tree: DocNode) -> set[str]:
    out: set[str] = set()
    stack = [tree]
    while stack:
        n = stack.pop()
        out.add(n.id)
        stack.extend(n.children)
    return out


def _id_to_breadcrumb(tree: DocNode) -> dict[str, str]:
    """Map node.id → 'document>page[0]>table>row[2]>cell[1]' style path."""
    out: dict[str, str] = {}

    def walk(node: DocNode, crumbs: list[str]) -> None:
        out[node.id] = ">".join(crumbs) or node.kind
        for i, c in enumerate(node.children):
            walk(c, crumbs + [f"{c.kind}[{i}]"])

    walk(tree, [tree.kind])
    return out


def _format_diff(a: DocNode, b: DocNode) -> str:
    a_ids, b_ids = _all_ids(a), _all_ids(b)
    only_a, only_b = a_ids - b_ids, b_ids - a_ids
    crumbs_a, crumbs_b = _id_to_breadcrumb(a), _id_to_breadcrumb(b)
    lines = [
        f"  legacy_only ({len(only_a)}):",
        *(f"    {nid}  {crumbs_a[nid]}" for nid in sorted(only_a, key=crumbs_a.__getitem__)),
        f"  bottom_up_only ({len(only_b)}):",
        *(f"    {nid}  {crumbs_b[nid]}" for nid in sorted(only_b, key=crumbs_b.__getitem__)),
    ]
    return "\n".join(lines)


def _all_cases() -> list:
    cases = sorted(p.name for p in CASES_DIR.iterdir() if (p / "source.pdf").exists())
    out = []
    for c in cases:
        if c in _XFAIL_CASES:
            out.append(pytest.param(
                c,
                marks=pytest.mark.xfail(
                    strict=False,
                    reason=f"bottom-up parity pending for {c}",
                ),
            ))
        else:
            out.append(c)
    return out


@pytest.mark.parametrize("case", _all_cases())
def test_bottom_up_matches_legacy(case: str) -> None:
    case_dir = CASES_DIR / case
    pdf = case_dir / "source.pdf"
    cfg = _load_parser_config(case_dir)
    legacy = parse(pdf, **{**cfg, "use_bottom_up": False})
    new = parse(pdf, **{**cfg, "use_bottom_up": True})
    legacy_ids, new_ids = _all_ids(legacy), _all_ids(new)
    assert legacy_ids == new_ids, (
        f"\nbottom-up parity failed for {case}:\n{_format_diff(legacy, new)}"
    )
