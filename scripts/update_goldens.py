"""Regenerate expected_tree.json and expected_skeleton.json for golden cases.

Usage:
    python scripts/update_goldens.py [--case <name>] [--all]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pdf_parser.pipeline import parse
from pdf_parser.render.json_ import to_json

ROOT = Path(__file__).resolve().parents[1]
SYNTH = ROOT / "tests" / "golden" / "synthetic"


def _skeleton(node):
    out = {"kind": node["kind"]}
    if node.get("text"):
        out["text"] = node["text"]
    children = node.get("children") or []
    if children:
        out["children"] = [_skeleton(c) for c in children]
    return out


def _strip_bbox_noise(obj):
    """Round bbox floats to 1pt for stable diffs."""
    if isinstance(obj, dict):
        if set(obj.keys()) >= {"page", "x0", "y0", "x1", "y1"}:
            return {**obj,
                    "x0": round(obj["x0"]),
                    "y0": round(obj["y0"]),
                    "x1": round(obj["x1"]),
                    "y1": round(obj["y1"])}
        return {k: _strip_bbox_noise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_strip_bbox_noise(v) for v in obj]
    return obj


def update_case(case_dir: Path) -> None:
    pdf = case_dir / "source.pdf"
    if not pdf.exists():
        raise SystemExit(f"missing {pdf}")
    tree = parse(pdf)
    full = json.loads(to_json(tree))
    full = _strip_bbox_noise(full)
    (case_dir / "expected_tree.json").write_text(json.dumps(full, indent=2, sort_keys=True) + "\n")
    (case_dir / "expected_skeleton.json").write_text(json.dumps(_skeleton(full), indent=2, sort_keys=True) + "\n")
    print(f"updated {case_dir.name}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", help="case name to update")
    ap.add_argument("--all", action="store_true", help="update all cases")
    args = ap.parse_args()
    cases = sorted(SYNTH.iterdir()) if args.all else [SYNTH / args.case] if args.case else []
    if not cases:
        ap.error("specify --case <name> or --all")
    for c in cases:
        update_case(c)


if __name__ == "__main__":
    main()
