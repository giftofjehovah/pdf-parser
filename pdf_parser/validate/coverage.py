"""Layer 1 coverage check: leaf-text multiset == page-text multiset (mod whitespace + whitelist)."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from pdf_parser.model import DocNode

# Whitelist of patterns we expect to drop from extracted text (page numbers, repeated headers).
DROP_PATTERNS = [
    re.compile(r"^Page \d+( of \d+)?$"),
    re.compile(r"^\d+$"),  # bare page numbers
]


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _leaf_text(tree: DocNode) -> str:
    parts: list[str] = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.text:
            parts.append(node.text)
        stack.extend(reversed(node.children))
    return " ".join(parts)


def _filter_drops(s: str) -> str:
    keep_lines: list[str] = []
    for line in s.splitlines():
        line_stripped = line.strip()
        if any(p.match(line_stripped) for p in DROP_PATTERNS):
            continue
        keep_lines.append(line)
    return "\n".join(keep_lines)


@dataclass
class CoverageDiff:
    missing: str  # chars present in raw but not in tree
    extra: str    # chars present in tree but not in raw


def coverage_diff(tree: DocNode, raw_text: str) -> CoverageDiff:
    raw = _normalize(_filter_drops(raw_text))
    leaf = _normalize(_leaf_text(tree))
    raw_counts = Counter(raw)
    leaf_counts = Counter(leaf)
    missing = raw_counts - leaf_counts
    extra = leaf_counts - raw_counts
    return CoverageDiff(
        missing="".join(sorted(missing.elements())),
        extra="".join(sorted(extra.elements())),
    )


def coverage_ok(tree: DocNode, raw_text: str) -> bool:
    d = coverage_diff(tree, raw_text)
    return d.missing == ""
