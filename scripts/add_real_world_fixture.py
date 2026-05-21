"""Manage real-world golden fixtures.

Usage:
    # Register a new PDF as a fixture case:
    python scripts/add_real_world_fixture.py --add rw01_latex_paper /path/to/paper.pdf

    # Refresh goldens after an intentional parser change:
    python scripts/add_real_world_fixture.py --update rw01_latex_paper

    # Inspect the current parse output without writing anything:
    python scripts/add_real_world_fixture.py --inspect rw01_latex_paper
"""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json
from scripts.update_goldens import _skeleton, _strip_bbox_noise

REAL_DIR = Path(__file__).resolve().parents[1] / "tests" / "golden" / "real_world"


def _write_case(case_dir: Path) -> None:
    """Parse the PDF and write expected_tree.json + expected_skeleton.json."""
    tree = parse(case_dir / "source.pdf")
    full = _strip_bbox_noise(json.loads(to_json(tree)))
    (case_dir / "expected_tree.json").write_text(
        json.dumps(full, indent=2, sort_keys=True) + "\n"
    )
    (case_dir / "expected_skeleton.json").write_text(
        json.dumps(_skeleton(full), indent=2, sort_keys=True) + "\n"
    )


def cmd_add(name: str, pdf_path: Path) -> None:
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")
    case_dir = REAL_DIR / name
    if (case_dir / "source.pdf").exists():
        raise SystemExit(
            f"{case_dir}/source.pdf already exists. Use --update to refresh its goldens."
        )
    # Parse before touching the final destination so a bad PDF leaves no trace.
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp())
    try:
        tmp_pdf = tmp_dir / "source.pdf"
        shutil.copy2(pdf_path, tmp_pdf)
        tree = parse(tmp_pdf)
        full = _strip_bbox_noise(json.loads(to_json(tree)))
        # Commit to disk only after a successful parse.
        case_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, case_dir / "source.pdf")
        (case_dir / "expected_tree.json").write_text(
            json.dumps(full, indent=2, sort_keys=True) + "\n"
        )
        (case_dir / "expected_skeleton.json").write_text(
            json.dumps(_skeleton(full), indent=2, sort_keys=True) + "\n"
        )
    except Exception:
        shutil.rmtree(case_dir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"Added fixture '{name}'.")
    print("Inspect the skeleton before committing:")
    print(f"  python scripts/add_real_world_fixture.py --inspect {name}")


def cmd_update(name: str) -> None:
    case_dir = REAL_DIR / name
    if not (case_dir / "source.pdf").exists():
        raise SystemExit(f"No source.pdf found at {case_dir}.  Use --add first.")
    _write_case(case_dir)
    print(f"Updated fixture '{name}'.")
    print("Review the diff before committing:")
    print(f"  git diff tests/golden/real_world/{name}/")


def cmd_inspect(name: str) -> None:
    case_dir = REAL_DIR / name
    if not (case_dir / "source.pdf").exists():
        raise SystemExit(f"No source.pdf found at {case_dir}.")
    tree = parse(case_dir / "source.pdf")
    full = _strip_bbox_noise(json.loads(to_json(tree)))
    print(json.dumps(_skeleton(full), indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Manage real-world PDF golden fixtures.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--add",     metavar="NAME", help="register a new PDF")
    mode.add_argument("--update",  metavar="NAME", help="refresh an existing fixture")
    mode.add_argument("--inspect", metavar="NAME", help="print skeleton (read-only)")
    ap.add_argument("pdf", nargs="?", type=Path, help="source PDF path (required for --add)")
    args = ap.parse_args()

    if args.add:
        if not args.pdf:
            ap.error("--add requires a PDF path argument")
        cmd_add(args.add, args.pdf)
    elif args.update:
        if args.pdf:
            ap.error("pdf path is only valid with --add")
        cmd_update(args.update)
    elif args.inspect:
        if args.pdf:
            ap.error("pdf path is only valid with --add")
        cmd_inspect(args.inspect)


if __name__ == "__main__":
    main()
