"""Generation server session (P06.3c): memory check, arguments, start, health, request and stop.

A small Python script behind a `llama-server` launcher stands in for the real server, so these
tests need neither the real binary nor a model. The script serves `/health` and
`/v1/chat/completions` on the requested loopback port and behaves as `mode.txt` says. It is written
to pytest's temporary folder, or beside this file when that folder is mounted `noexec`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.services import llama_session
from app.services.generation_input import GenerationInput, GenerationPart
from app.services.llama_server import LlamaServerUnavailableError
from app.services.llama_session import (
    GenerationOutOfMemoryError,
    LlamaServerRequestError,
    LlamaServerSession,
    build_chat_messages,
    build_llama_server_command,
    ensure_enough_memory,
    estimate_required_memory_bytes,
    read_available_memory_bytes,
)
from app.services.model_catalog import get_model_option

SERVER_SCRIPT = """\
import http.server, json, os, pathlib, signal, sys, time

here = pathlib.Path(__file__).parent
mode = (here / "mode.txt").read_text().strip()
args = sys.argv[1:]
(here / "args.json").write_text(json.dumps(args))
(here / "pid.txt").write_text(str(os.getpid()))
if mode == "exit":
    sys.exit(3)
if mode == "ignore_term":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
port = int(args[args.index("--port") + 1])


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(503 if mode == "unhealthy" else 200, {"status": "ok"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        (here / "request.json").write_bytes(body)
        if mode == "slow_chat":
            time.sleep(3)
        if mode == "bad_chat":
            self._send(500, {"error": "secret model failure"})
        elif mode == "empty_chat":
            self._send(200, {"choices": [{"message": {"content": "  "}}]})
        else:
            self._send(200, {"choices": [{"message": {"content": "Generated notes"}}]})

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


@pytest.fixture
def tmp_path(tmp_path: Path) -> Iterator[Path]:
    """Return an empty folder where scripts can run; it is removed after the test."""

    if not os.statvfs(tmp_path).f_flag & os.ST_NOEXEC:
        yield tmp_path
        return
    folder = Path(tempfile.mkdtemp(prefix=".llama-session-test-", dir=Path(__file__).parent))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def stand_in_server(directory: Path, mode: str = "ok") -> Path:
    """Install the stand-in `llama-server` in `directory` and set its behavior to `mode`."""

    directory.mkdir(parents=True, exist_ok=True)
    (directory / "server.py").write_text(SERVER_SCRIPT, encoding="utf-8")
    (directory / "mode.txt").write_text(mode, encoding="utf-8")
    launcher = directory / "llama-server"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{directory}/server.py" "$@"\n', encoding="utf-8"
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    return directory


def make_session(directory: Path, **overrides: Any) -> LlamaServerSession:
    options: dict[str, Any] = {
        "model_path": directory / "model.gguf",
        "projector_path": directory / "mmproj.gguf",
        "directory": directory,
        "startup_timeout_seconds": 20.0,
        "request_timeout_seconds": 20.0,
    }
    options.update(overrides)
    return LlamaServerSession(**options)


def sample_input() -> GenerationInput:
    return GenerationInput(
        system_prompt="System rules",
        parts=(
            GenerationPart(kind="text", text="Spoken sentence"),
            GenerationPart(kind="image", file_name="a.png", image_png=b"\x89PNG"),
        ),
    )


def assert_process_is_gone(directory: Path) -> None:
    pid = int((directory / "pid.txt").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_command_is_an_argument_array_for_loopback_only() -> None:
    command = build_llama_server_command(
        Path("/opt/llama.cpp/llama-server"),
        Path("/data/models/m.gguf"),
        Path("/data/models/p.gguf"),
        8081,
        context_tokens=4096,
        threads=6,
    )

    assert command == [
        "/opt/llama.cpp/llama-server",
        "-m",
        "/data/models/m.gguf",
        "--mmproj",
        "/data/models/p.gguf",
        "--host",
        "127.0.0.1",
        "--port",
        "8081",
        "-c",
        "4096",
        "-np",
        "1",
        "-t",
        "6",
    ]
    assert "0.0.0.0" not in command


def test_command_leaves_the_thread_count_to_the_server_by_default() -> None:
    command = build_llama_server_command(Path("s"), Path("m"), Path("p"), 9000)

    assert "-t" not in command
    assert command[command.index("--host") + 1] == "127.0.0.1"


@pytest.mark.parametrize("port", [0, 65536, -1])
def test_command_refuses_an_invalid_port(port: int) -> None:
    with pytest.raises(ValueError):
        build_llama_server_command(Path("s"), Path("m"), Path("p"), port)


def test_command_refuses_invalid_sizes() -> None:
    with pytest.raises(ValueError):
        build_llama_server_command(Path("s"), Path("m"), Path("p"), 9000, context_tokens=0)
    with pytest.raises(ValueError):
        build_llama_server_command(Path("s"), Path("m"), Path("p"), 9000, threads=-1)


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_session_refuses_invalid_timeouts(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError):
        make_session(tmp_path, startup_timeout_seconds=timeout)
    with pytest.raises(ValueError):
        make_session(tmp_path, request_timeout_seconds=timeout)


def test_chat_messages_keep_the_part_order_and_encode_images() -> None:
    messages = build_chat_messages(sample_input())

    assert messages[0] == {"role": "system", "content": "System rules"}
    assert messages[1]["content"] == [
        {"type": "text", "text": "Spoken sentence"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw=="}},
    ]


def test_session_starts_answers_and_stops_the_server(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path)

    with make_session(directory, threads=2) as session:
        process = session.process
        assert process is not None and process.poll() is None
        assert session.complete(sample_input()) == "Generated notes"
        arguments = json.loads((directory / "args.json").read_text())
        assert arguments[arguments.index("--host") + 1] == "127.0.0.1"
        assert arguments[arguments.index("--port") + 1] == str(session.port)
        assert arguments[arguments.index("-t") + 1] == "2"
        request = json.loads((directory / "request.json").read_text())
        assert request["messages"][0] == {"role": "system", "content": "System rules"}
        assert request["stream"] is False

    assert process.poll() is not None
    assert session.process is None
    assert_process_is_gone(directory)


def test_session_starts_without_a_shell_and_discards_server_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = stand_in_server(tmp_path)
    calls: list[tuple[Any, dict[str, Any]]] = []
    real_popen = subprocess.Popen

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append((args[0], kwargs))
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(llama_session.subprocess, "Popen", spy)

    with make_session(directory):
        pass

    command, kwargs = calls[0]
    assert isinstance(command, list)
    assert kwargs["shell"] is False
    assert kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["env"]["LD_LIBRARY_PATH"].split(":")[0] == str(directory)


def test_failed_health_check_stops_the_process(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path, "unhealthy")
    session = make_session(directory, startup_timeout_seconds=1.0)

    with pytest.raises(LlamaServerUnavailableError, match="did not become healthy"):
        session.start()

    assert session.process is None
    assert_process_is_gone(directory)


def test_server_that_exits_while_starting_is_reported_with_its_code(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path, "exit")

    with pytest.raises(LlamaServerUnavailableError, match="exited with code 3"):
        make_session(directory).start()


def test_missing_server_is_reported(tmp_path: Path) -> None:
    with pytest.raises(LlamaServerUnavailableError, match="not installed"):
        make_session(tmp_path / "missing").start()


def test_server_that_cannot_run_is_reported(tmp_path: Path) -> None:
    executable = tmp_path / "llama-server"
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o644)

    with pytest.raises(LlamaServerUnavailableError, match="could not be started"):
        make_session(tmp_path).start()


def test_request_failure_is_reported_without_the_response_body_and_the_server_stops(
    tmp_path: Path,
) -> None:
    directory = stand_in_server(tmp_path, "bad_chat")

    with pytest.raises(LlamaServerRequestError) as raised:
        with make_session(directory) as session:
            session.complete(sample_input())

    assert str(raised.value) == "llama-server answered the request with HTTP 500."
    assert "secret" not in str(raised.value)
    assert_process_is_gone(directory)


def test_answer_without_text_is_a_request_failure(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path, "empty_chat")

    with make_session(directory) as session:
        with pytest.raises(LlamaServerRequestError, match="no text"):
            session.complete(sample_input())

    assert_process_is_gone(directory)


def test_request_to_a_server_that_died_is_a_request_failure(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path)

    with make_session(directory) as session:
        assert session.process is not None
        session.process.kill()
        session.process.wait()
        with pytest.raises(LlamaServerRequestError, match="not running"):
            session.complete(sample_input())


def test_request_timeout_is_a_request_failure_and_the_server_stops(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path, "slow_chat")
    session = make_session(directory, request_timeout_seconds=0.3)

    with session:
        with pytest.raises(LlamaServerRequestError, match="could not complete"):
            session.complete(sample_input())

    assert_process_is_gone(directory)


def test_server_stops_when_the_job_raises(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path)

    with pytest.raises(KeyError):
        with make_session(directory):
            raise KeyError("job failed")

    assert_process_is_gone(directory)


def test_server_that_ignores_termination_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = stand_in_server(tmp_path, "ignore_term")
    monkeypatch.setattr(llama_session, "_TERMINATE_GRACE_SECONDS", 0.3)

    with make_session(directory) as session:
        process = session.process
        assert process is not None

    assert process.returncode is not None
    assert_process_is_gone(directory)


def test_stop_is_safe_before_a_start_and_twice(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path)
    session = make_session(directory)

    session.stop()
    with session:
        session.stop()
    session.stop()

    assert_process_is_gone(directory)


def test_session_cannot_be_started_twice(tmp_path: Path) -> None:
    directory = stand_in_server(tmp_path)

    with make_session(directory) as session:
        with pytest.raises(RuntimeError, match="already started"):
            session.start()


@pytest.fixture
def module_logger_enabled() -> Iterator[None]:
    """Re-enable the module logger, which an Alembic `fileConfig` in an earlier test disables."""

    logger = logging.getLogger("app.services.llama_session")
    was_disabled = logger.disabled
    logger.disabled = False
    try:
        yield
    finally:
        logger.disabled = was_disabled


@pytest.mark.usefixtures("module_logger_enabled")
def test_session_logs_neither_prompts_nor_answers(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    directory = stand_in_server(tmp_path)

    with caplog.at_level(logging.DEBUG, logger="app.services.llama_session"):
        with make_session(directory) as session:
            session.complete(sample_input())

    assert "Spoken sentence" not in caplog.text
    assert "Generated notes" not in caplog.text
    assert "127.0.0.1" in caplog.text


def test_out_of_memory_error_is_readable_and_carries_the_stable_code() -> None:
    with pytest.raises(GenerationOutOfMemoryError) as raised:
        ensure_enough_memory(6 * 1024**3, 2 * 1024**3)

    assert raised.value.code == "GENERATION_OUT_OF_MEMORY"
    assert raised.value.required_bytes == 6 * 1024**3
    assert raised.value.available_bytes == 2 * 1024**3
    assert str(raised.value) == (
        "Not enough memory to generate the document: about 6.0 GiB are needed and 2.0 GiB are "
        "available. Raise the memory limit of Docker or Podman, or close other programs, then "
        "try again."
    )


@pytest.mark.usefixtures("module_logger_enabled")
def test_enough_or_unknown_memory_passes(caplog: pytest.LogCaptureFixture) -> None:
    ensure_enough_memory(5, 5)
    ensure_enough_memory(5, 6)
    with caplog.at_level(logging.WARNING, logger="app.services.llama_session"):
        ensure_enough_memory(5, None)

    assert "memory check was skipped" in caplog.text


def test_required_memory_is_the_model_files_plus_the_overhead() -> None:
    option = get_model_option("gemma-4-e2b")

    assert estimate_required_memory_bytes(option, 100) == option.total_bytes + 100
    assert estimate_required_memory_bytes(option) > option.total_bytes


def write_cgroup(
    directory: Path, limit: str, current: int, inactive_file: int | None = None
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "memory.max").write_text(f"{limit}\n", encoding="utf-8")
    (directory / "memory.current").write_text(f"{current}\n", encoding="utf-8")
    if inactive_file is not None:
        (directory / "memory.stat").write_text(
            f"anon 1\ninactive_file {inactive_file}\nactive_file 7\n", encoding="utf-8"
        )


def write_meminfo(path: Path, available_kib: int) -> None:
    path.write_text(
        f"MemTotal: 99999999 kB\nMemAvailable: {available_kib} kB\nBuffers: 1 kB\n",
        encoding="utf-8",
    )


def test_available_memory_is_the_cgroup_headroom_when_it_is_the_smaller_limit(
    tmp_path: Path,
) -> None:
    write_cgroup(tmp_path / "cg", str(8 * 1024**3), 3 * 1024**3, inactive_file=1024**3)
    write_meminfo(tmp_path / "meminfo", 64 * 1024 * 1024)

    available = read_available_memory_bytes(tmp_path / "cg", tmp_path / "meminfo")

    assert available == 6 * 1024**3


def test_available_memory_is_the_host_figure_when_it_is_smaller(tmp_path: Path) -> None:
    write_cgroup(tmp_path / "cg", str(8 * 1024**3), 0)
    write_meminfo(tmp_path / "meminfo", 2 * 1024 * 1024)

    assert read_available_memory_bytes(tmp_path / "cg", tmp_path / "meminfo") == 2 * 1024**3


def test_unlimited_cgroup_falls_back_to_the_host_figure(tmp_path: Path) -> None:
    write_cgroup(tmp_path / "cg", "max", 123)
    write_meminfo(tmp_path / "meminfo", 1024 * 1024)

    assert read_available_memory_bytes(tmp_path / "cg", tmp_path / "meminfo") == 1024**3


def test_unknown_memory_is_none(tmp_path: Path) -> None:
    assert read_available_memory_bytes(tmp_path / "no-cg", tmp_path / "no-meminfo") is None
    (tmp_path / "meminfo").write_text("MemTotal: 1 kB\n", encoding="utf-8")
    assert read_available_memory_bytes(tmp_path / "no-cg", tmp_path / "meminfo") is None
