"""Tests for the LLM-prompt renderer.

Covers the four corners that motivated the design:

* Plain page (envelope + anchors + indented HTML+MD).
* Nested table (fixture 02): nested table indented inside parent cell;
  both ids reachable.
* Page-spanning table (fixture 03): same ``data-id`` on both slices;
  ``data-continues-to`` / ``data-continued-from`` / header repeat;
  breadcrumb carries the heading across the page boundary; rows
  partition cleanly (no drops, no duplicates).
* Spanning + nested (fixture 07): outer slice on each page contains
  the nested sub-table that physically lives on that page.
* Rowspan placeholders (fixture 16): all-covered rows are absent from
  the rendered slice; inline paragraph between two sub-tables survives.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pdf_parser.model import DocNode
from pdf_parser.pipeline import parse
from pdf_parser.render.llm_prompt import (
    to_llm_prompt,
    to_llm_prompts,
)

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
NESTED = Path("tests/golden/synthetic/02_nested_table/source.pdf")
SPANNING = Path("tests/golden/synthetic/03_page_spanning/source.pdf")
SPANNING_NESTED = Path(
    "tests/golden/synthetic/07_page_spanning_with_nested/source.pdf"
)
BETWEEN = Path("tests/golden/synthetic/16_text_between_subtables/source.pdf")


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #


def _split_envelope(prompt: str) -> tuple[dict, str]:
    """Parse the YAML-like envelope into a dict; return (envelope, body)."""
    assert prompt.startswith("---\n"), "every prompt must open with ---"
    end = prompt.index("\n---\n", 4)
    head = prompt[4:end]
    body = prompt[end + len("\n---\n"):]
    env: dict = {}
    for line in head.splitlines():
        key, _, val = line.partition(": ")
        if key in ("page", "pages_total"):
            env[key] = int(val)
        else:
            env[key] = json.loads(val)
    return env, body


def _all_ids(tree: DocNode) -> set[str]:
    out: set[str] = set()
    stack = [tree]
    while stack:
        n = stack.pop()
        out.add(n.id)
        stack.extend(n.children)
    return out


_DATA_ID_RE = re.compile(r'data-id="([0-9a-f]{12})"')
_ANCHOR_ID_RE = re.compile(r"<!-- id:([0-9a-f]{12})")


def _ids_in(text: str) -> set[str]:
    return set(_DATA_ID_RE.findall(text)) | set(_ANCHOR_ID_RE.findall(text))


# --------------------------------------------------------------------------- #
# universal invariants                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("pdf", [SIMPLE, NESTED, SPANNING, SPANNING_NESTED, BETWEEN])
def test_every_prompt_has_well_formed_envelope(pdf):
    tree = parse(pdf)
    valid_ids = _all_ids(tree)
    for i, prompt in enumerate(to_llm_prompts(tree)):
        env, body = _split_envelope(prompt)
        assert env["page"] == i
        assert env["pages_total"] == len(tree.children)
        assert isinstance(env["breadcrumb"], list)
        assert isinstance(env["continued_tables"], list)
        # Every id mentioned in the body must exist in the tree.  This
        # protects against the renderer fabricating ids if the format
        # changes in the future.
        unknown = _ids_in(body) - valid_ids
        assert not unknown, f"page {i} emits ids not in tree: {unknown}"


@pytest.mark.parametrize("pdf", [SIMPLE, NESTED, SPANNING, SPANNING_NESTED, BETWEEN])
def test_no_placeholder_rows_leak(pdf):
    """Rowspan-placeholder rows (every cell ``covered``) must never appear
    in the LLM's view — they have no semantic content and would mislead
    chunk-boundary decisions."""
    tree = parse(pdf)
    for prompt in to_llm_prompts(tree):
        # An empty <tr><td data-id="..."></td></tr> with no inner text is
        # a strong signal of a leaked placeholder row.  We forbid the
        # tighter pattern: an entire <tr> whose only <td> is empty.
        assert not re.search(
            r'<tr data-id="[0-9a-f]+">\s*<td data-id="[0-9a-f]+"></td>\s*</tr>',
            prompt,
        )


# --------------------------------------------------------------------------- #
# fixture 02 — nested table                                                   #
# --------------------------------------------------------------------------- #


def test_nested_table_renders_inline_within_cell():
    tree = parse(NESTED)
    [prompt] = to_llm_prompts(tree)
    _, body = _split_envelope(prompt)
    # Outer cells and inner cells both surface
    assert "Outer-Col-1" in body
    assert "sub-A" in body
    # Indented HTML: nested <table> sits deeper than the outer one.
    outer_idx = body.index("<table data-id=")
    inner_idx = body.index("<table data-id=", outer_idx + 1)
    # Inner table line starts further right (it lives inside a <td>).
    outer_line = body[body.rfind("\n", 0, outer_idx) + 1 : outer_idx]
    inner_line = body[body.rfind("\n", 0, inner_idx) + 1 : inner_idx]
    assert len(inner_line) > len(outer_line), (
        f"nested table should be indented further than outer; "
        f"outer leading whitespace={len(outer_line)!r}, "
        f"inner leading whitespace={len(inner_line)!r}"
    )


# --------------------------------------------------------------------------- #
# fixture 03 — page-spanning table                                            #
# --------------------------------------------------------------------------- #


def test_spanning_table_emits_one_slice_per_page():
    tree = parse(SPANNING)
    prompts = to_llm_prompts(tree)
    assert len(prompts) == 2

    env0, body0 = _split_envelope(prompts[0])
    env1, body1 = _split_envelope(prompts[1])

    # Same table id appears on both slices.
    p0_ids = _ids_in(body0)
    p1_ids = _ids_in(body1)
    common = p0_ids & p1_ids
    assert common, "the spanning table's data-id must appear on both pages"

    # Continuation markers
    assert 'data-continues-to="p1"' in body0
    assert 'data-continued-from="p0"' in body1
    assert 'data-header-repeat="true"' in body1
    assert "data-continued-from" not in body0
    assert "data-continues-to" not in body1

    # Heading flows into the breadcrumb on page 1.
    assert env0["breadcrumb"] == []
    assert "Page-Spanning Table" in env1["breadcrumb"]

    # Page 1 envelope lists the continued table.
    assert env1["continued_tables"], "page 1 must advertise the continuation"
    cont = env1["continued_tables"][0]
    assert cont["first_page"] == 0
    assert cont["header"] == ["ID", "Description", "Value"]


def test_spanning_table_rows_partition_with_header_repeated():
    """Every body row (non-header) appears in exactly one slice; the header
    row id appears in both."""
    tree = parse(SPANNING)
    # Locate the table to enumerate its rows.
    page0 = tree.children[0]
    [table] = [c for c in page0.children if c.kind == "table"]
    header_id = table.children[0].id
    body_row_ids = {r.id for r in table.children[1:]}

    p0, p1 = [_split_envelope(p)[1] for p in to_llm_prompts(tree)]
    p0_rows = set(_DATA_ID_RE.findall(p0))
    p1_rows = set(_DATA_ID_RE.findall(p1))

    # Header id is in both.
    assert header_id in p0_rows
    assert header_id in p1_rows

    # Body rows partition cleanly across the two slices.
    p0_body = (p0_rows & body_row_ids)
    p1_body = (p1_rows & body_row_ids)
    assert p0_body.isdisjoint(p1_body), \
        "no body row may appear in two slices"
    assert p0_body | p1_body == body_row_ids, \
        "every body row must appear in exactly one slice"


def test_spanning_table_row_range_attribute():
    tree = parse(SPANNING)
    p0, p1 = [_split_envelope(p)[1] for p in to_llm_prompts(tree)]
    m0 = re.search(r'data-row-range="(\d+)-(\d+)"', p0)
    m1 = re.search(r'data-row-range="(\d+)-(\d+)"', p1)
    assert m0 and m1
    p0_lo, p0_hi = int(m0.group(1)), int(m0.group(2))
    p1_lo, p1_hi = int(m1.group(1)), int(m1.group(2))
    # Ranges are contiguous and non-overlapping.
    assert p0_lo == 0
    assert p1_lo == p0_hi + 1
    # The header row (index 0) lives on page 0; page 1's lo is therefore >= 1.
    assert p1_lo >= 1
    # End of the last slice equals total rows - 1.
    n_rows = parse(SPANNING).children[0].children[-1].attrs.get("n_rows")
    assert p1_hi == n_rows - 1


# --------------------------------------------------------------------------- #
# fixture 07 — spanning outer with nested sub-tables on each page             #
# --------------------------------------------------------------------------- #


def test_spanning_with_nested_renders_subtable_on_correct_page():
    tree = parse(SPANNING_NESTED)
    prompts = to_llm_prompts(tree)
    p0, p1 = [_split_envelope(p)[1] for p in prompts]

    # Each page's nested sub-table is labelled with its own page-marker text.
    assert "p1-A" in p0 and "p1-B" in p0
    assert "p2-A" in p1 and "p2-B" in p1
    # And NOT bleed into the other slice.
    assert "p2-A" not in p0
    assert "p1-A" not in p1

    # Outer table id is the same on both pages (one logical table).
    outer_id = re.search(r'<table data-id="([0-9a-f]{12})"', p0).group(1)
    assert outer_id in p1


# --------------------------------------------------------------------------- #
# fixture 16 — mixed-content cell                                             #
# --------------------------------------------------------------------------- #


def test_between_paragraph_inside_cell_survives():
    tree = parse(BETWEEN)
    [prompt] = to_llm_prompts(tree)
    _, body = _split_envelope(prompt)
    # Both nested sub-tables and the between-paragraph anchor are present.
    assert "Item" in body and "Month" in body
    assert "NOTE:" in body
    assert "between the two sub-tables" in body
    # The paragraph carries its own anchor comment so the LLM can reference it.
    assert re.search(
        r"<!-- id:[0-9a-f]{12} kind:paragraph -->\s+NOTE:",
        body,
    )


def test_between_fixture_placeholder_rows_absent():
    """Fixture 16's outer table contains 8 rowspan-placeholder rows that
    leak into the standard markdown rendering as empty <tr>s.  The
    LLM-prompt renderer must hide them entirely."""
    tree = parse(BETWEEN)
    [prompt] = to_llm_prompts(tree)
    # The outer table claims shape 11x1 from the parser, but the LLM
    # should see only three rows in the rendered slice: header, mixed
    # content, footer.
    body = _split_envelope(prompt)[1]
    # Count <tr> tags at the OUTER table's indent depth (two-space indent).
    outer_tr_count = len(re.findall(r"^  <tr data-id=", body, flags=re.MULTILINE))
    assert outer_tr_count == 3, (
        f"expected 3 visible outer rows, got {outer_tr_count}"
    )


# --------------------------------------------------------------------------- #
# misuse                                                                       #
# --------------------------------------------------------------------------- #


def test_out_of_range_page_raises():
    tree = parse(SIMPLE)
    with pytest.raises(ValueError):
        to_llm_prompt(tree, page_index=99)
    with pytest.raises(ValueError):
        to_llm_prompt(tree, page_index=-1)
