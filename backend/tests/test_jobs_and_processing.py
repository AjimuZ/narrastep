"""Background job API and deterministic orchestration tests."""

from __future__ import annotations

import asyncio
import json
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.main import app
from app.models import (
    JobStatus,
    MediaAsset,
    ProcessingJob,
    Project,
    ProjectExport,
    ProjectState,
    Screenshot,
    SegmentScreenshotAssociation,
    TranscriptSegment,
)
from app.services.media import MediaMetadata
from app.services.pipeline import PersistedFrameCandidate
from app.services.processing import perform_processing
from app.services.workspaces import (
    acquire_project_operation,
    create_project_workspace,
    release_project_operation,
)


def _uploaded_project(session: Session) -> tuple[Project, Path]:
    """Arrange one validated source record and matching safe local file."""
    project = Project(name="Process", state=ProjectState.UPLOADED)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    source = workspace / "source" / "source.mp4"
    source.write_bytes(b"local-video-fixture")
    asset = MediaAsset(
        project_id=project.id,
        display_filename="demo.mp4",
        stored_filename="source.mp4",
        relative_path=source.relative_to(get_settings().data_root).as_posix(),
        content_type="video/mp4",
        size_bytes=source.stat().st_size,
        duration_seconds=4,
        width=640,
        height=360,
        container_format="mp4",
        metadata_json={},
    )
    session.add(asset)
    session.commit()
    return project, source


def test_queue_and_cancel_job_use_persisted_frontend_contract(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publish a queued job before dispatch and cancel it durably before execution."""
    project, _ = _uploaded_project(session)
    from app.tasks.celery_app import process_project_task

    monkeypatch.setattr(
        process_project_task,
        "apply_async",
        lambda **_: None,
    )

    queued = client.post(f"/api/v1/projects/{project.id}/process", json={})
    assert queued.status_code == 202
    job = queued.json()
    assert job["status"] == "queued"
    assert job["currentStage"] == "queued"
    assert job["version"] == 1
    assert job["selectionStrategy"] == "legacy"
    assert job["ocrMode"] == "off"
    assert job["imageBudgetProfile"] == "balanced"
    persisted_job = session.get(ProcessingJob, job["id"])
    assert persisted_job is not None
    assert persisted_job.configuration_snapshot == {
        "algorithmVersion": "legacy_v1",
        "selectionStrategy": "legacy",
        "ocrMode": "off",
        "imageBudgetProfile": "balanced",
        "tesseractPath": "tesseract",
        "screenshotAlternatives": 2,
        "projectMode": "tutorial",
        "directDuplicateThreshold": 0.94,
        "borderlineDuplicateThreshold": 0.84,
        "borderlineSsimThreshold": 0.96,
        "ocrTokenJaccardThreshold": 0.85,
    }
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["id"] == job["id"]
    assert client.get(f"/api/v1/projects/{project.id}").json()["activeJobId"] == job["id"]

    cancelled = client.post(f"/api/v1/jobs/{job['id']}/cancel", json={})
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["currentStage"] == "cancelled"
    assert client.get(f"/api/v1/projects/{project.id}").json()["status"] == "uploaded"


def test_completed_job_sse_begins_with_persisted_snapshot(
    client: TestClient, session: Session
) -> None:
    """Resynchronize terminal progress immediately and close the event stream cleanly."""
    project = Project(name="Completed", state=ProjectState.REVIEW)
    session.add(project)
    session.flush()
    job = ProcessingJob(
        project_id=project.id,
        status=JobStatus.COMPLETED,
        stage="completed",
        progress_percent=100,
        progress_version=9,
        message="Ready for review.",
    )
    session.add(job)
    session.commit()

    response = client.get(f"/api/v1/jobs/{job.id}/events")

    assert response.status_code == 200
    assert "id: 9" in response.text
    assert "event: snapshot" in response.text
    assert '"currentStage":"completed"' in response.text
    assert '"version":9' in response.text


def test_adaptive_job_snapshots_effective_ocr_profile_and_thresholds(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep adaptive worker behavior stable after its API request has been queued."""
    project, _ = _uploaded_project(session)
    from app.tasks.celery_app import process_project_task

    monkeypatch.setattr(process_project_task, "apply_async", lambda **_: None)
    adaptive_settings: Settings = get_settings().model_copy(
        update={
            "screenshot_selection_strategy": "adaptive_v1",
            "ocr_mode": "selective",
            "image_budget_profile": "compact",
            "adaptive_direct_duplicate_threshold": 0.93,
        }
    )
    app.dependency_overrides[get_settings] = lambda: adaptive_settings
    try:
        response = client.post(f"/api/v1/projects/{project.id}/process", json={})
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 202
    payload = response.json()
    assert payload["selectionStrategy"] == "adaptive_v1"
    assert payload["ocrMode"] == "selective"
    assert payload["imageBudgetProfile"] == "compact"
    persisted_job = session.get(ProcessingJob, payload["id"])
    assert persisted_job is not None
    assert persisted_job.configuration_snapshot["algorithmVersion"] == "adaptive_v1.0"
    assert persisted_job.configuration_snapshot["directDuplicateThreshold"] == 0.93


def test_export_request_is_persisted_before_worker_dispatch(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return an honest queued export record while immutable generation runs asynchronously."""
    project = Project(name="Export", state=ProjectState.REVIEW)
    session.add(project)
    session.commit()
    from app.tasks.celery_app import export_project_task

    monkeypatch.setattr(
        export_project_task,
        "apply_async",
        lambda **_: None,
    )

    response = client.post(
        f"/api/v1/projects/{project.id}/exports",
        json={"includeOriginalVideo": False},
    )

    assert response.status_code == 202
    export = response.json()
    assert export["status"] == "queued"
    assert export["includeOriginalVideo"] is False
    assert export["currentStage"] == "queued"
    assert export["metrics"] == {}
    history = client.get(f"/api/v1/projects/{project.id}/exports")
    assert history.status_code == 200
    assert history.json()["items"][0]["id"] == export["id"]


def test_mock_enabled_orchestration_persists_review_evidence(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the full durable worker flow with explicit deterministic test substitutes."""
    project, _ = _uploaded_project(session)
    project.state = ProjectState.QUEUED
    job = ProcessingJob(project_id=project.id)
    session.add(job)
    session.commit()
    metadata = MediaMetadata(
        format_name="mp4",
        duration_seconds=4,
        size_bytes=20,
        width=640,
        height=360,
        video_codec="h264",
        audio_codec="aac",
        frame_rate=25,
        content_type="video/mp4",
    )

    def fake_audio(_: Path, destination: Path, **__: object) -> Path:
        """Create legal PCM input while avoiding a host FFmpeg dependency in unit tests."""
        with wave.open(str(destination), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16_000)
            output.writeframes(b"\x00\x00" * 64_000)
        return destination

    def fake_frames(
        _: Path, __: object, output_directory: Path, **___: object
    ) -> list[PersistedFrameCandidate]:
        """Stage one PNG candidate through the bounded-memory processing contract."""
        output_directory.mkdir(parents=True, exist_ok=True)
        path = output_directory / "candidate.png"
        from PIL import Image

        Image.new("RGB", (32, 18), (120, 120, 120)).save(path)
        return [
            PersistedFrameCandidate(
                timestamp_seconds=2,
                image_path=path,
                stability=1,
                sharpness=0.5,
                exposure_penalty=0,
                quality_score=0.9,
                perceptual_hash="0123456789abcdef",
                source_index=0,
                width=32,
                height=18,
            )
        ]

    monkeypatch.setattr("app.services.processing.validate_video", lambda *_args, **_kw: metadata)
    monkeypatch.setattr("app.services.processing.extract_pcm_audio", fake_audio)
    monkeypatch.setattr(
        "app.services.processing.extract_deduplicated_frames_to_directory",
        fake_frames,
    )
    monkeypatch.setattr("app.services.progress.publish_job_event", lambda _job: None)

    perform_processing(job.id, get_settings())

    session.expire_all()
    completed_job = session.get(ProcessingJob, job.id)
    completed_project = session.get(Project, project.id)
    assert completed_job is not None and completed_job.status == JobStatus.COMPLETED
    assert completed_job.progress_percent == 100
    assert completed_job.metrics_version == 1
    assert completed_job.metrics_summary["terminalStatus"] == "completed"
    assert completed_job.metrics_summary["screenshotRowCount"] == 1
    assert completed_job.diagnostics_relative_path is not None
    job_response = client.get(f"/api/v1/jobs/{completed_job.id}").json()
    assert job_response["metrics"]["screenshotRowCount"] == 1
    assert completed_project is not None and completed_project.state == ProjectState.REVIEW
    assert completed_project.detected_language == "en"
    assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 1
    assert session.scalar(select(func.count()).select_from(Screenshot)) == 1
    assert session.scalar(select(func.count()).select_from(SegmentScreenshotAssociation)) == 1
    raw_path = (
        create_project_workspace(get_settings().data_root, project.id)
        / "transcript"
        / "raw-transcript.json"
    )
    assert raw_path.is_file()
    audio_path = (
        create_project_workspace(get_settings().data_root, project.id)
        / "audio"
        / "normalized-audio.wav"
    )
    assert audio_path.is_file()
    diagnostics_path = get_settings().data_root / completed_job.diagnostics_relative_path
    diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    assert diagnostics["operationId"] == completed_job.id
    assert "Hello from the local Narrastep test pipeline." not in diagnostics_path.read_text(
        encoding="utf-8"
    )


def test_eager_dispatch_refreshes_terminal_job_before_response(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Return worker-completed state when eager execution finishes during task handoff."""
    project, _ = _uploaded_project(session)
    from app.tasks.celery_app import process_project_task

    def complete_immediately(*, args: tuple[str], task_id: str) -> None:
        """Simulate eager completion in a separate worker session before route return."""
        with SessionLocal() as worker_session:
            job = worker_session.get(ProcessingJob, args[0])
            assert job is not None and job.celery_task_id == task_id
            job.status = JobStatus.COMPLETED
            job.stage = "completed"
            job.progress_percent = 100
            job.completed_at = job.updated_at
            job.project.state = ProjectState.REVIEW
            worker_session.commit()

    monkeypatch.setattr(process_project_task, "apply_async", complete_immediately)
    response = client.post(f"/api/v1/projects/{project.id}/process", json={})
    assert response.status_code == 202
    assert response.json()["status"] == "completed"
    assert response.json()["currentStage"] == "completed"


def test_failed_project_exposes_latest_job_after_refresh(
    client: TestClient, session: Session
) -> None:
    """Let the processing page reload persisted failure details through activeJobId."""
    project = Project(name="Failed", state=ProjectState.FAILED)
    session.add(project)
    session.flush()
    job = ProcessingJob(
        project_id=project.id,
        status=JobStatus.FAILED,
        stage="failed",
        error_code="TRANSCRIPTION_FAILED",
    )
    session.add(job)
    session.commit()
    response = client.get(f"/api/v1/projects/{project.id}")
    assert response.status_code == 200
    assert response.json()["activeJobId"] == job.id
    loaded = client.get(f"/api/v1/jobs/{job.id}").json()
    assert loaded["errorCode"] == "TRANSCRIPTION_FAILED"


@pytest.mark.asyncio
async def test_sse_uses_redis_wakeup_then_resynchronizes_from_sqlite(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treat Redis as a wakeup while emitting the newer authoritative database version."""
    from app.routes import api

    project = Project(name="Stream", state=ProjectState.PROCESSING)
    session.add(project)
    session.flush()
    job = ProcessingJob(
        project_id=project.id,
        status=JobStatus.RUNNING,
        progress_version=1,
        stage="transcribing",
    )
    session.add(job)
    session.commit()

    class FakePubSub:
        """Return one immediate notification without requiring a real Redis server."""

        async def get_message(self, **_: object) -> dict[str, object]:
            """Wake the stream after the database update is committed."""
            return {"type": "message"}

    fake_pubsub = FakePubSub()

    async def open_fake(*_: object) -> tuple[object, FakePubSub]:
        """Provide deterministic pub/sub resources for the stream loop."""
        return object(), fake_pubsub

    async def close_fake(_: object) -> None:
        """Accept fake cleanup without external I/O."""

    monkeypatch.setattr(api, "_open_job_pubsub", open_fake)
    monkeypatch.setattr(api, "_close_job_pubsub", close_fake)
    settings = get_settings().model_copy(
        update={"sse_poll_seconds": 0.01, "sse_heartbeat_seconds": 1}
    )
    stream = api._job_event_stream(job.id, -1, settings)
    snapshot = await anext(stream)
    assert "event: snapshot" in snapshot and "id: 1" in snapshot

    with SessionLocal() as writer:
        current = writer.get(ProcessingJob, job.id)
        assert current is not None
        current.progress_version = 2
        current.progress_percent = 40
        writer.commit()
    progress = await asyncio.wait_for(anext(stream), timeout=1)
    await stream.aclose()
    payload = json.loads(progress.split("data: ", maxsplit=1)[1])
    assert "event: progress" in progress and payload["version"] == 2
    assert payload["createdAt"].endswith("Z")


def test_running_cancellation_persists_before_redis_hint(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep SQLite authoritative by committing cancellation before publishing its Redis hint."""
    project = Project(name="Cancel running", state=ProjectState.PROCESSING)
    session.add(project)
    session.flush()
    job = ProcessingJob(project_id=project.id, status=JobStatus.RUNNING)
    session.add(job)
    session.commit()
    observed: list[JobStatus] = []

    def observe_persisted(job_id: str) -> None:
        """Read cancellation through a separate connection at publish time."""
        with SessionLocal() as observer:
            persisted = observer.get(ProcessingJob, job_id)
            assert persisted is not None and persisted.cancel_requested is True
            observed.append(persisted.status)

    monkeypatch.setattr("app.routes.api.publish_cancellation_flag", observe_persisted)
    monkeypatch.setattr("app.routes.api.publish_job_event", lambda _job: None)
    response = client.post(f"/api/v1/jobs/{job.id}/cancel", json={})
    assert response.status_code == 202
    assert response.json()["status"] == "cancelling"
    assert observed == [JobStatus.CANCELLING]


def test_export_queueing_serializes_against_in_flight_review_mutation(
    client: TestClient, session: Session
) -> None:
    """Avoid capturing a stale review transaction inside an immutable export request."""
    project = Project(name="Review lock", state=ProjectState.REVIEW)
    session.add(project)
    session.commit()
    reservation = acquire_project_operation(get_settings().data_root, project.id, "review")
    try:
        response = client.post(
            f"/api/v1/projects/{project.id}/exports",
            json={"includeOriginalVideo": False},
        )
    finally:
        release_project_operation(reservation)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_BUSY"
    session.expire_all()
    assert session.get(Project, project.id).state == ProjectState.REVIEW  # type: ignore[union-attr]
    assert session.scalar(select(func.count()).select_from(ProjectExport)) == 0
