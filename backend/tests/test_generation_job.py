"""Generation job and endpoints (P06.3d): document, progress, cancellation and failures.

A small Python script behind a `llama-server` launcher stands in for the real server, as in
`test_llama_session.py`, so no real binary or model is needed. The model files are tiny generated
files whose names, sizes and hashes replace the pinned catalog entry for the duration of a test.
The script is written to pytest's temporary folder, or beside this file when that is `noexec`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.main import app
from app.models import (
    ExportStatus,
    Generation,
    JobStatus,
    MediaAsset,
    ModelInstallState,
    OutputMode,
    ProcessingJob,
    Project,
    ProjectExport,
    ProjectState,
    Screenshot,
    SegmentScreenshotAssociation,
    SpeakerLabel,
    SpeakerSource,
    TranscriptSegment,
    utc_now,
)
from app.services import generation_job
from app.services.generation_job import (
    GENERATION_PROMPT_VERSION,
    perform_generation,
    pinned_model_files,
)
from app.services.model_catalog import MODEL_CATALOG, ModelFile, ModelOption
from app.services.model_download import model_directory
from app.services.model_manager import transition_model
from app.services.reconciliation import reconcile_interrupted_work
from app.services.workspaces import create_project_workspace
from app.tasks.celery_app import celery_app

State = ModelInstallState
MODEL_ID = "gemma-4-e2b"
WEIGHTS_BYTES = b"tiny-weights"
PROJECTOR_BYTES = b"tiny-projector"
# Distinct wording that must never reach a log line or an error message.
SPOKEN = "The quarterly budget needs a second review"

SMALL_OPTION = ModelOption(
    model_id=MODEL_ID,
    label="Light",
    repository="test/tiny-GGUF",
    revision="a" * 40,
    files=(
        ModelFile("tiny-Q4_0.gguf", len(WEIGHTS_BYTES), hashlib.sha256(WEIGHTS_BYTES).hexdigest()),
        ModelFile(
            "mmproj-tiny-Q8_0.gguf",
            len(PROJECTOR_BYTES),
            hashlib.sha256(PROJECTOR_BYTES).hexdigest(),
        ),
    ),
)

SERVER_SCRIPT = """\
import http.server, json, os, pathlib, sys, time

here = pathlib.Path(__file__).parent
mode = (here / "mode.txt").read_text().strip()
args = sys.argv[1:]
(here / "args.json").write_text(json.dumps(args))
(here / "pid.txt").write_text(str(os.getpid()))
if mode == "exit":
    sys.exit(3)
port = int(args[args.index("--port") + 1])
count = 0


class Handler(http.server.BaseHTTPRequestHandler):
    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"status": "ok"})

    def do_POST(self):
        global count
        body = self.rfile.read(int(self.headers["Content-Length"]))
        count += 1
        (here / f"request-{count}.json").write_bytes(body)
        if mode == "die":
            os._exit(1)
        if mode == "slow":
            time.sleep(30)
        self._send(200, {"choices": [{"message": {"content": f"Answer {count}"}}]})

    def log_message(self, *args):
        pass


http.server.HTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


@dataclass
class Environment:
    """The settings and the stand-in server folder a test works in."""

    settings: Settings
    server_dir: Path

    def set_mode(self, mode: str) -> None:
        """Change how the stand-in server behaves for the next start."""
        (self.server_dir / "mode.txt").write_text(mode, encoding="utf-8")

    def requests(self) -> list[dict[str, Any]]:
        """Return the decoded request bodies the stand-in server received, in order."""
        files = sorted(
            self.server_dir.glob("request-*.json"), key=lambda path: int(path.stem.split("-")[1])
        )
        return [json.loads(path.read_text(encoding="utf-8")) for path in files]

    def server_was_started(self) -> bool:
        """Tell whether the stand-in server process ever ran."""
        return (self.server_dir / "pid.txt").exists()

    def assert_server_is_gone(self) -> None:
        """Fail unless the stand-in server process that ran has ended."""
        pid = int((self.server_dir / "pid.txt").read_text())
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)


@pytest.fixture
def tmp_path(tmp_path: Path) -> Iterator[Path]:
    """Return an empty folder where scripts can run; it is removed after the test."""
    if not os.statvfs(tmp_path).f_flag & os.ST_NOEXEC:
        yield tmp_path
        return
    folder = Path(tempfile.mkdtemp(prefix=".generation-job-test-", dir=Path(__file__).parent))
    try:
        yield folder
    finally:
        shutil.rmtree(folder, ignore_errors=True)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Environment]:
    """Install the stand-in server and the tiny catalog entry, and use temporary settings."""
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    (server_dir / "server.py").write_text(SERVER_SCRIPT, encoding="utf-8")
    (server_dir / "mode.txt").write_text("ok", encoding="utf-8")
    launcher = server_dir / "llama-server"
    launcher.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{server_dir}/server.py" "$@"\n', encoding="utf-8"
    )
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    settings = Settings(data_root=tmp_path / "data")
    monkeypatch.setitem(MODEL_CATALOG, MODEL_ID, SMALL_OPTION)
    monkeypatch.setattr(generation_job, "LLAMA_SERVER_DIR", server_dir)
    monkeypatch.setattr(generation_job, "read_available_memory_bytes", lambda: 64 * 1024**3)
    monkeypatch.setattr(generation_job, "_CANCEL_POLL_SECONDS", 0.1)
    # Cancelling a queued generation revokes its task; no broker runs in these tests.
    monkeypatch.setattr(celery_app.control, "revoke", lambda *_, **__: None)
    app.dependency_overrides[get_settings] = lambda: settings
    yield Environment(settings, server_dir)
    app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def module_logger_enabled() -> Iterator[None]:
    """Re-enable the module logger, which an Alembic `fileConfig` in an earlier test disables."""
    logger = logging.getLogger("app.services.generation_job")
    was_disabled = logger.disabled
    logger.disabled = False
    try:
        yield
    finally:
        logger.disabled = was_disabled


def install_model(session: Session, settings: Settings, *, enabled: bool = True) -> None:
    """Walk the tiny model to `installed` or `enabled` and write its generated files."""
    steps = [State.DOWNLOADING, State.VERIFYING, State.INSTALLED]
    for step in steps + ([State.ENABLED] if enabled else []):
        transition_model(session, settings, MODEL_ID, step)
    directory = model_directory(settings, MODEL_ID)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SMALL_OPTION.files[0].name).write_bytes(WEIGHTS_BYTES)
    (directory / SMALL_OPTION.files[1].name).write_bytes(PROJECTOR_BYTES)


def review_project(
    session: Session,
    settings: Settings,
    texts: list[str],
    *,
    with_screenshot: bool = True,
    state: ProjectState = ProjectState.REVIEW,
) -> Project:
    """Create a project in review with one reviewed segment per text and one real screenshot."""
    project = Project(name="Weekly sync", state=state, output_mode=OutputMode.MEETING)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(settings.data_root, project.id)
    segments = [
        TranscriptSegment(
            project_id=project.id,
            sequence=index,
            start_seconds=index * 10.0,
            end_seconds=index * 10.0 + 9.0,
            raw_text=text,
            reviewed_text=text,
            raw_words=[],
            speaker=SpeakerLabel.YOU if index == 0 else None,
            speaker_source=SpeakerSource.AUTOMATIC if index == 0 else None,
        )
        for index, text in enumerate(texts)
    ]
    session.add_all(segments)
    session.flush()
    if with_screenshot:
        image_path = workspace / "screenshots" / "frame-0001.png"
        Image.new("RGB", (32, 18), (90, 120, 150)).save(image_path)
        screenshot = Screenshot(
            project_id=project.id,
            relative_path=image_path.relative_to(settings.data_root).as_posix(),
            timestamp_seconds=1.0,
            perceptual_hash="0123456789abcdef",
            quality_score=0.9,
            width=32,
            height=18,
        )
        session.add(screenshot)
        session.flush()
        session.add(
            SegmentScreenshotAssociation(
                segment_id=segments[0].id,
                screenshot_id=screenshot.id,
                is_primary=True,
                rank=0,
                score=0.9,
            )
        )
    session.commit()
    return project


def queue_generation(
    session: Session, project: Project, *, language: str | None = None, task_id: str = "task-1"
) -> Generation:
    """Insert a queued generation the way the endpoint does, without dispatching it."""
    generation = Generation(
        project_id=project.id,
        model_id=MODEL_ID,
        celery_task_id=task_id,
        language=language,
        prompt_version=GENERATION_PROMPT_VERSION,
        model_files=pinned_model_files(SMALL_OPTION),
    )
    session.add(generation)
    session.commit()
    return generation


def reload(session: Session, generation: Generation) -> Generation:
    """Return the generation as stored right now."""
    session.expire_all()
    stored = session.get(Generation, generation.id)
    assert stored is not None
    return stored


def generation_folder(env: Environment, generation: Generation) -> Path:
    """Return where the generation's own files live."""
    return (
        env.settings.data_root / "projects" / generation.project_id / "generations" / generation.id
    )


def spoken_texts(count: int) -> list[str]:
    """Return `count` segment texts of about 120 characters that contain `SPOKEN`."""
    return [f"{SPOKEN}, item {index}. " + "x" * 80 for index in range(count)]


# --- success -----------------------------------------------------------------------------------


def test_job_writes_the_document_with_prompt_version_and_model_file_hashes(
    session: Session, env: Environment
) -> None:
    """One part needs no merge: the answer is the document, headed by its provenance."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(2))
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings, celery_task_id="task-1")

    stored = reload(session, generation)
    assert stored.status == JobStatus.COMPLETED
    assert stored.progress_percent == 100
    assert stored.parts_total == 1 and stored.parts_completed == 1
    assert stored.relative_path == (
        f"projects/{project.id}/generations/{generation.id}/document.md"
    )
    document_path = env.settings.data_root / stored.relative_path
    text = document_path.read_text(encoding="utf-8")
    assert stored.size_bytes == document_path.stat().st_size
    assert f"promptVersion: {GENERATION_PROMPT_VERSION}" in text
    for file in SMALL_OPTION.files:
        assert f"{file.name} sha256:{file.sha256}" in text
    assert text.rstrip().endswith("Answer 1")
    assert stored.model_files == pinned_model_files(SMALL_OPTION)
    assert not (generation_folder(env, generation) / ".work").exists()
    assert not list(generation_folder(env, generation).glob("*.partial"))
    assert len(env.requests()) == 1
    env.assert_server_is_gone()


def test_request_carries_the_type_prompt_the_transcript_and_the_screenshot(
    session: Session, env: Environment
) -> None:
    """The model receives the meeting prompt, the speaker line, the language and one image."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(2))
    generation = queue_generation(session, project, language="it")

    perform_generation(generation.id, env.settings)

    request = env.requests()[0]
    system, user = request["messages"]
    assert (
        "Summary" in system["content"]
        and "Write the document in this language: Italian" in (system["content"])
    )
    kinds = [part["type"] for part in user["content"]]
    assert kinds.count("image_url") == 1
    joined = "\n".join(part.get("text", "") for part in user["content"])
    assert SPOKEN in joined and "Speaker: You" in joined
    assert "step-001_00m00s.png" in joined


def test_many_parts_report_real_progress_and_are_merged(
    session: Session, env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each part is a stored progress step; the merge request holds the notes in order."""
    monkeypatch.setattr(generation_job, "PART_BUDGET_TOKENS", 100)
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(5))
    generation = queue_generation(session, project)
    original = generation_job._record_progress
    observed: list[tuple[str, int, int]] = []

    def spy(*args: Any, **kwargs: Any) -> None:
        original(*args, **kwargs)
        with SessionLocal() as other:
            row = other.get(Generation, generation.id)
            assert row is not None
            observed.append((row.stage, row.progress_percent, row.parts_completed))

    monkeypatch.setattr(generation_job, "_record_progress", spy)

    perform_generation(generation.id, env.settings)

    stored = reload(session, generation)
    assert stored.status == JobStatus.COMPLETED
    assert stored.parts_total >= 2 and stored.parts_completed == stored.parts_total
    generating = [item for item in observed if item[0] == "generating"]
    assert [item[2] for item in generating] == list(range(1, stored.parts_total + 1))
    percents = [item[1] for item in generating]
    assert percents == sorted(set(percents)) and 10 < percents[0] and percents[-1] <= 90
    stages = [item[0] for item in observed]
    assert stages.index("merging") > stages.index("generating")
    assert stages[-1] == "saving"
    requests = env.requests()
    assert len(requests) == stored.parts_total + 1
    merge_text = "\n".join(part["text"] for part in requests[-1]["messages"][1]["content"])
    for number in range(1, stored.parts_total + 1):
        assert f"Answer {number}" in merge_text
    assert merge_text.index("Answer 1") < merge_text.index(f"Answer {stored.parts_total}")
    text = (env.settings.data_root / stored.relative_path).read_text(encoding="utf-8")
    assert text.rstrip().endswith(f"Answer {stored.parts_total + 1}")


def test_notes_that_do_not_fit_one_merge_request_are_merged_in_rounds() -> None:
    """Grouping keeps small notes together and always shrinks a list of large ones."""
    small = ["a" * 40] * 10
    assert generation_job._group_notes(small) == [small]
    # Three notes of about 3000 tokens: two never fit one request of 5000, so they are paired
    # anyway, which guarantees the rounds end.
    big = "n" * 12_000
    assert generation_job._group_notes([big, big, big]) == [[big, big], [big]]
    # Four notes of about 2000 tokens: two per request fit, so two requests are needed.
    medium = "m" * 8_000
    assert generation_job._group_notes([medium] * 4) == [[medium, medium], [medium, medium]]


@pytest.mark.usefixtures("module_logger_enabled")
def test_logs_and_errors_never_contain_transcript_text(
    session: Session, env: Environment, caplog: pytest.LogCaptureFixture
) -> None:
    """Neither a successful nor a failed run leaves transcript text in logs or error fields."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(2))
    first = queue_generation(session, project)
    with caplog.at_level(logging.DEBUG):
        perform_generation(first.id, env.settings)
    env.set_mode("die")
    second = queue_generation(session, project, task_id="task-2")
    with caplog.at_level(logging.DEBUG):
        perform_generation(second.id, env.settings)

    assert SPOKEN not in caplog.text and "Answer 1" not in caplog.text
    failed = reload(session, second)
    assert failed.status == JobStatus.FAILED
    assert SPOKEN not in (failed.error_message or "") + failed.message


# --- cancellation ------------------------------------------------------------------------------


def test_cancelling_a_running_request_stops_the_server_and_removes_the_partial_work(
    session: Session, env: Environment
) -> None:
    """A cancellation does not wait for the model to answer: the server stops within seconds."""
    env.set_mode("slow")
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(2))
    generation = queue_generation(session, project)

    def cancel_when_the_request_arrives() -> None:
        deadline = time.monotonic() + 20
        while not (env.server_dir / "request-1.json").exists():
            if time.monotonic() > deadline:
                return
            time.sleep(0.05)
        with SessionLocal() as other:
            row = other.get(Generation, generation.id)
            assert row is not None
            row.cancel_requested = True
            row.status = JobStatus.CANCELLING
            other.commit()

    thread = threading.Thread(target=cancel_when_the_request_arrives)
    started = time.monotonic()
    thread.start()
    perform_generation(generation.id, env.settings)
    thread.join()

    assert time.monotonic() - started < 20
    stored = reload(session, generation)
    assert stored.status == JobStatus.CANCELLED and stored.stage == "cancelled"
    assert stored.error_code is None and stored.relative_path is None
    assert not generation_folder(env, generation).exists()
    env.assert_server_is_gone()


def test_a_generation_cancelled_before_it_starts_is_skipped_and_never_starts_the_server(
    client: TestClient, session: Session, env: Environment
) -> None:
    """The endpoint cancels a queued generation at once and the late task does nothing."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    cancelled = client.post(f"/api/v1/generations/{generation.id}/cancel")
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"
    perform_generation(generation.id, env.settings)

    assert reload(session, generation).status == JobStatus.CANCELLED
    assert not env.server_was_started()
    assert not generation_folder(env, generation).exists()


def test_cancelling_a_running_generation_asks_the_worker_to_stop(
    client: TestClient, session: Session, env: Environment
) -> None:
    """A running generation becomes `cancelling` with the durable flag the worker polls."""
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)
    generation.status = JobStatus.RUNNING
    session.commit()

    response = client.post(f"/api/v1/generations/{generation.id}/cancel")

    assert response.status_code == 202
    assert response.json()["status"] == "cancelling"
    assert reload(session, generation).cancel_requested is True
    finished = client.post(f"/api/v1/generations/{generation.id}/cancel")
    assert finished.json()["status"] == "cancelling"


# --- missing or disabled model -----------------------------------------------------------------


def test_missing_model_files_fail_the_job_without_starting_the_server(
    session: Session, env: Environment
) -> None:
    """The model is enabled but a file is gone: a stable code and no server."""
    install_model(session, env.settings)
    (model_directory(env.settings, MODEL_ID) / SMALL_OPTION.files[0].name).unlink()
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings)

    stored = reload(session, generation)
    assert stored.status == JobStatus.FAILED
    assert stored.error_code == "GENERATION_MODEL_UNAVAILABLE"
    assert "missing or incomplete" in (stored.error_message or "")
    assert not env.server_was_started()
    assert not generation_folder(env, generation).exists()


def test_a_file_of_the_wrong_size_counts_as_missing(session: Session, env: Environment) -> None:
    """A truncated file fails the cheap size check instead of crashing the server later."""
    install_model(session, env.settings)
    (model_directory(env.settings, MODEL_ID) / SMALL_OPTION.files[1].name).write_bytes(b"short")
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings)

    assert reload(session, generation).error_code == "GENERATION_MODEL_UNAVAILABLE"
    assert not env.server_was_started()


def test_a_model_that_is_installed_but_not_enabled_fails_the_job(
    session: Session, env: Environment
) -> None:
    """Disabling the model after queueing stops the job before any server starts."""
    install_model(session, env.settings, enabled=False)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings)

    stored = reload(session, generation)
    assert stored.status == JobStatus.FAILED
    assert stored.error_code == "GENERATION_MODEL_UNAVAILABLE"
    assert "not enabled" in (stored.error_message or "")
    assert not env.server_was_started()


def test_the_kill_switch_fails_a_queued_job(session: Session, env: Environment) -> None:
    """With local generation disabled the worker refuses to run what was queued earlier."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)
    disabled = Settings(data_root=env.settings.data_root, local_generation="disabled")

    perform_generation(generation.id, disabled)

    assert reload(session, generation).error_code == "LOCAL_GENERATION_DISABLED"
    assert not env.server_was_started()


def test_not_enough_memory_fails_the_job_with_its_stable_code(
    session: Session, env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The memory check runs before the server is started (risk K5)."""
    monkeypatch.setattr(generation_job, "read_available_memory_bytes", lambda: 1024)
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings)

    stored = reload(session, generation)
    assert stored.error_code == "GENERATION_OUT_OF_MEMORY"
    assert "Raise the memory limit" in (stored.error_message or "")
    assert not env.server_was_started()


# --- the server stops --------------------------------------------------------------------------


def test_a_server_that_cannot_start_fails_the_job_and_leaves_nothing_behind(
    session: Session, env: Environment
) -> None:
    """The stand-in exits while starting: a stable code, no document, no folder."""
    env.set_mode("exit")
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings)

    stored = reload(session, generation)
    assert stored.status == JobStatus.FAILED
    assert stored.error_code == "GENERATION_SERVER_FAILED"
    assert stored.relative_path is None and stored.progress_percent < 100
    assert not generation_folder(env, generation).exists()
    env.assert_server_is_gone()


def test_a_server_that_stops_during_a_request_fails_the_job(
    session: Session, env: Environment
) -> None:
    """The server dies while answering: the failure is reported and nothing is left running."""
    env.set_mode("die")
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings)

    stored = reload(session, generation)
    assert stored.status == JobStatus.FAILED
    assert stored.error_code == "GENERATION_SERVER_FAILED"
    assert "stopped or did not answer" in (stored.error_message or "")
    assert stored.completed_at is not None
    assert not generation_folder(env, generation).exists()
    env.assert_server_is_gone()


def test_a_project_without_evidence_fails_instead_of_asking_the_model(
    session: Session, env: Environment
) -> None:
    """Segments with blank text leave nothing to plan, so no server is started."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, ["  "], with_screenshot=False)
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings)

    assert reload(session, generation).error_code == "GENERATION_FAILED"
    assert not env.server_was_started()


def test_an_unexpected_error_becomes_a_safe_failure(
    session: Session, env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any other exception ends the job as `GENERATION_FAILED` with a fixed message."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    def broken(*_: object, **__: object) -> None:
        raise RuntimeError(SPOKEN)

    monkeypatch.setattr(generation_job, "plan_generation_parts", broken)

    perform_generation(generation.id, env.settings)

    stored = reload(session, generation)
    assert stored.error_code == "GENERATION_FAILED"
    assert SPOKEN not in (stored.error_message or "")
    assert not env.server_was_started()


# --- the single worker -------------------------------------------------------------------------


def test_the_worker_runs_one_task_at_a_time_and_generation_shares_its_queue() -> None:
    """Compose starts one process with concurrency 1; the generation task uses the same queue."""
    from app.tasks.celery_app import (
        export_project_task,
        generate_document_task,
        process_project_task,
    )

    compose = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    )
    workers = [
        service
        for service in compose["services"].values()
        if "celery" in " ".join(map(str, service.get("command", [])))
    ]
    assert len(workers) == 1
    assert "--concurrency=1" in workers[0]["command"]
    assert generate_document_task.name in celery_app.tasks
    assert not celery_app.conf.task_routes
    router = celery_app.amqp.router
    queues = {
        router.route({}, task.name)["queue"].name
        for task in (generate_document_task, process_project_task, export_project_task)
    }
    assert len(queues) == 1


@pytest.mark.parametrize("busy", ["processing", "export", "generation"])
def test_generation_does_not_start_while_other_work_is_running(
    session: Session, env: Environment, busy: str
) -> None:
    """Running work found at the start makes the job fail with `WORKER_BUSY`, not overlap it."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    other = Project(name="Other", state=ProjectState.PROCESSING)
    session.add(other)
    session.flush()
    if busy == "processing":
        session.add(ProcessingJob(project_id=other.id, status=JobStatus.RUNNING))
    elif busy == "export":
        session.add(ProjectExport(project_id=other.id, status=ExportStatus.RUNNING))
    else:
        session.add(
            Generation(
                project_id=other.id,
                model_id=MODEL_ID,
                status=JobStatus.RUNNING,
                prompt_version=GENERATION_PROMPT_VERSION,
            )
        )
    session.commit()
    generation = queue_generation(session, project)

    perform_generation(generation.id, env.settings)

    stored = reload(session, generation)
    assert stored.status == JobStatus.FAILED and stored.error_code == "WORKER_BUSY"
    assert not env.server_was_started()


def test_processing_and_deleting_are_refused_while_a_generation_is_active(
    client: TestClient, session: Session, env: Environment
) -> None:
    """The same project cannot be processed or deleted under a queued or running generation."""
    project = Project(name="Busy", state=ProjectState.UPLOADED, output_mode=OutputMode.MEETING)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(env.settings.data_root, project.id)
    (workspace / "source" / "source.mp4").write_bytes(b"video")
    session.add(
        MediaAsset(
            project_id=project.id,
            display_filename="a.mp4",
            stored_filename="source.mp4",
            relative_path=f"projects/{project.id}/source/source.mp4",
            content_type="video/mp4",
            size_bytes=5,
            duration_seconds=4,
            width=640,
            height=360,
            container_format="mp4",
            metadata_json={},
        )
    )
    session.commit()
    queue_generation(session, project)

    processing = client.post(f"/api/v1/projects/{project.id}/process")
    deleted = client.delete(f"/api/v1/projects/{project.id}")

    assert processing.status_code == 409 and processing.json()["error"]["code"] == "PROJECT_BUSY"
    assert deleted.status_code == 409 and deleted.json()["error"]["code"] == "PROJECT_BUSY"
    assert client.get(f"/api/v1/projects/{project.id}").status_code == 200


def test_a_generation_that_finds_itself_running_again_is_interrupted(
    session: Session, env: Environment
) -> None:
    """A redelivery of the same task after a restart settles the row instead of repeating it."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project, task_id="task-9")
    generation.status = JobStatus.RUNNING
    session.commit()

    perform_generation(generation.id, env.settings, celery_task_id="other-task")
    assert reload(session, generation).status == JobStatus.RUNNING

    perform_generation(generation.id, env.settings, celery_task_id="task-9")
    stored = reload(session, generation)
    assert stored.status == JobStatus.FAILED and stored.error_code == "GENERATION_INTERRUPTED"
    assert not env.server_was_started()


def test_startup_reconciliation_fails_a_generation_no_worker_owns(
    session: Session, env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A running generation without a live task is failed and its folder removed."""
    project = review_project(session, get_settings(), spoken_texts(1))
    generation = queue_generation(session, project, task_id="lost-task")
    generation.status = JobStatus.RUNNING
    session.commit()
    leftover = (
        get_settings().data_root / "projects" / project.id / "generations" / generation.id / ".work"
    )
    leftover.mkdir(parents=True)
    monkeypatch.setattr("app.services.reconciliation._active_celery_task_ids", lambda: set())

    reconcile_interrupted_work(get_settings())

    stored = reload(session, generation)
    assert stored.status == JobStatus.FAILED and stored.error_code == "GENERATION_INTERRUPTED"
    assert not leftover.parent.exists()


# --- endpoints ---------------------------------------------------------------------------------


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Capture what the endpoint hands to Celery instead of running the task."""
    from app.tasks.celery_app import generate_document_task

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        generate_document_task, "apply_async", lambda **kwargs: calls.append(kwargs)
    )
    return calls


def error_code(response: Any) -> str:
    """Return the stable code of an error response."""
    return str(response.json()["error"]["code"])


def test_endpoint_queues_the_generation_and_the_task_produces_the_document(
    client: TestClient, session: Session, env: Environment, dispatched: list[dict[str, Any]]
) -> None:
    """Queue through the API, run the task body, then read the document back over HTTP."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(2))

    created = client.post(f"/api/v1/projects/{project.id}/generations", json={"language": "it"})

    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "queued" and body["projectId"] == project.id
    assert body["modelId"] == MODEL_ID and body["language"] == "it"
    assert body["promptVersion"] == GENERATION_PROMPT_VERSION
    assert body["modelFiles"] == [
        {"name": item.name, "sizeBytes": item.size_bytes, "sha256": item.sha256}
        for item in SMALL_OPTION.files
    ]
    assert body["documentAvailable"] is False and body["partsTotal"] == 0
    assert "relativePath" not in body
    assert dispatched[0]["args"] == (body["id"],)
    assert client.get(f"/api/v1/generations/{body['id']}/document").status_code == 409
    assert error_code(client.get(f"/api/v1/generations/{body['id']}/document")) == (
        "GENERATION_NOT_READY"
    )

    perform_generation(body["id"], env.settings, celery_task_id=dispatched[0]["task_id"])

    done = client.get(f"/api/v1/generations/{body['id']}").json()
    assert done["status"] == "completed" and done["documentAvailable"] is True
    assert (
        done["partsTotal"] == 1 and done["partsCompleted"] == 1 and done["progressPercent"] == 100
    )
    document = client.get(f"/api/v1/generations/{body['id']}/document")
    assert document.status_code == 200
    assert document.headers["content-type"].startswith("text/markdown")
    assert "Answer 1" in document.text
    listing = client.get(f"/api/v1/projects/{project.id}/generations").json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == body["id"]
    assert client.get(f"/api/v1/projects/{project.id}").json()["status"] == "review"


def test_a_second_generation_is_refused_while_one_is_active(
    client: TestClient, session: Session, env: Environment, dispatched: list[dict[str, Any]]
) -> None:
    """One active generation per project; a finished one allows the next."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    url = f"/api/v1/projects/{project.id}/generations"

    first = client.post(url)
    second = client.post(url)

    assert first.status_code == 202
    assert second.status_code == 409 and error_code(second) == "PROJECT_BUSY"
    client.post(f"/api/v1/generations/{first.json()['id']}/cancel")
    assert client.post(url).status_code == 202
    assert len(dispatched) == 2


def test_endpoint_refusals_name_the_reason(
    client: TestClient, session: Session, env: Environment, dispatched: list[dict[str, Any]]
) -> None:
    """Each precondition has its own status and stable code, and nothing is stored or queued."""
    uploaded = review_project(session, env.settings, spoken_texts(1), state=ProjectState.UPLOADED)
    empty = review_project(session, env.settings, [], with_screenshot=False)
    ready = review_project(session, env.settings, spoken_texts(1))

    assert error_code(client.post(f"/api/v1/projects/{ready.id}/generations")) == (
        "GENERATION_MODEL_UNAVAILABLE"
    )
    install_model(session, env.settings, enabled=False)
    not_enabled = client.post(f"/api/v1/projects/{ready.id}/generations")
    assert not_enabled.status_code == 409
    assert error_code(not_enabled) == "GENERATION_MODEL_UNAVAILABLE"
    transition_model(session, env.settings, MODEL_ID, State.ENABLED)

    wrong_state = client.post(f"/api/v1/projects/{uploaded.id}/generations")
    assert wrong_state.status_code == 409 and error_code(wrong_state) == "INVALID_PROJECT_STATE"
    no_transcript = client.post(f"/api/v1/projects/{empty.id}/generations")
    assert no_transcript.status_code == 409 and error_code(no_transcript) == "INVALID_PROJECT_STATE"
    unknown = client.post("/api/v1/projects/00000000-0000-0000-0000-000000000000/generations")
    assert unknown.status_code == 404 and error_code(unknown) == "PROJECT_NOT_FOUND"
    bad_language = client.post(
        f"/api/v1/projects/{ready.id}/generations", json={"language": "klingon"}
    )
    assert bad_language.status_code == 422

    disabled = Settings(data_root=env.settings.data_root, local_generation="disabled")
    app.dependency_overrides[get_settings] = lambda: disabled
    switched_off = client.post(f"/api/v1/projects/{ready.id}/generations")
    assert switched_off.status_code == 403 and error_code(switched_off) == (
        "LOCAL_GENERATION_DISABLED"
    )

    assert dispatched == []
    assert client.get(f"/api/v1/projects/{ready.id}/generations").json()["total"] == 0
    assert error_code(client.get("/api/v1/generations/missing")) == "GENERATION_NOT_FOUND"
    assert error_code(client.post("/api/v1/generations/missing/cancel")) == "GENERATION_NOT_FOUND"
    assert error_code(client.get("/api/v1/generations/missing/document")) == (
        "GENERATION_NOT_FOUND"
    )


def test_unavailable_worker_fails_the_queued_generation_with_a_503(
    client: TestClient, session: Session, env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dispatch that raises leaves a failed row, not a generation that waits forever."""
    from app.tasks.celery_app import generate_document_task

    def refuse(**_: object) -> None:
        raise ConnectionError("broker down")

    monkeypatch.setattr(generate_document_task, "apply_async", refuse)
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))

    response = client.post(f"/api/v1/projects/{project.id}/generations")

    assert response.status_code == 503 and error_code(response) == "WORKER_UNAVAILABLE"
    items = client.get(f"/api/v1/projects/{project.id}/generations").json()["items"]
    assert items[0]["status"] == "failed" and items[0]["errorCode"] == "WORKER_UNAVAILABLE"
    assert client.post(f"/api/v1/projects/{project.id}/generations").status_code == 503


def test_a_model_cannot_be_deleted_while_a_generation_is_active(
    client: TestClient, session: Session, env: Environment
) -> None:
    """`MODEL_IN_USE` is answered from the generation table, then deletion works again."""
    install_model(session, env.settings)
    project = review_project(session, env.settings, spoken_texts(1))
    generation = queue_generation(session, project)

    blocked = client.delete(f"/api/v1/models/{MODEL_ID}")
    assert blocked.status_code == 409 and error_code(blocked) == "MODEL_IN_USE"
    assert model_directory(env.settings, MODEL_ID).is_dir()

    generation.status = JobStatus.COMPLETED
    generation.completed_at = utc_now()
    session.commit()
    assert client.delete(f"/api/v1/models/{MODEL_ID}").status_code == 200


def test_removing_the_original_video_is_refused_while_a_generation_is_active(
    session: Session, env: Environment
) -> None:
    """The job may decode frames from the video, so its removal waits for the generation."""
    from app.services.storage import REASON_BUSY, removal_blocked_reason

    project = review_project(session, env.settings, spoken_texts(1))
    session.add(
        MediaAsset(
            project_id=project.id,
            display_filename="a.mp4",
            stored_filename="source.mp4",
            relative_path=f"projects/{project.id}/source/source.mp4",
            content_type="video/mp4",
            size_bytes=5,
            duration_seconds=4,
            width=640,
            height=360,
            container_format="mp4",
            metadata_json={},
        )
    )
    session.commit()
    session.refresh(project)
    queue_generation(session, project)

    assert removal_blocked_reason(session, project, env.settings.data_root) == REASON_BUSY
