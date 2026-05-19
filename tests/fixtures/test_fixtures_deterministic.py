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
