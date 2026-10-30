"""Tests for `scripts/license_inventory.py`, the comparison of installed packages with LICENSES.md.

The comparison is a pure function, so these tests feed it fixed listings and edited copies of the
real document; no container is started. The run against the built image is recorded under P08.5 in
`docs/ROADMAP.md`.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import license_inventory as inventory

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
LICENSES = REPOSITORY / "docs" / "LICENSES.md"
DOCKERFILE = BACKEND / "Dockerfile"
LOCK_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)", re.M)

DOCUMENT = """# Licenses

## Debian packages in the backend image

| Package | Checked version | License | Note |
| --- | --- | --- | --- |
| `ffmpeg` | 7 | GPL-2.0-or-later | |
| `libgomp1` | 14 | GPL-3.0-or-later WITH GCC-exception-3.1 | |

## Backend Python packages

| Package | Version | License | Required by | Note |
| --- | --- | --- | --- | --- |
| `alembic` | 1.20.1 | MIT | direct |  |
| `numpy` | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT | direct |  |
| `packaging` | 26.3 | Apache-2.0 OR BSD-2-Clause | `hatchling` |  |

### Kept behind their newest release

| Component | Locked | Newest | Reason |
| --- | --- | --- | --- |
| `redis` | 6.4.0 | 8.1.0 | x |

## Planned components
"""
DOCKER = "RUN apt-get update \\\n    && apt-get install --yes --no-install-recommends \\\n" + (
    "        ffmpeg \\\n        libgomp1 \\\n    && rm -rf /var/lib/apt/lists/*\n"
)
PYTHON = {"alembic": "1.20.1", "numpy": "2.5.4", "packaging": "26.3", "narrastep-backend": "0.2.0"}
DEBIAN = {"ffmpeg": "7", "libgomp1": "14", "base-files": "13"}


def listing(**changes: dict[str, str]) -> inventory.Listing:
    """Return a consistent listing, with `python` or `debian` replaced by the given table."""
    return inventory.Listing(
        python=changes.get("python", PYTHON), debian=changes.get("debian", DEBIAN)
    )


def real_listing() -> inventory.Listing:
    """Return what the real backend image holds: its runtime lock plus the Dockerfile's apt list."""
    lock = (BACKEND / "requirements.lock").read_text(encoding="utf-8")
    python = {inventory.canonical_name(n): v for n, v in LOCK_LINE.findall(lock)}
    packages = inventory.explicit_apt_packages(DOCKERFILE.read_text(encoding="utf-8"))
    return inventory.Listing(python=python, debian=dict.fromkeys(packages, "1"))


def test_a_consistent_fixture_passes() -> None:
    """Accept installed packages that the document lists with the same versions and licenses."""
    assert inventory.find_problems(DOCUMENT, DOCKER, listing()) == []


def test_an_installed_python_package_missing_from_the_document_fails() -> None:
    """Name the package that the image has and LICENSES.md lacks."""
    problems = inventory.find_problems(
        DOCUMENT, DOCKER, listing(python=PYTHON | {"surprise": "1.0"})
    )
    assert problems == ["Python package surprise 1.0 is installed but not in LICENSES.md"]


def test_a_python_version_that_differs_fails() -> None:
    """Report a document that fell behind the installed version."""
    problems = inventory.find_problems(DOCUMENT, DOCKER, listing(python=PYTHON | {"alembic": "2"}))
    assert problems == ["Python package alembic: installed 2, LICENSES.md says 1.20.1"]


@pytest.mark.parametrize("wrong", ["Proprietary", "SSPL-1.0", "BSL-1.1", "GPL-3.0-or-later", ""])
def test_a_license_outside_the_free_ones_fails(wrong: str) -> None:
    """Refuse non-free licenses, and copyleft ones outside the Debian table."""
    document = DOCUMENT.replace("| 1.20.1 | MIT |", f"| 1.20.1 | {wrong} |")
    problems = inventory.find_problems(document, DOCKER, listing())
    assert len(problems) == 1 and problems[0].startswith("Python package alembic: ")


def test_a_compound_expression_needs_every_and_operand_free() -> None:
    """Fail `MIT AND Proprietary`, and pass `Proprietary OR MIT`."""
    assert inventory.license_problem("MIT AND Proprietary", allow_copyleft=False)
    assert inventory.license_problem("Proprietary OR MIT", allow_copyleft=False) is None
    assert inventory.license_problem("MPL-2.0 AND MIT", allow_copyleft=False) is None
    assert inventory.license_problem("MIT-CMU", allow_copyleft=False) is None
    assert inventory.license_problem("GPL-3.0-or-later WITH Some-exception", allow_copyleft=True)


def test_a_debian_package_missing_from_the_image_or_the_document_fails() -> None:
    """Compare the Dockerfile's apt list with the image and with the Debian table."""
    absent = inventory.find_problems(DOCUMENT, DOCKER, listing(debian={"ffmpeg": "7"}))
    assert absent == ["Debian package libgomp1 is named in the Dockerfile but not installed"]
    undocumented = DOCKER.replace("libgomp1 \\", "libgomp1 \\\n        tesseract-ocr \\")
    problems = inventory.find_problems(
        DOCUMENT, undocumented, listing(debian=DEBIAN | {"tesseract-ocr": "5"})
    )
    assert problems == ["Debian package tesseract-ocr is installed but not in LICENSES.md"]


def test_a_missing_section_is_an_error_not_a_pass() -> None:
    """Raise when a heading was renamed, so the check never passes over an empty table."""
    with pytest.raises(ValueError, match="no section"):
        inventory.find_problems(
            DOCUMENT.replace("## Debian packages", "## Other"), DOCKER, listing()
        )


def test_the_real_document_matches_the_runtime_lock_and_the_dockerfile() -> None:
    """Keep LICENSES.md consistent with the packages the real image installs."""
    document = LICENSES.read_text(encoding="utf-8")
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert inventory.find_problems(document, dockerfile, real_listing()) == []


def test_apt_packages_come_from_the_real_dockerfile() -> None:
    """Read the nine packages the backend Dockerfile names, without flags or the cleanup command."""
    names = inventory.explicit_apt_packages(DOCKERFILE.read_text(encoding="utf-8"))
    assert names[:4] == ["ffmpeg", "ca-certificates", "libgomp1", "tesseract-ocr"]
    assert len(names) == 9 and not any(name.startswith(("-", "&", "rm")) for name in names)


def copy_with(tmp_path: Path, old: str, new: str) -> Path:
    """Write a copy of LICENSES.md with one replaced fragment and return its path."""
    text = LICENSES.read_text(encoding="utf-8")
    assert old in text
    copy = tmp_path / "LICENSES.md"
    copy.write_text(text.replace(old, new, 1), encoding="utf-8")
    return copy


def run_main(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, licenses: Path
) -> tuple[int, str, str]:
    """Run the command line on the real listing and a document copy; return code, stdout, stderr."""
    installed = tmp_path / "installed.json"
    found = real_listing()
    installed.write_text(json.dumps({"python": found.python, "debian": found.debian}))
    code = inventory.main(["--installed", str(installed), "--licenses", str(licenses)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_command_passes_on_the_real_document(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Exit 0 and say how many packages matched."""
    code, out, err = run_main(capsys, tmp_path, LICENSES)
    assert (code, err) == (0, "")
    assert out.startswith("License inventory passed")


def test_the_command_fails_on_a_wrong_license_entry(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Exit 1 and name the package when a copy of the document gets a non-free license."""
    before = LICENSES.read_text(encoding="utf-8")
    copy = copy_with(
        tmp_path, "| `alembic` | 1.20.1 | MIT |", "| `alembic` | 1.20.1 | Proprietary |"
    )
    code, _, err = run_main(capsys, tmp_path, copy)
    assert code == 1
    assert "Python package alembic: 'Proprietary' is outside the free licenses" in err
    assert LICENSES.read_text(encoding="utf-8") == before


def test_the_command_fails_when_a_package_row_is_removed(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Exit 1 when a copy of the document lacks a package the image installs."""
    copy = copy_with(tmp_path, "| `tomlkit` | 0.15.2 | MIT | `hatchling` |  |\n", "")
    code, _, err = run_main(capsys, tmp_path, copy)
    assert code == 1
    assert "Python package tomlkit 0.15.2 is installed but not in LICENSES.md" in err


def test_the_command_exits_two_for_an_unreadable_listing(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Distinguish input that cannot be read (2) from a real difference (1)."""
    broken = tmp_path / "installed.json"
    broken.write_text("{}")
    assert inventory.main(["--installed", str(broken)]) == 2
    assert "not a package listing" in capsys.readouterr().err


def test_the_image_listing_runs_the_engine_with_a_fixed_argument_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start the container with the script on standard input and parse the JSON it prints."""
    calls: list[tuple[list[str], str]] = []

    def fake_run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        """Record the call and answer like the script's `--list` would."""
        calls.append((command, str(options["input"])))
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"python": {"a": "1"}, "debian": {"b": "2"}}), ""
        )

    monkeypatch.setattr(inventory.subprocess, "run", fake_run)
    found = inventory.listing_from_image("example:1", engine="podman")
    assert (dict(found.python), dict(found.debian)) == ({"a": "1"}, {"b": "2"})
    command, source = calls[0]
    assert command[:5] == ["podman", "run", "--rm", "-i", "--entrypoint"]
    assert command[-4:] == ["example:1", "-I", "-", "--list"]
    assert "def main" in source


def test_list_mode_runs_from_standard_input_without_a_file_path(tmp_path: Path) -> None:
    """Run the script the way `--image` does inside a container: `python -I - --list` on stdin.

    There `__file__` is missing, so the module must import without resolving a repository path;
    the working directory is outside the repository to prove `--list` needs none.
    """
    source = (BACKEND / "scripts" / "license_inventory.py").read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-I", "-", "--list"],
        input=source,
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    found = inventory.listing_from_json(result.stdout)
    assert "pytest" in found.python


def test_a_failing_engine_is_reported_as_unusable_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turn a missing engine or a failed container into `RuntimeError`, never a traceback."""

    def missing(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        """Behave like an absent engine binary."""
        raise FileNotFoundError("docker")

    monkeypatch.setattr(inventory.subprocess, "run", missing)
    with pytest.raises(RuntimeError, match="cannot run docker"):
        inventory.listing_from_image("example:1")


def test_the_script_imports_only_the_standard_library() -> None:
    """Keep the script runnable in any image, and free of network clients."""
    tree = ast.parse((BACKEND / "scripts" / "license_inventory.py").read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert imported <= sys.stdlib_module_names
    assert imported.isdisjoint({"socket", "urllib", "http", "ssl", "ftplib", "smtplib"})
