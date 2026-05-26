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


def test_text_between_subtables_spanning_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["17_text_between_subtables_spanning"](a)
    BUILDERS["17_text_between_subtables_spanning"](b)
    assert _digest(a) == _digest(b)


def test_text_between_subtables_spanning_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "17_text_between_subtables_spanning" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["17_text_between_subtables_spanning"](regen)
    assert _digest(committed) == _digest(regen)


def test_ruled_header_open_body_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["18_ruled_header_open_body"](a)
    BUILDERS["18_ruled_header_open_body"](b)
    assert _digest(a) == _digest(b)


def test_ruled_header_open_body_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "18_ruled_header_open_body" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["18_ruled_header_open_body"](regen)
    assert _digest(committed) == _digest(regen)


def test_ruled_header_framed_body_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["19_ruled_header_framed_body"](a)
    BUILDERS["19_ruled_header_framed_body"](b)
    assert _digest(a) == _digest(b)


def test_ruled_header_framed_body_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "19_ruled_header_framed_body" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["19_ruled_header_framed_body"](regen)
    assert _digest(committed) == _digest(regen)


def test_ruled_header_row_strips_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["20_ruled_header_row_strips"](a)
    BUILDERS["20_ruled_header_row_strips"](b)
    assert _digest(a) == _digest(b)


def test_ruled_header_row_strips_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "20_ruled_header_row_strips" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["20_ruled_header_row_strips"](regen)
    assert _digest(committed) == _digest(regen)


def test_vertical_merge_invisible_lines_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["21_vertical_merge_invisible_lines"](a)
    BUILDERS["21_vertical_merge_invisible_lines"](b)
    assert _digest(a) == _digest(b)


def test_vertical_merge_invisible_lines_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "21_vertical_merge_invisible_lines" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["21_vertical_merge_invisible_lines"](regen)
    assert _digest(committed) == _digest(regen)

def test_bordered_cell_with_bulleted_prose_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["23_bordered_cell_with_bulleted_prose"](a)
    BUILDERS["23_bordered_cell_with_bulleted_prose"](b)
    assert _digest(a) == _digest(b)


def test_bordered_cell_with_bulleted_prose_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "23_bordered_cell_with_bulleted_prose" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["23_bordered_cell_with_bulleted_prose"](regen)
    assert _digest(committed) == _digest(regen)

def test_borderless_long_text_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["14b_borderless_long_text"](a)
    BUILDERS["14b_borderless_long_text"](b)
    assert _digest(a) == _digest(b)


def test_borderless_long_text_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "14b_borderless_long_text" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["14b_borderless_long_text"](regen)
    assert _digest(committed) == _digest(regen)


def test_borderless_long_text_spanning_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["14c_borderless_long_text_spanning"](a)
    BUILDERS["14c_borderless_long_text_spanning"](b)
    assert _digest(a) == _digest(b)


def test_borderless_long_text_spanning_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "14c_borderless_long_text_spanning" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["14c_borderless_long_text_spanning"](regen)
    assert _digest(committed) == _digest(regen)

def test_subtable_flush_outer_edges_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["24_subtable_flush_outer_edges"](a)
    BUILDERS["24_subtable_flush_outer_edges"](b)
    assert _digest(a) == _digest(b)


def test_subtable_flush_outer_edges_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "24_subtable_flush_outer_edges" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["24_subtable_flush_outer_edges"](regen)
    assert _digest(committed) == _digest(regen)

def test_subtable_flush_outer_vertical_only_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["25_subtable_flush_outer_vertical_only"](a)
    BUILDERS["25_subtable_flush_outer_vertical_only"](b)
    assert _digest(a) == _digest(b)


def test_subtable_flush_outer_vertical_only_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "25_subtable_flush_outer_vertical_only" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["25_subtable_flush_outer_vertical_only"](regen)
    assert _digest(committed) == _digest(regen)

def test_spanning_subtable_flush_at_break_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["26_spanning_subtable_flush_at_break"](a)
    BUILDERS["26_spanning_subtable_flush_at_break"](b)
    assert _digest(a) == _digest(b)


def test_spanning_subtable_flush_at_break_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "26_spanning_subtable_flush_at_break" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["26_spanning_subtable_flush_at_break"](regen)
    assert _digest(committed) == _digest(regen)


def test_ruled_header_prose_body_cell_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["27_ruled_header_prose_body_cell"](a)
    BUILDERS["27_ruled_header_prose_body_cell"](b)
    assert _digest(a) == _digest(b)


def test_ruled_header_prose_body_cell_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "27_ruled_header_prose_body_cell" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["27_ruled_header_prose_body_cell"](regen)
    assert _digest(committed) == _digest(regen)


def test_title_cover_with_meta_lines_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["28_title_cover_with_meta_lines"](a)
    BUILDERS["28_title_cover_with_meta_lines"](b)
    assert _digest(a) == _digest(b)


def test_title_cover_with_meta_lines_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "28_title_cover_with_meta_lines" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["28_title_cover_with_meta_lines"](regen)
    assert _digest(committed) == _digest(regen)


def test_headerless_keyvalue_table_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["29_headerless_keyvalue_table"](a)
    BUILDERS["29_headerless_keyvalue_table"](b)
    assert _digest(a) == _digest(b)


def test_headerless_keyvalue_table_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "29_headerless_keyvalue_table" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["29_headerless_keyvalue_table"](regen)
    assert _digest(committed) == _digest(regen)


def test_label_rowspan_bulleted_rows_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["30_label_rowspan_bulleted_rows"](a)
    BUILDERS["30_label_rowspan_bulleted_rows"](b)
    assert _digest(a) == _digest(b)


def test_label_rowspan_bulleted_rows_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "30_label_rowspan_bulleted_rows" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["30_label_rowspan_bulleted_rows"](regen)
    assert _digest(committed) == _digest(regen)


def test_label_with_inline_bullet_cell_pdf_is_byte_stable(tmp_path):
    a = tmp_path / "a.pdf"; b = tmp_path / "b.pdf"
    BUILDERS["31_label_with_inline_bullet_cell"](a)
    BUILDERS["31_label_with_inline_bullet_cell"](b)
    assert _digest(a) == _digest(b)


def test_label_with_inline_bullet_cell_committed_fixture_matches_regeneration(tmp_path):
    committed = GOLDEN_DIR / "31_label_with_inline_bullet_cell" / "source.pdf"
    assert committed.exists(), "run `python -m tests.fixtures.build_pdfs` first"
    regen = tmp_path / "regen.pdf"
    BUILDERS["31_label_with_inline_bullet_cell"](regen)
    assert _digest(committed) == _digest(regen)
