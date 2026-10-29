"""Bundled `llama.cpp` server checks (P06.1): library path, version parsing and build pin.

A small shell script stands in for `llama-server`, so these tests need neither the real binary
nor Docker. The image build runs the same check against the real server. The scripts are written
to pytest's temporary folder, or beside this file when that folder is mounted `noexec`.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.services.llama_server import (
    PINNED_LLAMA_SERVER_BUILD,
    PINNED_LLAMA_SERVER_COMMIT,
    LlamaServerUnavailableError,
    LlamaServerVersion,
    check_pinned_llama_server,
    llama_server_environment,
    read_llama_server_version,
)
from scripts import check_llama_server

PINNED_LINE = (
    f"version: 0.5.0 (build {PINNED_LLAMA_SERVER_BUILD}, commit {PINNED_LLAMA_SERVER_COMMIT})"
)


@pytest.fixture
def tmp_path(tmp_path: Path) -> Iterator[Path]:
    """Return an empty folder where scripts can run; it is removed after the test."""

    if not os.statvfs(tmp_path).f_flag & os.ST_NOEXEC:
        yield tmp_path
        return
    folder = Path(tempfile.mkdtemp(prefix=".llama-server-test-", dir=Path(__file__).parent))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def fake_server(directory: Path, body: str) -> Path:
    """Write an executable `llama-server` shell script with `body` into `directory`."""

    directory.mkdir(parents=True, exist_ok=True)
    executable = directory / "llama-server"
    executable.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def test_environment_puts_the_server_folder_first_and_keeps_the_rest() -> None:
    directory = Path("/opt/llama.cpp")

    assert llama_server_environment(directory, {"PATH": "/bin"}) == {
        "PATH": "/bin",
        "LD_LIBRARY_PATH": "/opt/llama.cpp",
    }
    assert llama_server_environment(directory, {"LD_LIBRARY_PATH": "/usr/local/lib"}) == {
        "LD_LIBRARY_PATH": "/opt/llama.cpp:/usr/local/lib"
    }


def test_environment_defaults_to_a_copy_of_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)

    environment = llama_server_environment(Path("/opt/llama.cpp"))

    assert environment["LD_LIBRARY_PATH"] == "/opt/llama.cpp"
    assert "LD_LIBRARY_PATH" not in os.environ


def test_version_is_read_from_standard_error_with_the_library_path_and_folder(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "llama.cpp"
    fake_server(
        directory,
        f'echo "args=$*" >&2\necho "lib=$LD_LIBRARY_PATH" >&2\necho "cwd=$(pwd)" >&2\n'
        f'echo "{PINNED_LINE}" >&2\necho "built with GNU 13.3.0 for Linux x86_64" >&2',
    )

    version = read_llama_server_version(directory)

    assert version == LlamaServerVersion(
        name="0.5.0", build=PINNED_LLAMA_SERVER_BUILD, commit=PINNED_LLAMA_SERVER_COMMIT
    )


def test_version_check_passes_only_the_version_flag_and_scoped_settings(tmp_path: Path) -> None:
    directory = tmp_path / "llama.cpp"
    # The script prints the version only when it receives exactly the expected argument, library
    # path and working directory, so a wrong call fails with "no version line".
    fake_server(
        directory,
        f'[ "$*" = "--version" ] && [ "${{LD_LIBRARY_PATH%%:*}}" = "{directory}" ] '
        f'&& [ "$(pwd)" = "{directory}" ] && echo "{PINNED_LINE}"',
    )

    assert read_llama_server_version(directory).build == PINNED_LLAMA_SERVER_BUILD


def test_missing_server_is_reported(tmp_path: Path) -> None:
    with pytest.raises(LlamaServerUnavailableError, match="not installed"):
        read_llama_server_version(tmp_path)


def test_server_that_cannot_start_is_reported(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o644)

    with pytest.raises(LlamaServerUnavailableError, match="could not be started"):
        read_llama_server_version(tmp_path)


def test_failing_server_is_reported_without_its_output(tmp_path: Path) -> None:
    # A missing shared library makes the dynamic loader print the library name and exit with 127.
    fake_server(tmp_path, 'echo "error while loading shared libraries: libgomp.so.1" >&2\nexit 127')

    with pytest.raises(LlamaServerUnavailableError) as raised:
        read_llama_server_version(tmp_path)

    assert str(raised.value) == "llama-server --version exited with code 127."


def test_output_without_a_version_line_is_reported(tmp_path: Path) -> None:
    fake_server(tmp_path, 'echo "usage: llama-server [options]"')

    with pytest.raises(LlamaServerUnavailableError, match="no version line"):
        read_llama_server_version(tmp_path)


def test_server_that_hangs_is_stopped(tmp_path: Path) -> None:
    fake_server(tmp_path, "exec sleep 5")

    with pytest.raises(LlamaServerUnavailableError, match="timed out"):
        read_llama_server_version(tmp_path, timeout_seconds=0.2)


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_timeout_is_refused(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError):
        read_llama_server_version(tmp_path, timeout_seconds=timeout)


def test_pinned_build_with_a_longer_commit_hash_passes(tmp_path: Path) -> None:
    fake_server(
        tmp_path,
        f'echo "version: 0.5.0 (build {PINNED_LLAMA_SERVER_BUILD}, '
        f'commit {PINNED_LLAMA_SERVER_COMMIT}5305b828)" >&2',
    )

    assert check_pinned_llama_server(tmp_path).build == PINNED_LLAMA_SERVER_BUILD


@pytest.mark.parametrize(
    "line",
    [
        f"version: 0.4.1 (build 10964, commit {PINNED_LLAMA_SERVER_COMMIT})",
        f"version: 0.5.0 (build {PINNED_LLAMA_SERVER_BUILD}, commit b29c606e2)",
    ],
)
def test_other_build_or_commit_is_refused(tmp_path: Path, line: str) -> None:
    fake_server(tmp_path, f'echo "{line}" >&2')

    with pytest.raises(LlamaServerUnavailableError, match="expected build 11146"):
        check_pinned_llama_server(tmp_path)


def test_build_check_script_reports_success_and_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_server(tmp_path / "good", f'echo "{PINNED_LINE}" >&2')
    fake_server(tmp_path / "old", 'echo "version: 0.4.1 (build 10964, commit b29c606e2)" >&2')

    assert check_llama_server.main([str(tmp_path / "good")]) == 0
    assert "llama-server 0.5.0 (build 11146, commit 7fe450e19) runs." in capsys.readouterr().out
    assert check_llama_server.main([str(tmp_path / "old")]) == 1
    assert "llama-server check failed: llama-server is build 10964" in capsys.readouterr().err
    assert check_llama_server.main([str(tmp_path / "missing")]) == 1
    assert "not installed" in capsys.readouterr().err
