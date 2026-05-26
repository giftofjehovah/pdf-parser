"""Tests for the --debug bundle: shape, content invariants, and CLI wiring."""

import json
from pathlib import Path

import pypdfium2 as pdfium
from typer.testing import CliRunner

from pdf_parser.cli import app
from pdf_parser.debug import parse_with_debug, write_bundle

runner = CliRunner()
unmixed_runner = CliRunner(mix_stderr=False)

SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")
SPANNING = Path("tests/golden/synthetic/03_page_spanning/source.pdf")


# --- core API ------------------------------------------------------------


def test_parse_with_debug_returns_intermediates_for_each_stage():
    bundle = parse_with_debug(SIMPLE)
    assert bundle.tree.kind == "document"
    assert len(bundle.raw_pages) == 1
    assert len(bundle.segmented) == 1
    assert len(bundle.cells_per_page) == 1
    # 01_simple_table: one 3x3 table, all line-bounded.
    assert len(bundle.cells_per_page[0]) == 9
    assert {c.source for c in bundle.cells_per_page[0]} == {"line"}
    assert len(bundle.tables_pre_stitch) == 1
    assert len(bundle.tables_post_stitch) == 1


def test_parse_with_debug_records_all_stage_timings():
    bundle = parse_with_debug(SIMPLE)
    expected = {"ingest_ms", "segment_ms", "extract_tables_ms",
                "stitch_pages_ms", "build_tree_ms", "total_ms"}
    assert set(bundle.timings_ms.keys()) == expected
    # total is the sum of the parts within a small slop for clock noise.
    parts_sum = sum(v for k, v in bundle.timings_ms.items() if k != "total_ms")
    assert abs(bundle.timings_ms["total_ms"] - parts_sum) < 5.0


def test_parse_with_debug_produces_same_tree_as_pipeline():
    """The debug pipeline must be a behaviour-preserving rerun of parse()."""
    from pdf_parser.pipeline import parse

    bundle = parse_with_debug(SIMPLE)
    canonical = parse(SIMPLE)
    # IDs are content-addressed (sha256 of kind+bbox+text+child_ids),
    # so structural equality is sufficient — and stronger than text equality.
    assert bundle.tree.id == canonical.id


def test_parse_with_debug_captures_cross_page_stitch():
    """A page-spanning fixture must show ONE pre-stitch table per page,
    then ONE post-stitch table merged across both pages."""
    bundle = parse_with_debug(SPANNING)
    assert len(bundle.tables_pre_stitch) == 2  # two fragments, one per page
    assert len(bundle.tables_post_stitch) == 1  # merged
    merged = bundle.tables_post_stitch[0]
    assert isinstance(merged.bbox, list)
    assert {b.page for b in merged.bbox} == {0, 1}


# --- on-disk bundle ------------------------------------------------------


REQUIRED_FILES = (
    "manifest.json",
    "tree.json",
    "validate.json",
    "README.md",
    "stages/01_ingest.json",
    "stages/02_segment.json",
    "stages/03_cells.json",
    "stages/04_tables.json",
    "stages/05_stitch.json",
    "overlays/01_ingest.pdf",
    "overlays/02_segment.pdf",
    "overlays/03_cells.pdf",
    "overlays/04_tables.pdf",
    "overlays/05_stitch.pdf",
    "overlays/06_final.pdf",
)


def test_write_bundle_emits_every_required_file(tmp_path):
    bundle = parse_with_debug(SIMPLE)
    write_bundle(bundle, tmp_path)
    for relpath in REQUIRED_FILES:
        f = tmp_path / relpath
        assert f.is_file(), f"missing {relpath}"
        assert f.stat().st_size > 0, f"empty {relpath}"
    # one page text dump per page
    pages = sorted((tmp_path / "pages").glob("page_*.txt"))
    assert len(pages) == 1


def test_manifest_records_env_versions_hash_and_counts(tmp_path):
    bundle = parse_with_debug(SIMPLE)
    write_bundle(bundle, tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())

    assert manifest["pdf"]["name"] == "source.pdf"
    assert len(manifest["pdf"]["sha256"]) == 64  # sha256 hex digest length
    assert manifest["pdf"]["page_count"] == 1
    assert manifest["env"]["deps"]["pdfplumber"] == "0.11.4"
    assert manifest["env"]["deps"]["pdf-parser"] != "not installed"
    # Counts reflect the actual tree: one table, three rows, nine cells.
    assert manifest["counts"]["table"] == 1
    assert manifest["counts"]["row"] == 3
    assert manifest["counts"]["cell"] == 9
    assert manifest["stage_summary"]["cells_total"] == 9


def test_validate_json_round_trips_a_validation_report(tmp_path):
    bundle = parse_with_debug(SIMPLE)
    write_bundle(bundle, tmp_path)
    report = json.loads((tmp_path / "validate.json").read_text())
    assert report["passed"] is True
    assert report["errors"] == []
    assert "coverage_missing" in report and "coverage_extra" in report


def test_tree_json_round_trips_through_pydantic(tmp_path):
    """The on-disk tree.json must reload into a valid DocNode with the same id."""
    from pdf_parser.model import DocNode

    bundle = parse_with_debug(SIMPLE)
    write_bundle(bundle, tmp_path)
    reloaded = DocNode.model_validate_json((tmp_path / "tree.json").read_text())
    assert reloaded.id == bundle.tree.id


def test_cells_stage_json_captures_source_and_confidence(tmp_path):
    bundle = parse_with_debug(SIMPLE)
    write_bundle(bundle, tmp_path)
    data = json.loads((tmp_path / "stages" / "03_cells.json").read_text())
    assert len(data) == 1 and data[0]["page"] == 0
    cells = data[0]["cells"]
    assert len(cells) == 9
    assert all("source" in c and "confidence" in c and "bbox" in c for c in cells)
    assert {c["source"] for c in cells} == {"line"}


def test_each_overlay_has_one_page_per_pdf_page(tmp_path):
    bundle = parse_with_debug(SPANNING)
    write_bundle(bundle, tmp_path)
    for name in ("01_ingest", "02_segment", "03_cells", "04_tables", "05_stitch", "06_final"):
        doc = pdfium.PdfDocument(str(tmp_path / "overlays" / f"{name}.pdf"))
        try:
            assert len(doc) == 2, f"{name}: expected 2 pages, got {len(doc)}"
        finally:
            doc.close()


def test_page_text_dump_lists_each_leaf_kind_in_reading_order(tmp_path):
    bundle = parse_with_debug(SIMPLE)
    write_bundle(bundle, tmp_path)
    dump = (tmp_path / "pages" / "page_000.txt").read_text()
    # Heading appears before the paragraph, which appears before cells.
    h_idx = dump.find("Simple Table Example")
    p_idx = dump.find("three columns")
    c_idx = dump.find("Apple")
    assert 0 <= h_idx < p_idx < c_idx


# --- CLI wiring ----------------------------------------------------------


def test_cli_debug_flag_creates_bundle_and_prints_render(tmp_path):
    out_dir = tmp_path / "bundle"
    result = unmixed_runner.invoke(
        app, ["parse", str(SIMPLE), "--debug", str(out_dir), "--format", "json"],
    )
    assert result.exit_code == 0, result.stderr
    # stderr carries the "bundle written" message; stdout carries the JSON render.
    assert "debug bundle written" in result.stderr
    data = json.loads(result.stdout)
    assert data["kind"] == "document"
    assert (out_dir / "manifest.json").is_file()
    assert (out_dir / "overlays" / "06_final.pdf").is_file()


def test_cli_debug_into_existing_dir_overwrites_files(tmp_path):
    """Re-running --debug into the same dir refreshes the artifacts."""
    out_dir = tmp_path / "bundle"
    out_dir.mkdir()
    (out_dir / "notes.txt").write_text("user notes that must survive")

    result = runner.invoke(
        app, ["parse", str(SIMPLE), "--debug", str(out_dir), "-o", str(tmp_path / "tree.json")],
    )
    assert result.exit_code == 0, result.stdout
    assert (out_dir / "notes.txt").read_text() == "user notes that must survive"
    assert (out_dir / "tree.json").is_file()
