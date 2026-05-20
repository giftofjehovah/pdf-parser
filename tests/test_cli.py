import json
from pathlib import Path

from typer.testing import CliRunner

from pdf_parser.cli import app

runner = CliRunner()
SIMPLE = Path("tests/golden/synthetic/01_simple_table/source.pdf")


def test_parse_json_default():
    result = runner.invoke(app, ["parse", str(SIMPLE)])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["kind"] == "document"


def test_parse_markdown():
    result = runner.invoke(app, ["parse", str(SIMPLE), "--format", "markdown"])
    assert result.exit_code == 0
    assert "# Simple Table Example" in result.stdout


def test_parse_html():
    result = runner.invoke(app, ["parse", str(SIMPLE), "--format", "html"])
    assert result.exit_code == 0
    assert "<article>" in result.stdout


def test_parse_chunks():
    result = runner.invoke(app, ["parse", str(SIMPLE), "--format", "chunks"])
    assert result.exit_code == 0
    chunks = json.loads(result.stdout)
    assert isinstance(chunks, list) and chunks


def test_validate_only_exits_zero_on_good_pdf():
    result = runner.invoke(app, ["parse", str(SIMPLE), "--validate-only"])
    assert result.exit_code == 0
