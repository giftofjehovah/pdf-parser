"""Tests for the 11_pl_statement golden fixture.

Covers dense financial-table extraction: 34 rows × 5 tight columns (label +
FY2021–FY2024), 7.5 pt font, right-aligned numerics, parenthetical negatives,
em-dash N/A values, mixed-sign rows, percentage rows, and EPS dollar values.
"""
from pathlib import Path

import pytest

from pdf_parser.stages.extract_tables_v2 import extract_tables

PL = (
    Path(__file__).resolve().parents[1]
    / "golden" / "synthetic" / "11_pl_statement" / "source.pdf"
)

# ---------------------------------------------------------------------------
# Shared fixture — extract once, reuse across all tests in this module.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def table():
    tables = extract_tables(PL)
    assert len(tables) == 1, f"expected 1 table, got {len(tables)}"
    return tables[0]


@pytest.fixture(scope="module")
def grid(table):
    """rows × cols → cell text (None → '')."""
    return [
        [cell.text or "" for cell in row.children]
        for row in table.children
    ]


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------

def test_shape(table):
    assert len(table.children) == 34, "expected 34 rows"
    assert all(len(row.children) == 5 for row in table.children), "every row must have 5 cols"


# ---------------------------------------------------------------------------
# Header row
# ---------------------------------------------------------------------------

def test_column_headers(grid):
    header = grid[0]
    assert header[0] == "Income Statement"
    assert header[1:] == ["FY2021", "FY2022", "FY2023", "FY2024"]


# ---------------------------------------------------------------------------
# Section headers (label column only; numeric cols are empty)
# ---------------------------------------------------------------------------

SECTION_ROWS = {1: "Revenue", 6: "Cost of Revenue", 12: "Operating Expenses",
                20: "Other Items", 29: "Per Share Data"}

@pytest.mark.parametrize("row_idx,label", SECTION_ROWS.items())
def test_section_headers(grid, row_idx, label):
    assert grid[row_idx][0] == label
    assert grid[row_idx][1:] == ["", "", "", ""], \
        f"section-header row {row_idx} numeric cols should be empty"


# ---------------------------------------------------------------------------
# Revenue block (rows 2–5)
# ---------------------------------------------------------------------------

def test_revenue_line_items(grid):
    assert grid[2][0] == "Product Revenue"
    assert grid[3][0] == "Service Revenue"
    assert grid[4][0] == "Other Revenue"


def test_total_revenue_values(grid):
    row = grid[5]
    assert row[0] == "Total Revenue"
    assert row[1] == "15,820"
    assert row[2] == "19,390"
    assert row[3] == "23,590"
    assert row[4] == "28,370"


# ---------------------------------------------------------------------------
# Cost of Revenue / Gross Profit block (rows 7–11)
# ---------------------------------------------------------------------------

def test_parenthetical_negatives_preserved(grid):
    """Parenthetical negative format must survive extraction intact."""
    cogs_row = grid[7]   # Cost of Goods Sold
    assert cogs_row[1] == "(8,240)"
    assert cogs_row[4] == "(13,760)"

    total_cogs = grid[9]  # Total Cost of Revenue
    assert total_cogs[3] == "(12,250)"


def test_gross_profit_values(grid):
    row = grid[10]
    assert row[0] == "Gross Profit"
    assert row[1] == "7,260"
    assert row[2] == "9,060"
    assert row[3] == "11,340"
    assert row[4] == "14,130"


def test_gross_margin_percentage_row(grid):
    row = grid[11]
    assert row[0] == "Gross Margin %"
    assert all("%" in v for v in row[1:]), "all numeric cells should contain '%'"


# ---------------------------------------------------------------------------
# Operating Expenses / EBIT block (rows 13–19)
# ---------------------------------------------------------------------------

def test_total_opex_values(grid):
    row = grid[17]
    assert row[0] == "Total Operating Expenses"
    assert row[1] == "(7,430)"
    assert row[4] == "(12,340)"


def test_mixed_sign_ebit_row(grid):
    """FY2021 is negative, FY2022–FY2024 are positive — must not be swapped."""
    row = grid[18]
    assert row[0] == "Operating Income (EBIT)"
    assert row[1] == "(170)"    # loss
    assert row[2] == "150"      # profit
    assert row[3] == "670"
    assert row[4] == "1,790"


def test_ebit_margin_percentage_row(grid):
    row = grid[19]
    assert row[0] == "EBIT Margin %"
    assert row[1] == "-1.1%"
    assert row[2] == "0.8%"


# ---------------------------------------------------------------------------
# Other Items / Net Income block (rows 21–28)
# ---------------------------------------------------------------------------

def test_other_items_preservation(grid):
    assert grid[21][0] == "Interest Income"
    assert grid[22][0] == "Interest Expense"
    assert grid[23][0] == "Other Income (Expense), net"


def test_em_dash_na_values(grid):
    """Em-dash (—) used for N/A tax values in FY2021 and FY2022."""
    row = grid[26]   # Income Tax Provision
    assert row[0] == "Income Tax Provision"
    assert row[1] == "—"
    assert row[2] == "—"
    assert row[3] == "(96)"
    assert row[4] == "(418)"


def test_net_income_values(grid):
    row = grid[27]
    assert row[0] == "Net Income (Loss)"
    assert row[1] == "(465)"   # loss year
    assert row[2] == "(105)"   # loss year
    assert row[3] == "384"     # first profitable year
    assert row[4] == "1,482"


def test_net_margin_percentage_row(grid):
    row = grid[28]
    assert row[0] == "Net Margin %"
    assert "-" in row[1] and "%" in row[1]   # negative margin
    assert "-" in row[2] and "%" in row[2]
    assert "%" in row[3] and "-" not in row[3]   # positive margin
    assert "%" in row[4] and "-" not in row[4]


# ---------------------------------------------------------------------------
# Per Share Data (rows 30–33)
# ---------------------------------------------------------------------------

def test_eps_dollar_parenthetical_format(grid):
    """EPS cells use '$(0.47)' format — dollar sign must not be stripped."""
    basic = grid[30]
    diluted = grid[31]
    assert basic[0] == "Basic EPS"
    assert basic[1] == "$(0.47)"
    assert basic[2] == "$(0.11)"
    assert basic[3] == "$0.38"
    assert basic[4] == "$1.49"
    assert diluted[1] == "$(0.47)"
    assert diluted[3] == "$0.37"


def test_shares_outstanding_values(grid):
    basic_shares = grid[32]
    diluted_shares = grid[33]
    assert basic_shares[0] == "Wtd-Avg Shares, Basic (M)"
    assert diluted_shares[0] == "Wtd-Avg Shares, Diluted (M)"
    # Sanity-check decimal format retained
    assert basic_shares[3] == "100.2"
    assert diluted_shares[4] == "103.8"


# ---------------------------------------------------------------------------
# Cross-row integrity: adjacent rows must not bleed into each other
# ---------------------------------------------------------------------------

def test_no_cross_row_text_leakage(grid):
    """Spot-check: values from one row must not appear in the adjacent row."""
    # "15,820" is Total Revenue (row 5); it must not appear in the Cost-of-Revenue
    # section header row (row 6) or the first COGS row (row 7).
    total_revenue_val = "15,820"
    assert total_revenue_val not in grid[6], "section header row should not contain revenue total"
    assert total_revenue_val not in grid[7], "COGS row should not contain revenue total"

    # "1,482" is Net Income FY2024 (row 27); must not bleed into Net Margin row (28).
    net_income_val = "1,482"
    assert net_income_val not in grid[28], "net margin % row should not contain net income value"


# ---------------------------------------------------------------------------
# All 34 rows present with non-null label column
# ---------------------------------------------------------------------------

def test_all_row_labels_non_empty(grid):
    empty_label_rows = [i for i, row in enumerate(grid) if not row[0]]
    assert empty_label_rows == [], f"rows with empty labels: {empty_label_rows}"


# ---------------------------------------------------------------------------
# Cell alignment: numeric columns right-aligned, label column left-aligned
# ---------------------------------------------------------------------------


def _aligns(table):
    """rows × cols → align attr ('left'/'right')."""
    return [[c.attrs.get("align", "left") for c in row.children] for row in table.children]


def test_label_column_always_left_aligned(table):
    aligns = _aligns(table)
    for r_idx, row in enumerate(aligns):
        assert row[0] == "left", f"row {r_idx} label cell should be left-aligned"


def test_numeric_columns_right_aligned_in_body_rows(table):
    """Body data rows (not section headers, not the column header) sit
    flush against the right wall — alignment must be detected as 'right'.

    Section-header rows have empty numeric cells → align defaults to 'left';
    the column-header row (row 0) is centered → also 'left'.  All other
    rows carry right-aligned numerics in cols 1-4.
    """
    section_row_idxs = set(SECTION_ROWS)
    aligns = _aligns(table)
    for r_idx, row in enumerate(aligns):
        if r_idx == 0 or r_idx in section_row_idxs:
            continue
        for c_idx in range(1, 5):
            assert row[c_idx] == "right", (
                f"row {r_idx} col {c_idx} should be right-aligned, got {row[c_idx]}"
            )
