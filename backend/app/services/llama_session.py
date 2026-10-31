"""Run the bundled `llama-server` on loopback for one generation: start, ask, stop (P06.3c)."""

from __future__ import annotations

import base64
import json
import logging
import math
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from app.services.generation_input import GenerationInput
from app.services.llama_server import (
    LLAMA_SERVER_DIR,
    LLAMA_SERVER_EXECUTABLE,
    LlamaServerUnavailableError,
    llama_server_environment,
)
from app.services.model_catalog import ModelOption

logger = logging.getLogger(__name__)

# The server is never reachable from outside the worker container: the address is fixed.
LOOPBACK_HOST = "127.0.0.1"
GENERATION_OUT_OF_MEMORY = "GENERATION_OUT_OF_MEMORY"

_GIB = 1024**3
# Memory the server needs besides the weights: the key-value cache of the context, the image
# encoder buffers and the process itself. An estimate, kept above the measured need on purpose.
DEFAULT_MEMORY_OVERHEAD_BYTES = 1 * _GIB
DEFAULT_CONTEXT_TOKENS = 8192

_CGROUP_DIR = Path("/sys/fs/cgroup")
_MEMINFO_PATH = Path("/proc/meminfo")
# How long a stopped server may take to exit after SIGTERM before it is killed.
_TERMINATE_GRACE_SECONDS = 5.0
_KILL_WAIT_SECONDS = 5.0
_HEALTH_POLL_SECONDS = 0.25


class GenerationOutOfMemoryError(RuntimeError):
    """Report that the container has less free memory than the model needs (risk K5).

    `code` is the stable public code the interface translates. The message states the needed and
    available amounts so the user can raise Docker's memory limit; it holds no transcript text.
    """

    code = GENERATION_OUT_OF_MEMORY

    def __init__(self, required_bytes: int, available_bytes: int) -> None:
        self.required_bytes = required_bytes
        self.available_bytes = available_bytes
        super().__init__(
            f"Not enough memory to generate the document: about {required_bytes / _GIB:.1f} GiB "
            f"are needed and {available_bytes / _GIB:.1f} GiB are available. "
            "Raise the memory limit of Docker or Podman, or close other programs, then try again."
        )


class LlamaServerRequestError(RuntimeError):
    """Report a failed request to the running server, without the response body in the message."""


def _read_int(path: Path) -> int | None:
    """Return the integer in `path`, or None when it is missing, `max` or not a number."""
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except OSError, ValueError:
        return None


def _cgroup_available_bytes(cgroup_dir: Path) -> int | None:
    """Return the room left below the cgroup v2 limit, or None when there is no limit."""
    limit = _read_int(cgroup_dir / "memory.max")
    used = _read_int(cgroup_dir / "memory.current")
    if limit is None or used is None:
        return None
    # Cached file pages (such as the mapped model) are reclaimable, so they do not count as used.
    try:
        stat_text = (cgroup_dir / "memory.stat").read_text(encoding="utf-8")
    except OSError:
        stat_text = ""
    for line in stat_text.splitlines():
        name, _, value = line.partition(" ")
        if name == "inactive_file" and value.isdigit():
            used = max(0, used - int(value))
            break
    return max(0, limit - used)


def _meminfo_available_bytes(meminfo: Path) -> int | None:
    """Return `MemAvailable` of a `/proc/meminfo` file in bytes, or None when absent."""
    try:
        text = meminfo.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("MemAvailable:"):
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                return int(fields[1]) * 1024
    return None


def read_available_memory_bytes(
    cgroup_dir: Path = _CGROUP_DIR, meminfo: Path = _MEMINFO_PATH
) -> int | None:
    """Return the memory this container can still use, or None when it cannot be determined.

    The smaller of the cgroup v2 headroom (limit minus usage, which is what Docker Desktop's memory
    setting changes) and the host's `MemAvailable` is used, because either one can be the binding
    limit. The paths are parameters so tests can supply their own files.
    """

    values = [
        value
        for value in (_cgroup_available_bytes(cgroup_dir), _meminfo_available_bytes(meminfo))
        if value is not None
    ]
    return min(values) if values else None


def estimate_required_memory_bytes(
    option: ModelOption, overhead_bytes: int = DEFAULT_MEMORY_OVERHEAD_BYTES
) -> int:
    """Return the memory estimated for running `option`: all its files plus `overhead_bytes`."""
    return option.total_bytes + overhead_bytes


def ensure_enough_memory(required_bytes: int, available_bytes: int | None) -> None:
    """Raise `GenerationOutOfMemoryError` when `available_bytes` is below `required_bytes`.

    An unknown amount (None) passes: refusing to run because the system cannot be inspected would
    block machines that work, and the server's own failure is reported by `LlamaServerSession`.
    """

    if available_bytes is None:
        logger.warning("The available memory is unknown, so the memory check was skipped.")
        return
    if available_bytes < required_bytes:
        raise GenerationOutOfMemoryError(required_bytes, available_bytes)


def free_loopback_port() -> int:
    """Return a TCP port on 127.0.0.1 that was free a moment ago.

    Another process could take it before the server binds; the server then fails to start and the
    session reports it.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((LOOPBACK_HOST, 0))
        return int(probe.getsockname()[1])


def build_llama_server_command(
    executable: Path,
    model_path: Path,
    projector_path: Path,
    port: int,
    *,
    context_tokens: int = DEFAULT_CONTEXT_TOKENS,
    threads: int = 0,
) -> list[str]:
    """Return the argument array that starts the server on loopback for one model.

    The address is always `127.0.0.1`; there is no parameter for another host. One slot (`-np 1`)
    serves the single sequential job. `threads` of 0 leaves the thread count to `llama.cpp`. The
    array is passed to the process directly, never through a shell. Requires a valid port and
    positive sizes.
    """

    if not 1 <= port <= 65535:
        raise ValueError("The llama-server port is out of range.")
    if context_tokens < 1 or threads < 0:
        raise ValueError("The llama-server context and threads must be positive.")
    command = [
        str(executable),
        "-m",
        str(model_path),
        "--mmproj",
        str(projector_path),
        "--host",
        LOOPBACK_HOST,
        "--port",
        str(port),
        "-c",
        str(context_tokens),
        "-np",
        "1",
    ]
    if threads > 0:
        command += ["-t", str(threads)]
    return command


def build_chat_messages(generation_input: GenerationInput) -> list[dict[str, Any]]:
    """Return the OpenAI-style `messages` for one request: the system prompt, then the user parts.

    Text parts stay text; image parts become base64 PNG data URLs, in the same order.
    """

    content: list[dict[str, Any]] = []
    for part in generation_input.parts:
        if part.kind == "image" and part.image_png is not None:
            encoded = base64.b64encode(part.image_png).decode("ascii")
            content.append(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}
            )
        else:
            content.append({"type": "text", "text": part.text})
    return [
        {"role": "system", "content": generation_input.system_prompt},
        {"role": "user", "content": content},
    ]


# Requests go to the local server only; proxy settings from the environment must not capture them.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


@dataclass
class LlamaServerSession:
    """Own one `llama-server` process for the duration of a generation.

    Use it as a context manager: `__enter__` starts the server and waits until it reports healthy,
    and `__exit__` always stops it, also when a request failed or the job raised. A failed start
    stops the process before the error leaves `start`. The session logs ports and counts, never
    prompts, images or model output.
    """

    model_path: Path
    projector_path: Path
    directory: Path = LLAMA_SERVER_DIR
    context_tokens: int = DEFAULT_CONTEXT_TOKENS
    threads: int = 0
    startup_timeout_seconds: float = 300.0
    request_timeout_seconds: float = 3600.0
    port: int = 0
    process: subprocess.Popen[bytes] | None = None

    def __post_init__(self) -> None:
        for value in (self.startup_timeout_seconds, self.request_timeout_seconds):
            if value <= 0 or not math.isfinite(value):
                raise ValueError("The llama-server timeouts must be greater than zero.")

    @property
    def base_url(self) -> str:
        """Return the loopback address of the running server."""
        return f"http://{LOOPBACK_HOST}:{self.port}"

    def start(self) -> None:
        """Start the server and wait for a healthy answer.

        Raises `LlamaServerUnavailableError` when the executable is missing, the process cannot
        start, exits early or does not become healthy within `startup_timeout_seconds`. The
        process is stopped before any such error is raised.
        """

        if self.process is not None:
            raise RuntimeError("The llama-server session is already started.")
        executable = self.directory / LLAMA_SERVER_EXECUTABLE
        if not executable.is_file():
            raise LlamaServerUnavailableError(f"llama-server is not installed in {self.directory}.")
        self.port = free_loopback_port()
        command = build_llama_server_command(
            executable,
            self.model_path,
            self.projector_path,
            self.port,
            context_tokens=self.context_tokens,
            threads=self.threads,
        )
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                # Server output can echo prompts, so it is discarded rather than logged.
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                env=llama_server_environment(self.directory),
                cwd=self.directory,
            )
        except OSError as exc:
            raise LlamaServerUnavailableError("llama-server could not be started.") from exc
        try:
            self._wait_until_healthy()
        except BaseException:
            self.stop()
            raise
        logger.info("llama-server is healthy on %s:%d.", LOOPBACK_HOST, self.port)

    def _wait_until_healthy(self) -> None:
        """Poll `/health` until it answers 200; raise when the process exits or time runs out."""
        deadline = time.monotonic() + self.startup_timeout_seconds
        while True:
            assert self.process is not None
            exit_code = self.process.poll()
            if exit_code is not None:
                raise LlamaServerUnavailableError(
                    f"llama-server exited with code {exit_code} before it was ready."
                )
            if self._health_is_ok():
                return
            if time.monotonic() >= deadline:
                raise LlamaServerUnavailableError(
                    "llama-server did not become healthy before the timeout."
                )
            time.sleep(_HEALTH_POLL_SECONDS)

    def _health_is_ok(self) -> bool:
        """Return True when `/health` answers 200; a loading server answers 503."""
        try:
            with _LOCAL_OPENER.open(f"{self.base_url}/health", timeout=2.0) as response:
                return bool(response.status == 200)
        except urllib.error.URLError, OSError, ValueError:
            return False

    def complete(self, generation_input: GenerationInput, *, max_tokens: int = 2048) -> str:
        """Send one chat request and return the model's text.

        Raises `LlamaServerRequestError` when the server is not running, answers with an error,
        times out, exits during the request or returns no text. The message never includes the
        response body.
        """

        if self.process is None or self.process.poll() is not None:
            raise LlamaServerRequestError("llama-server is not running.")
        payload = json.dumps(
            {
                "messages": build_chat_messages(generation_input),
                "max_tokens": max_tokens,
                "temperature": 0.2,
                "stream": False,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _LOCAL_OPENER.open(request, timeout=self.request_timeout_seconds) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise LlamaServerRequestError(
                f"llama-server answered the request with HTTP {exc.code}."
            ) from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise LlamaServerRequestError("llama-server could not complete the request.") from exc
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LlamaServerRequestError("llama-server returned an unexpected answer.") from exc
        if not isinstance(text, str) or not text.strip():
            raise LlamaServerRequestError("llama-server returned no text.")
        return text

    def stop(self) -> None:
        """Stop the process: terminate, wait, then kill. Safe to call again or before a start."""
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_TERMINATE_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=_KILL_WAIT_SECONDS)
        logger.info("llama-server on port %d was stopped.", self.port)

    def __enter__(self) -> LlamaServerSession:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop()
