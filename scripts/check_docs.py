"""Validate that repository-relative Markdown links resolve to existing files."""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Image targets are repository documentation dependencies too, so one expression validates both
# ordinary links and the screenshots embedded in the README.
LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
IGNORED_PREFIXES = ("http://", "https://", "mailto:", "#")


def markdown_files(root: Path) -> list[Path]:
    """Return tracked-style Markdown inputs without generated dependency trees."""
    # `.claude` holds third-party agent skills whose internal links are owned upstream.
    return [
        path
        for path in root.rglob("*.md")
        if not {"node_modules", ".git", ".venv", ".claude"}.intersection(path.parts)
    ]


def unresolved_links(root: Path) -> list[str]:
    """Return readable errors for repository-relative links that do not exist."""
    failures: list[str] = []
    for document in markdown_files(root):
        content = document.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(content):
            target = raw_target.strip().strip("<>")
            if target.startswith(IGNORED_PREFIXES):
                continue
            path_text = target.split("#", maxsplit=1)[0]
            if not path_text:
                continue
            resolved = (document.parent / path_text).resolve()
            if not resolved.exists():
                failures.append(f"{document.relative_to(root)} -> {target}")
    return failures


def main() -> int:
    """Run the documentation link audit and return a shell-friendly status."""
    root = Path(__file__).resolve().parents[1]
    failures = unresolved_links(root)
    if failures:
        print("Unresolved documentation links:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("Documentation links are valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
