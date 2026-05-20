import hashlib
from pathlib import Path

from tests.fixtures.build_pdfs import BUILDERS, GOLDEN_DIR


def _digest(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_simple_table_pdf_is_byte_stable(tmp_path):
    out1 = tmp_path / "a.pdf"
    out2 = tmp_path / "b.pdf"
    BUILDERS["01_simple_table"](out1)
    BUILDERS["01_simple_table"](out2)
    assert _digest(out1) == _digest(out2)


def test_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "01_simple_table" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["01_simple_table"](regen)
    assert _digest(committed) == _digest(regen)


def test_nested_table_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    BUILDERS["02_nested_table"](a)
    BUILDERS["02_nested_table"](b)
    assert _digest(a) == _digest(b)


def test_page_spanning_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    BUILDERS["03_page_spanning"](a)
    BUILDERS["03_page_spanning"](b)
    assert _digest(a) == _digest(b)


def test_multi_column_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["04_multi_column"](a)
    BUILDERS["04_multi_column"](b)
    assert _digest(a) == _digest(b)


def test_sections_lists_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["05_sections_lists"](a)
    BUILDERS["05_sections_lists"](b)
    assert _digest(a) == _digest(b)


def test_page_spanning_no_header_repeat_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["06_page_spanning_no_header_repeat"](a)
    BUILDERS["06_page_spanning_no_header_repeat"](b)
    assert _digest(a) == _digest(b)


def test_page_spanning_with_nested_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["07_page_spanning_with_nested"](a)
    BUILDERS["07_page_spanning_with_nested"](b)
    assert _digest(a) == _digest(b)


def test_page_spanning_subtable_split_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["08_page_spanning_subtable_split"](a)
    BUILDERS["08_page_spanning_subtable_split"](b)
    assert _digest(a) == _digest(b)


def test_mixed_toc_and_spanning_table_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["09_mixed_toc_and_spanning_table"](a)
    BUILDERS["09_mixed_toc_and_spanning_table"](b)
    assert _digest(a) == _digest(b)


def test_merged_cells_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["10_merged_cells"](a)
    BUILDERS["10_merged_cells"](b)
    assert _digest(a) == _digest(b)


def test_merged_cells_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "10_merged_cells" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["10_merged_cells"](regen)
    assert _digest(committed) == _digest(regen)


def test_pl_statement_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["11_pl_statement"](a)
    BUILDERS["11_pl_statement"](b)
    assert _digest(a) == _digest(b)


def test_pl_statement_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "11_pl_statement" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["11_pl_statement"](regen)
    assert _digest(committed) == _digest(regen)


def test_comprehensive_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["13_comprehensive"](a)
    BUILDERS["13_comprehensive"](b)
    assert _digest(a) == _digest(b)


def test_comprehensive_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "13_comprehensive" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["13_comprehensive"](regen)
    assert _digest(committed) == _digest(regen)
