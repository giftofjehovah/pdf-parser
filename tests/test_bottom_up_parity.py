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
# Phase 5 outcome (18/19/20 — Phase-10 prep Residual E): the ruled-header
# fixtures reach full id-set parity.  ``detect_cells._ruled_header_body_cells``
# runs per-page after ``_line_cells``: each y-band of >=2 side-by-side line
# cells (the "ruled header") with NO multi-cell band above defines a column
# template.  ``_classify_body`` walks adjacent bands below, accepting a
# chain of single full-width "monster" line cells (fixtures 19/20 idiom) or
# an open body (fixture 18 idiom); any adjacent multi-cell band signals a
# column-structured body (fixture 01 idiom) and the band is skipped to
# avoid duplication.  Body words are then re-binned into the header columns
# with ``shared`` bbox style, monsters dropped, and the union flows into
# ``aggregate_tables`` unchanged.  This handles 13_comprehensive page 12's
# three concurrent ruled headers (Name/Score/Grade open + Region/Q1.. framed
# + Item/Qty/Price strips) -- page-wide gutter detection cannot recover
# them because the three idioms share no consistent column structure.
#
# Parity pass 2 outcome (fixture 23 — full id-set parity):
# the bordered-cell-with-bulleted-prose fixture is a single full-page
# table whose row is split horizontally into Label + Section by a
# visible vertical -- pdfplumber's line strategy emits two cells, one
# row, two columns.  ``_rows_to_celltable``'s 1-row gate
# (``if len(rows) < 2``) was rejecting it; relaxed to
# ``if all(len(r) < 2 for r in rows)`` so 1xN candidates pass while
# 1-row 1-col candidates (lone bordered blocks, split-off footers)
# remain rejected.  Behaviourally a no-op for the other 26 fixtures.
#
# Parity divergence -- fixture 22 (legacy over-emission, bottom-up canonical):
# 22_text_between_adjacent_tables remains xfailed but is structurally
# CORRECT under bottom-up.  Legacy emits 7 tables: the two real tables
# (Collection 4x7 + SVC Region 4x6) plus 5 spurious singletons -- one
# big 1x1 wrapper at y=118..574 with sig=('',), and four 2x1 header-only
# tables ('Dec-24',), ('Mar-25',), ('Jun-25',), ('Sep-25',) at
# y=479..500.  These extras are vestigial single-cell "tables" surfaced
# by the legacy anchor-detector + megatable noise pipeline -- not real
# tables in the source PDF.  Bottom-up emits only the two real tables.
# Comprehensive omnibus is unaffected: the two-table count is what every
# downstream consumer needs.  Left xfailed for the duration of the
# rewrite as a tracking marker; the goldens are re-baked under bottom-up
# in Phase 10 (the flip-default cleanup) and the xfail is dropped at the
# same time.
#
# Comprehensive assertions un-xfailed under Residual E:
#   * ``test_annex_a_open_body_table`` (Name/Score/Grade 5x3)
#   * ``test_annex_a_framed_body_table`` (Region/Q1../Q4 5x5)
#   * ``test_annex_a_row_strips_table`` (Item/Qty/Price 5x3)
#   * ``test_total_table_count`` (22 -> 23)
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
# Parity pass 3 outcome (fixture 16 — full id-set parity):
# ``aggregate_tables._expand_wrapper_with_placeholders`` runs after the
# nested-attachment loop on each 1xN wrapper.  Each nested sub-table whose
# width spans >= 50% of the wrapper's width contributes its row_bboxes y0/y1
# as wrapper H-line positions (mirrors legacy ``_outer_line_ys``'s 50% rule
# in ``extract_tables.py``); positions strictly inside the container row's
# y-range form covered placeholder rows at the wrapper's full width,
# shifting any existing footer / trailing rows down accordingly.  Fixture 17
# is untouched because its inner sub-tables are 180pt wide vs the wrapper's
# 400pt (45% < 50%) -- no qualifying H-lines, no expansion.  Fixtures
# 24/25 + Annex D outer 'Spanning Header' have NO line-detected container
# cell at all --
# pdfplumber's line strategy collapses 24's outer frame into a single
# table sharing internal cells, while 25 and Annex D have no outer rule
# evidence whatsoever.  Legacy promotes those wrappers via the anchor
# detector instead; bottom-up has no equivalent gutter→frame promotion
# yet.  Left as Phase-10+ residual; would need either an outer-rectangle
# synthesiser working on line geometry directly, or a port of the anchor
# detector's borderless-frame promotion into ``detect_cells``.
#
# Parity pass 1 outcome (fixtures 10/21 — full id-set parity):
# ``_apply_rowspan_merge`` re-clusters tall single-cell rows whose ymid
# diverges from their shorter neighbours into the first multi-cell row
# they y-overlap; ``_split_into_tables`` then keeps the table whole by
# tolerating a missing leftmost cell when an earlier row's rowspan
# covers the missing column.  ``_rows_to_celltable``'s rowspan-covered
# slots inherit the sparse-slot bbox (anchor x-range + sub-row y-range)
# from the original column-assignment loop -- byte-identical to legacy's
# ``_logical_grid_from_table`` convention for ALL covered slots, colspan
# and rowspan alike (``extract_tables.py`` lines 184-186 use
# ``(col_x0, row_y0, col_x1, row_y1)``).  The earlier Residual-B post-pass
# that overwrote covered bboxes with the spanning cell's full y-extent
# is removed: it was the residual ~1pt cause of the prior 10/21 parity
# divergence (a rowspan span y=154..190 ends up in row[2] which lives at
# y=154..172, so legacy emits the row[3] covered slot at y=172..190, not
# the span's y=154..190).  Unblocks both fixtures + the
# ``test_merged_cells_*`` and ``test_annex_e_*`` assertions in
# ``tests/test_comprehensive.py`` (6 behavioural + 2 parity fixtures).
#
# Parity pass 4 deferred (fixtures 02 / 07 / 08 / 13 / 26 -- snap-cluster
# y-bound divergence):
# pdfplumber's line-strategy ``find_tables`` runs with the default
# ``snap_tolerance=3``, so H-lines within 3pt of each other (e.g. the
# outer main-table row boundary at y=208 and the inner sub-table top at
# y=211 in fixture 07's nested-row band; y=136/y=139 in fixture 02's
# header/sub-table boundary) snap to their cluster midpoint.  The outer
# table's row y-bounds then drift ~1.5pt off the visible edge, and the
# inner sub-table inherits a different midpoint than legacy's per-cell
# recursive snap on the cropped parent cell.  Cascade-differs every cell
# in the affected nested-row bands.
#
# Attempted fix in this session: ``_unsnap_outer_cell_ys`` -- a per-cell
# post-pass that re-snaps each y-bound to the visible H-line whose width
# best matches the cell (legacy ``_outer_line_ys``'s 50%-of-table-width
# rule applied per cell instead of per table).  Outer cells correctly
# walked back to the un-snapped wide H-line on fixture 02, but inner
# sub-cells whose snapped midpoint already matches legacy's rounded
# midpoint were pushed off it (sub-A: 137.0 -> 139.0), and lattice-filler
# cells around inner sub-tables in 13_comprehensive's hardware inventory
# became spurious empty columns (``('', 'cpu-X', 'cpu-Y', '')``).  Net
# effect: zero parity gain on 02/07/08/13/26, one comprehensive regression
# on ``test_multicolumn_text_not_misidentified_as_table``.  Reverted.
#
# A correct fix needs legacy's two-pass shape: snap-tolerant whole-page
# detection for top-level tables, separate snap-tolerant CROPPED detection
# per parent cell for inner sub-tables.  Bottom-up's single-pass
# whole-page ``_line_cells`` cannot reproduce legacy's cropped-snap
# midpoints without re-running ``find_tables`` per parent cell -- a
# substantial rewrite outside the parity-cleanup scope.  Left as
# Phase-10+ parity-only residual; no behavioural test relies on
# byte-identical y-bounds in the affected nested-row bands.
#
# Phase 10 prep outcome (Residual D — fixtures 17 + Annex D in 13_comprehensive):
# ``detect_cells._frame_cells`` ports the legacy
# ``detect_tables._find_borderless_frames`` pass: a pair of long vertical
# rails plus one or two horizontal cap bands defines a "section frame".
# Per-page synthesised Cells (header band, content container, footer band)
# feed the existing ``_carve_container_frames`` + ``_build_single_col_wrapper``
# infrastructure in ``aggregate_tables`` -- no aggregate-stage change required.
# ``stitch_pages`` then joins per-page wrappers on matching column anchors,
# producing the 4-row stitched outer "Spanning Header" wrapper for Annex D.
#
# Fixture 17 now reaches full id-set parity (per-page wrappers + inner
# sub-tables structurally identical to legacy's pdfplumber+stitch output).
# 13_comprehensive omnibus recovers two more assertions:
# ``test_annex_d_outer_frame_spans_two_pages`` and
# ``test_has_exactly_five_spanning_tables``.
#
# Gates that keep the port from regressing other fixtures:
#   * Outermost rail pair must have NO internal tall rail between it --
#     internal rails are column dividers (fixtures 03/06/07/08/11/26 +
#     13_comprehensive pages 0/2/4-10/12/17) and the outer pair is then
#     NOT a section frame.
#   * No existing line cell may already span the rail pair -- pdfplumber's
#     line strategy already emits the wrapper on fixtures 16/19/20 so
#     duplicate promotion would either lose data on dedupe or split the
#     row cluster.
#   * Header / footer caps require >=2 full-width H-lines near the rail
#     ends.  Pure closed_rect (one top + one bot cap) cannot form a
#     multi-row wrapper from a single content cell -- left as Phase-10+
#     residual for fixtures 22 / 25 (legacy reaches them via the
#     megatable-decomposition pass not in this port).
#
# Parity pass 5 deferred (fixtures 24 / 25 -- megatable decomposition):
# legacy reaches both fixtures via ``detect_tables._try_decompose_megatable``,
# a row-cluster decomposition pass that splits a fused table by finding
# sparse rows (rows whose cells are far apart vertically from the
# surrounding rows) and emitting them as inter-table paragraphs wrapped
# in a 1x1 outer container.
#
# Fixture 24: pdfplumber's line strategy emits the inter-paragraph cell at
# (186, 172, 426, 226) at full table width because the source PDF has
# rules drawn around the paragraph.  Bottom-up fuses Item/Qty (3 rows) +
# paragraph (1 wide row) + Month/Sales (3 rows) into a single 7x2 table;
# legacy decomposes via the wide-paragraph split into a 1x1 wrapper
# hosting two nested 3x2 sub-tables.
#
# Fixture 25: outer rectangle has only vertical rails plus one top + one
# bottom cap (pure closed_rect).  Residual D's borderless-frame port
# explicitly rejects this shape (no header / footer cap-band text), so
# bottom-up sees only the inner sub-tables and emits them as siblings.
# Legacy reaches the 1x1 wrapper via megatable decomposition (which
# treats the rail-bounded region as a wrapper around any inner tables).
#
# Both fixtures would require either porting `_try_decompose_megatable`
# (high regression risk -- the gap-row classifier triggers on any sparse
# row including legitimate ones in fixtures 15/22) or relaxing
# `_frame_cells`'s pure-closed_rect gate (high regression risk -- would
# synthesise spurious wrappers around ordinary multi-column tables that
# share rails).  Both touch the load-bearing rim of the rewrite for
# parity-only gains on 2 fixtures.  Left as Phase-10+ residual; the
# behavioural shape (2 inner sub-tables present) is preserved -- only
# the wrapper hierarchy differs.
#
_XFAIL_CASES: set[str] = {
    "02_nested_table",
    "07_page_spanning_with_nested",
    "08_page_spanning_subtable_split",
    "13_comprehensive",
    "22_text_between_adjacent_tables",
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
