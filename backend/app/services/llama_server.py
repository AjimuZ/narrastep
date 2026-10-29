"""Locate the bundled `llama.cpp` CPU server and check that it runs on this system (P06.1)."""

from __future__ import annotations

import math
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# The backend image copies the server and its shared libraries here (see `backend/Dockerfile`).
LLAMA_SERVER_DIR = Path("/opt/llama.cpp")
LLAMA_SERVER_EXECUTABLE = "llama-server"
# Build number and commit of `ghcr.io/ggml-org/llama.cpp:server-v0.5.0`, pinned by digest in the
# Dockerfile. The image build fails when the copied server reports anything else.
PINNED_LLAMA_SERVER_BUILD = 11146
PINNED_LLAMA_SERVER_COMMIT = "7fe450e19"

# `llama-server --version` prints "version: <name> (build <number>, commit <hash>)".
_VERSION_PATTERN = re.compile(
    r"^version: (?P<name>\S+) \(build (?P<build>\d+), commit (?P<commit>[0-9a-f]+)\)$",
    re.MULTILINE,
)


class LlamaServerUnavailableError(RuntimeError):
    """Report that the bundled server is missing, cannot start or is not the pinned build.

    The message names the failure only; it never includes command output, so it is safe to log.
    """


@dataclass(frozen=True, slots=True)
class LlamaServerVersion:
    """Identify the server build as reported by `llama-server --version`."""

    name: str
    build: int
    commit: str


def llama_server_environment(
    directory: Path = LLAMA_SERVER_DIR, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """Return a child-process environment that finds the server's shared libraries.

    The official binaries look for their libraries in `/app/build/bin` and the working directory,
    neither of which holds them in the backend image, so `directory` is put first on
    `LD_LIBRARY_PATH`. The path is set for the server process only and never for the Python
    processes, so the bundled libraries cannot shadow the ones used by the API or the worker.
    `base` defaults to the current environment; an existing `LD_LIBRARY_PATH` is kept after it.
    """

    environment = dict(os.environ if base is None else base)
    existing = environment.get("LD_LIBRARY_PATH", "")
    environment["LD_LIBRARY_PATH"] = f"{directory}:{existing}" if existing else str(directory)
    return environment


def read_llama_server_version(
    directory: Path = LLAMA_SERVER_DIR, *, timeout_seconds: float = 30.0
) -> LlamaServerVersion:
    """Run `llama-server --version` from `directory` and return the reported build.

    Running the command loads every shared library the server needs, so a success also proves
    that the binaries are compatible with this system's C library, C++ runtime, OpenSSL and
    OpenMP. Requires a positive finite `timeout_seconds`. Raises `LlamaServerUnavailableError`
    when the executable is missing, cannot start, times out, exits with an error or prints no
    version line.
    """

    if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        raise ValueError("The llama-server timeout must be greater than zero.")
    executable = directory / LLAMA_SERVER_EXECUTABLE
    if not executable.is_file():
        raise LlamaServerUnavailableError(f"llama-server is not installed in {directory}.")
    try:
        completed = subprocess.run(
            [str(executable), "--version"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            env=llama_server_environment(directory),
            # The binaries also search the working directory; keep it on the bundled files.
            cwd=directory,
        )
    except subprocess.TimeoutExpired as exc:
        raise LlamaServerUnavailableError("llama-server --version timed out.") from exc
    except OSError as exc:
        raise LlamaServerUnavailableError("llama-server could not be started.") from exc
    if completed.returncode != 0:
        raise LlamaServerUnavailableError(
            f"llama-server --version exited with code {completed.returncode}."
        )
    # The version is printed on standard error by current builds; accept either stream.
    match = _VERSION_PATTERN.search(f"{completed.stdout}\n{completed.stderr}")
    if match is None:
        raise LlamaServerUnavailableError("llama-server --version printed no version line.")
    return LlamaServerVersion(name=match["name"], build=int(match["build"]), commit=match["commit"])


def check_pinned_llama_server(
    directory: Path = LLAMA_SERVER_DIR,
    *,
    expected_build: int = PINNED_LLAMA_SERVER_BUILD,
    expected_commit: str = PINNED_LLAMA_SERVER_COMMIT,
    timeout_seconds: float = 30.0,
) -> LlamaServerVersion:
    """Return the server version after checking that it is the pinned build and commit.

    The commit matches when either hash is a prefix of the other, because builds print an
    abbreviated hash. Raises `LlamaServerUnavailableError` for any failure of
    `read_llama_server_version` and when the build or commit differs from the pin.
    """

    version = read_llama_server_version(directory, timeout_seconds=timeout_seconds)
    commit_matches = version.commit.startswith(expected_commit) or expected_commit.startswith(
        version.commit
    )
    if version.build != expected_build or not commit_matches:
        raise LlamaServerUnavailableError(
            f"llama-server is build {version.build} commit {version.commit}; "
            f"expected build {expected_build} commit {expected_commit}."
        )
    return version
