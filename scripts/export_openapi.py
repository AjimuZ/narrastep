"""Generate or verify the checked-in Narrastep OpenAPI document."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def serialized_schema(root: Path) -> str:
    """Import the application from the repository and serialize its schema."""
    backend = root / "backend"
    sys.path.insert(0, str(backend))
    from app.main import app  # noqa: PLC0415

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    """Write OpenAPI output or fail when checked-in output has drifted."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    destination = root / "docs" / "openapi.json"
    generated = serialized_schema(root)
    if args.check:
        if not destination.exists() or destination.read_text(encoding="utf-8") != generated:
            print("docs/openapi.json is missing or out of date.", file=sys.stderr)
            return 1
        print("OpenAPI document is current.")
        return 0
    destination.write_text(generated, encoding="utf-8", newline="\n")
    print(f"Wrote {destination.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
