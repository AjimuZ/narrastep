"""Write the deterministic FastAPI schema to a requested documentation path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.main import app


def parse_arguments() -> argparse.Namespace:
    """Parse an optional output path suitable for local and CI use."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, nargs="?", default=Path("../docs/openapi.json"))
    return parser.parse_args()


def main() -> None:
    """Serialize the generated schema with stable indentation and key ordering."""
    output = parse_arguments().output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
