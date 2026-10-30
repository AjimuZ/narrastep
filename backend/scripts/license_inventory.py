"""Compare the packages installed in the backend image with docs/LICENSES.md (roadmap item I-15).

Scope, kept deliberately bounded: every Python distribution installed in the image, and the Debian
packages that `backend/Dockerfile` installs by name with `apt-get install`. Transitive Debian
packages and the Alpine packages of the web and broker images are not listed one by one; their
images are pinned by digest and recorded in the "Container images" table instead.

Usage:
    python scripts/license_inventory.py --image narrastep-ralph-backend:dev
    python scripts/license_inventory.py --list > installed.json   # inside the container
    python scripts/license_inventory.py --installed installed.json

The script uses the standard library only and makes no network request; `--image` only starts the
local container engine with a fixed argument list.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

PYTHON_HEADING = "## Backend Python packages"
DEBIAN_HEADING = "## Debian packages in the backend image"

# SPDX identifiers allowed by "Rules for new dependencies" in docs/LICENSES.md.
FREE_LICENSES = frozenset(
    {
        "0BSD",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC0-1.0",
        "ISC",
        "MIT",
        "MIT-0",
        "MIT-CMU",
        "MPL-2.0",
        "PSF-2.0",
        "Public-Domain",
        "SIL-OFL-1.1",
        "Zlib",
    }
)
# Allowed only for programs the application starts as subprocesses or libraries with a linking
# exception, so only the Debian table may use them.
COPYLEFT_LICENSES = frozenset(
    {
        "GPL-2.0-or-later",
        "GPL-3.0-or-later",
        "LGPL-2.1-or-later",
        "LGPL-3.0-or-later",
    }
)
# The project's own distribution, published under the repository's MIT License, is not a dependency.
OWN_PACKAGES = frozenset({"narrastep-backend"})
# Exceptions that may follow `WITH`; they only widen what a license allows.
FREE_EXCEPTIONS = frozenset({"GCC-exception-3.1"})

TABLE_ROW = re.compile(r"^\| `([A-Za-z0-9._+-]+)` \| ([^|]*?) \| ([^|]*?) \|", re.M)
APT_INSTALL = re.compile(r"apt-get install\b((?:[^\n]*\\\n)*[^\n]*)")


@dataclass(frozen=True)
class Listing:
    """Packages found in one image: canonical Python names and Debian names, each with a version."""

    python: Mapping[str, str]
    debian: Mapping[str, str]


def canonical_name(name: str) -> str:
    """Return the PEP 503 form of a Python package name, as `docs/LICENSES.md` writes it."""
    return re.sub(r"[-_.]+", "-", name).lower()


def installed_here() -> Listing:
    """Return the Python distributions and `dpkg` packages of the running environment.

    A missing `dpkg-query` yields no Debian packages, so the listing of a non-Debian host is
    reported as missing them rather than crashing.
    """
    python = {
        canonical_name(distribution.metadata["Name"]): distribution.version
        for distribution in metadata.distributions()
        if distribution.metadata["Name"]
    }
    debian: dict[str, str] = {}
    try:
        result = subprocess.run(
            ["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${db:Status-Abbrev}\n"],
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )
    except FileNotFoundError, subprocess.TimeoutExpired:
        return Listing(python, debian)
    for line in result.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[2].startswith("ii"):
            debian[fields[0]] = fields[1]
    return Listing(python, debian)


def listing_from_json(text: str) -> Listing:
    """Parse the output of `--list`; raises `ValueError` when it has another shape."""
    try:
        data = json.loads(text)
        python, debian = data["python"], data["debian"]
        if not all(
            isinstance(table, dict) and all(isinstance(v, str) for v in table.values())
            for table in (python, debian)
        ):
            raise TypeError("package tables must map names to version strings")
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"not a package listing: {error}") from error
    return Listing(python, debian)


def listing_from_image(image: str, engine: str = "docker") -> Listing:
    """Run this script's `--list` inside `image` and return what it reports.

    The source goes in through standard input, so the image does not need to contain it. Raises
    `RuntimeError` when the engine cannot start the container or its output is not a listing.
    """
    source = Path(__file__).read_text(encoding="utf-8")
    command = [engine, "run", "--rm", "-i", "--entrypoint", "python", image, "-I", "-", "--list"]
    try:
        result = subprocess.run(
            command, input=source, capture_output=True, text=True, check=False, timeout=300
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(f"cannot run {engine}: {error}") from error
    if result.returncode != 0:
        raise RuntimeError(f"{engine} run failed ({result.returncode}): {result.stderr.strip()}")
    try:
        return listing_from_json(result.stdout)
    except ValueError as error:
        raise RuntimeError(str(error)) from error


def section(document: str, heading: str, until: str | None = None) -> str:
    """Return the text from `heading` to `until` (or to the next `## ` heading).

    Raises `ValueError` when the heading is absent, so a renamed section cannot turn the check
    into a pass over nothing.
    """
    start = document.find(f"\n{heading}\n")
    if start < 0:
        raise ValueError(f"docs/LICENSES.md has no section '{heading}'")
    start += 1
    stop = document.find(f"\n{until}", start) if until else document.find("\n## ", start + 1)
    return document[start : stop if stop >= 0 else len(document)]


def parse_table(text: str) -> dict[str, tuple[str, str]]:
    """Map the package name of each three-column table row to its `(version, license)` cells."""
    return {name: (version, license_) for name, version, license_ in TABLE_ROW.findall(text)}


def explicit_apt_packages(dockerfile: str) -> list[str]:
    """Return the package names that the Dockerfile's `apt-get install` commands name."""
    names: list[str] = []
    for match in APT_INSTALL.finditer(dockerfile):
        for word in match[1].replace("\\\n", " ").split():
            if word == "&&":
                break
            if not word.startswith("-"):
                names.append(word)
    return names


def license_problem(expression: str, *, allow_copyleft: bool) -> str | None:
    """Return why `expression` is not a free license, or None when it is.

    Supports SPDX-style `AND`, `OR`, `WITH` and parentheses-free expressions. `AND` needs every
    operand free; `OR` needs one. Copyleft licenses count as free only with `allow_copyleft`.
    """
    allowed = FREE_LICENSES | (COPYLEFT_LICENSES if allow_copyleft else frozenset())
    text = expression.strip()
    if not text:
        return "no license given"
    for alternative in re.split(r"\s+OR\s+", text):
        unknown = []
        for operand in re.split(r"\s+AND\s+", alternative):
            identifier, _, exception = operand.strip().partition(" WITH ")
            if identifier not in allowed or (exception and exception not in FREE_EXCEPTIONS):
                unknown.append(operand.strip())
        if not unknown:
            return None
    return f"'{expression}' is outside the free licenses"


def find_problems(licenses: str, dockerfile: str, installed: Listing) -> list[str]:
    """Return every difference between the documents and the installed packages, sorted.

    Reports: an installed Python package that the document lacks or lists at another version; an
    explicitly installed Debian package that is missing from the image or the document; and any
    listed row whose license is outside the free ones.
    """
    problems: list[str] = []
    python_rows = parse_table(section(licenses, PYTHON_HEADING, until="### Kept behind"))
    debian_rows = parse_table(section(licenses, DEBIAN_HEADING))
    if not python_rows:
        problems.append(f"'{PYTHON_HEADING}' lists no package")
    for name, version in installed.python.items():
        if name in OWN_PACKAGES:
            continue
        row = python_rows.get(name)
        if row is None:
            problems.append(f"Python package {name} {version} is installed but not in LICENSES.md")
        elif row[0] != version:
            problems.append(
                f"Python package {name}: installed {version}, LICENSES.md says {row[0]}"
            )
    for name, (_, license_) in python_rows.items():
        if reason := license_problem(license_, allow_copyleft=False):
            problems.append(f"Python package {name}: {reason}")
    for name in explicit_apt_packages(dockerfile):
        if name not in installed.debian:
            problems.append(f"Debian package {name} is named in the Dockerfile but not installed")
        if name not in debian_rows:
            problems.append(f"Debian package {name} is installed but not in LICENSES.md")
    for name, (_, license_) in debian_rows.items():
        if reason := license_problem(license_, allow_copyleft=True):
            problems.append(f"Debian package {name}: {reason}")
    return sorted(problems)


def repository_file(*parts: str) -> Path:
    """Return a path below the repository that holds this file (`backend/scripts/` is two deep).

    Resolved on demand, never at import: inside a container the script arrives on standard input,
    where `__file__` does not exist, and `--list` needs no repository path.
    """
    return Path(__file__).resolve().parents[2].joinpath(*parts)


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse the source of the package listing and the documents to compare it with.

    `--licenses` and `--dockerfile` stay None when absent; `main` then falls back to the
    repository's own files.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="image to inspect by starting a container")
    source.add_argument("--installed", help="file written by --list; '-' reads standard input")
    source.add_argument("--list", action="store_true", help="print this environment as JSON")
    parser.add_argument("--engine", default="docker", help="container engine for --image")
    parser.add_argument("--licenses", type=Path, help="default: docs/LICENSES.md")
    parser.add_argument("--dockerfile", type=Path, help="default: backend/Dockerfile")
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Return 0 when the document matches, 1 on differences, 2 when the input cannot be read."""
    options = parse_arguments(arguments)
    if options.list:
        found = installed_here()
        print(json.dumps({"python": found.python, "debian": found.debian}, sort_keys=True))
        return 0
    try:
        if options.image:
            installed = listing_from_image(options.image, options.engine)
        else:
            if options.installed == "-":
                text = sys.stdin.read()
            else:
                text = Path(options.installed).read_text(encoding="utf-8")
            installed = listing_from_json(text)
        dockerfile_path = options.dockerfile or repository_file("backend", "Dockerfile")
        licenses_path = options.licenses or repository_file("docs", "LICENSES.md")
        dockerfile = dockerfile_path.read_text(encoding="utf-8")
        licenses = licenses_path.read_text(encoding="utf-8")
        problems = find_problems(licenses, dockerfile, installed)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"License inventory could not run: {error}", file=sys.stderr)
        return 2
    if problems:
        print("License inventory failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(
        f"License inventory passed: {len(installed.python)} Python distributions and "
        f"{len(explicit_apt_packages(dockerfile))} Debian packages match docs/LICENSES.md."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
