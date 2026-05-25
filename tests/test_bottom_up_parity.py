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
# Phase 9 outcome (07/13): ``aggregate_tables._carve_subclusters`` carves
# nested sub-cluster cells out of top-level row clustering using tall-cell
# brackets + synthetic parent cells.  This recovers the page-spanning
# nested Project Tracking table + Hardware Inventory nesting + the global
# 5-spanning-tables count for the omnibus, unblocking 6 of the 21
# previously-failing assertions in ``tests/test_comprehensive.py`` (all
# project_table tests, hardware_inventory nested tables, and the
# Quarterly Performance covered-cells count).  Strict id-set parity for
# 07/08/13 still fails: synthetic-parent bboxes are not byte-identical to
# legacy's per-cell bboxes (off by a few pt where legacy reconstructs the
# outer container from full-width H-lines), so node IDs diverge even when
# the tree's structural shape matches.  The remaining residuals (Phase-5
# ruled-header, Phase-6 rowspan, Phase-7 1xN outer-frame) surface in
# 13_comprehensive as 15 per-assertion bottom_up xfails — see the
# residual constants in ``tests/test_comprehensive.py`` for the exact
# upstream-fixture mapping.  Phase 10+ work: byte-identical synthetic
# bboxes (would unblock 07/13 parity); 1xN wrapper support (would unblock
# 16/17/24/25 + Annex C/D); rowspan detector (would unblock 10/21 +
# Annex E + merged_cells); ruled-header source-union (would unblock
# 18/19/20/23 + Annex A).
#
# Phase 10 prep outcome (fixtures 16 / Annex C in 13_comprehensive):
# ``aggregate_tables._carve_container_frames`` isolates cells that strictly
# contain ≥4 others, ``_build_single_col_wrapper`` emits the rejected 1xN
# candidate as a CellTable when at least one of its rows references such a
# container, and the recursive nested aggregation uses a tighter
# ``_NESTED_CONTAINER_GAP_MULT`` (1.2× instead of 2.5×) so sibling
# sub-tables separated only by inter-table whitespace inside the container
# split cleanly.  This unblocks the two ``test_annex_c_*`` assertions in
# ``tests/test_comprehensive.py`` and the ``test_16_keeps_between_text``
# regression in ``tests/stages/test_extract_tables_v2_between.py``.
# Strict id-set parity for 16/17 still fails: legacy emits an 11x1 outer
# wrapper with empty-placeholder rows whose y-extents mirror the inner
# sub-table row boundaries, while bottom-up emits a 3x1 wrapper (header /
# container / footer); IDs cascade-differ.  Fixtures 24/25 + Annex D
# outer 'Spanning Header' have NO line-detected container cell at all —
# pdfplumber's line strategy collapses 24's outer frame into a single
# table sharing internal cells, while 25 and Annex D have no outer rule
# evidence whatsoever.  Legacy promotes those wrappers via the anchor
# detector instead; bottom-up has no equivalent gutter→frame promotion
# yet.  Left as Phase-10+ residual; would need either an outer-rectangle
# synthesiser working on line geometry directly, or a port of the anchor
# detector's borderless-frame promotion into ``detect_cells``.
#
# Phase 10 prep outcome (fixtures 10/21 rowspan):
# ``_apply_rowspan_merge`` re-clusters tall single-cell rows whose ymid
# diverges from their shorter neighbours into the first multi-cell row
# they y-overlap; ``_split_into_tables`` then keeps the table whole by
# tolerating a missing leftmost cell when an earlier row's rowspan
# covers the missing column; and ``_rows_to_celltable``'s post-pass
# overwrites those covered slots' bboxes with the spanning cell's bbox
# so the rowspan semantics match legacy.  This unblocks the
# ``test_merged_cells_*`` + ``test_annex_e_*`` assertions in
# ``tests/test_comprehensive.py`` (6 assertions) and the
# ``test_multicolumn_text_not_misidentified_as_table`` aggregate
# (the rowspan split previously yielded spurious tables).  Strict
# id-set parity for 10/21 still fails: covered-slot bboxes differ from
# legacy's by ~1pt because legacy reconstructs them from the line
# detector's per-column anchor x-ranges (Q1=[a, b)), while bottom-up
# uses the spanning cell's own x-range (which matches the column anchor
# in practice but rounds differently on the y-bounds).  Left as
# Phase-10+ parity-only residual.
#
# Phase 10 prep deferred (07/13 synthetic-parent bbox parity):
# pdfplumber's line-strategy ``find_tables`` runs with the default
# ``snap_tolerance=3``, so H-lines within 3pt of each other (e.g. the
# outer main-table row boundary at y=208 and the inner sub-table
# top at y=211 in fixture 07's nested-row band) snap to their
# midpoint (y=209.5).  Bottom-up's "5" cell at row[5] col[0] then
# emits at y=209.5..248.5 while legacy uses the un-snapped outer
# boundaries y=208..250 (legacy reconstructs row geometry from the
# full-width H-line set directly, not from pdfplumber's snap-tolerant
# table extraction).  All cells in rows 4/5/6 (and 44/45/46 on page
# spanned) cascade-differ by 1.5pt on the shared boundary, driving
# 27 of 27 id divergences in fixture 07 and a proportional share in
# 13_comprehensive's nested-row bands.
#
# The Phase-9 ``_carve_subclusters`` synthetic parent inherits the
# snapped y-extents from its bracket cells, so its bbox matches the
# bracket cells' bbox — the divergence is upstream in ``detect_cells``,
# not in the carve.  Reducing ``snap_tolerance`` below 3 would unblock
# 07/13 parity but risks breaking sub-table detection on fixtures
# where inner H-lines genuinely overdraw the outer row boundary
# (fixtures 02/05/11 and the multi-cell rows of 14b).  A safer fix
# would build line cells directly from the un-snapped visible-edge
# intersections in a new helper, bypassing pdfplumber's snap heuristic
# for the outer table boundary only — but that is a broader
# ``detect_cells`` rewrite outside the allowed Phase-10-prep scope.
# Left as Phase-10+ parity-only residual; no behavioural test relies
# on byte-identical y-bounds in the nested-row bands.
#
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
