"""Fail the backend image build when the bundled `llama.cpp` server cannot run (P06.1)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.services.llama_server import (
    LLAMA_SERVER_DIR,
    PINNED_LLAMA_SERVER_BUILD,
    PINNED_LLAMA_SERVER_COMMIT,
    LlamaServerUnavailableError,
    check_pinned_llama_server,
)


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse the server folder and the expected build; defaults are the image's pinned values."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, nargs="?", default=LLAMA_SERVER_DIR)
    parser.add_argument("--expect-build", type=int, default=PINNED_LLAMA_SERVER_BUILD)
    parser.add_argument("--expect-commit", default=PINNED_LLAMA_SERVER_COMMIT)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Print the server version and return 0, or print the reason on stderr and return 1."""

    options = parse_arguments(arguments)
    try:
        version = check_pinned_llama_server(
            options.directory,
            expected_build=options.expect_build,
            expected_commit=options.expect_commit,
        )
    except LlamaServerUnavailableError as error:
        print(f"llama-server check failed: {error}", file=sys.stderr)
        return 1
    print(f"llama-server {version.name} (build {version.build}, commit {version.commit}) runs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
