"""Concurrency, restart, dispatch, and durable lifecycle regression tests."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from package_reader import open_package
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models import (
    ExportStatus,
    JobStatus,
    MediaAsset,
    ProcessingJob,
    Project,
    ProjectExport,
    ProjectState,
    TranscriptSegment,
)
from app.services.processing import perform_export, perform_processing
from app.services.reconciliation import reconcile_interrupted_work
from app.services.workspaces import create_project_workspace


def _project_with_source(session: Session, state: ProjectState) -> Project:
    """Create one project with a source row and owned file for lifecycle tests."""
    project = Project(name="Resilience", state=state)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    source = workspace / "source" / "source.mp4"
    source.write_bytes(b"source")
    session.add(
        MediaAsset(
            project_id=project.id,
            display_filename="source.mp4",
            stored_filename="source.mp4",
            relative_path=source.relative_to(get_settings().data_root).as_posix(),
            content_type="video/mp4",
            size_bytes=6,
            duration_seconds=1,
            width=16,
            height=9,
            container_format="mp4",
            metadata_json={},
        )
    )
    session.commit()
    return project


def test_worker_preflight_rejects_before_persisting_state(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Leave an uploaded project untouched when no worker answers the bounded preflight."""
    project = _project_with_source(session, ProjectState.UPLOADED)
    production_settings = get_settings().model_copy(
        update={"environment": "production", "mock_processing": False, "celery_always_eager": False}
    )
    app.dependency_overrides[get_settings] = lambda: production_settings
    monkeypatch.setattr("app.routes.api._redis_available", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.routes.api._worker_available", lambda *_args, **_kwargs: False)
    try:
        response = client.post(f"/api/v1/projects/{project.id}/process", json={})
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WORKER_UNAVAILABLE"
    session.expire_all()
    assert session.get(Project, project.id).state == ProjectState.UPLOADED  # type: ignore[union-attr]
    assert session.scalar(select(func.count()).select_from(ProcessingJob)) == 0


def test_database_enforces_active_operation_and_enum_constraints(session: Session) -> None:
    """Backstop API compare-and-set logic with partial uniqueness and enum checks."""
    project = Project(name="Unique", state=ProjectState.QUEUED)
    session.add(project)
    session.flush()
    session.add(ProcessingJob(project_id=project.id))
    session.commit()
    session.add(ProcessingJob(project_id=project.id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    export_project = Project(name="Unique export", state=ProjectState.EXPORTING)
    session.add(export_project)
    session.flush()
    session.add(ProjectExport(project_id=export_project.id))
    session.commit()
    session.add(ProjectExport(project_id=export_project.id))
    with pytest.raises(IntegrityError):
        session.commit()
    session.rollback()

    with pytest.raises(IntegrityError):
        session.execute(
            text("UPDATE projects SET state = 'invalid' WHERE id = :id"), {"id": project.id}
        )
        session.commit()
    session.rollback()


def test_same_task_processing_redelivery_interrupts_and_cleans_owned_artifacts(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve a fresh RUNNING redelivery and remove derivatives left by its dead owner."""
    project = _project_with_source(session, ProjectState.PROCESSING)
    job = ProcessingJob(
        project_id=project.id,
        status=JobStatus.RUNNING,
        celery_task_id="same-task",
        stage="transcribing",
    )
    segment = TranscriptSegment(
        project_id=project.id,
        sequence=0,
        start_seconds=0,
        end_seconds=1,
        raw_text="partial",
        reviewed_text="partial",
        raw_words=[],
    )
    session.add_all([job, segment])
    session.commit()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    audio = workspace / "audio" / "normalized-audio.wav"
    raw = workspace / "transcript" / "raw-transcript.json"
    temporary = workspace / ".processing" / job.id / "screenshots" / "candidate.png"
    audio.write_bytes(b"partial")
    raw.write_text("{}", encoding="utf-8")
    temporary.parent.mkdir(parents=True)
    temporary.write_bytes(b"partial")
    monkeypatch.setattr("app.services.processing.clear_cancellation_flag", lambda _job_id: None)
    monkeypatch.setattr("app.services.processing.publish_job_event", lambda _job: None)

    perform_processing(job.id, get_settings(), celery_task_id="same-task")

    session.expire_all()
    interrupted = session.get(ProcessingJob, job.id)
    assert interrupted is not None and interrupted.status == JobStatus.FAILED
    assert interrupted.error_code == "PROCESSING_INTERRUPTED"
    assert not audio.exists() and not raw.exists() and not temporary.exists()
    assert session.scalar(select(func.count()).select_from(TranscriptSegment)) == 0


def test_same_task_cancelling_redelivery_finishes_cancelled(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return a lost cancelling job to uploaded instead of misclassifying it as failed."""
    project = _project_with_source(session, ProjectState.PROCESSING)
    job = ProcessingJob(
        project_id=project.id,
        status=JobStatus.CANCELLING,
        cancel_requested=True,
        celery_task_id="cancel-task",
    )
    session.add(job)
    session.commit()
    monkeypatch.setattr("app.services.processing.clear_cancellation_flag", lambda _job_id: None)
    monkeypatch.setattr("app.services.processing.publish_job_event", lambda _job: None)

    perform_processing(job.id, get_settings(), celery_task_id="cancel-task")

    session.expire_all()
    cancelled = session.get(ProcessingJob, job.id)
    assert cancelled is not None and cancelled.status == JobStatus.CANCELLED
    assert session.get(Project, project.id).state == ProjectState.UPLOADED  # type: ignore[union-attr]


def test_same_task_export_redelivery_fails_without_touching_completed_zip(
    session: Session,
) -> None:
    """Remove only a partial redelivery artifact and never rebuild a completed export."""
    project = Project(name="Export", state=ProjectState.EXPORTING)
    session.add(project)
    session.flush()
    export = ProjectExport(
        project_id=project.id,
        status=ExportStatus.RUNNING,
        celery_task_id="export-task",
    )
    session.add(export)
    session.commit()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    destination = workspace / "exports" / f"narrastep-{export.id}.zip"
    partial = destination.with_suffix(".zip.partial")
    partial.write_bytes(b"partial")

    perform_export(export.id, get_settings(), celery_task_id="export-task")

    session.expire_all()
    failed = session.get(ProjectExport, export.id)
    assert failed is not None and failed.status == ExportStatus.FAILED
    assert not partial.exists() and not destination.exists()
    assert session.get(Project, project.id).state == ProjectState.REVIEW  # type: ignore[union-attr]


def test_reconciliation_waits_for_reachable_inspector_and_preserves_broker_queue(
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Avoid false interruption when control is unavailable or a queued task remains in Redis."""
    old = datetime.now(UTC) - timedelta(hours=1)
    running_project = Project(name="Running", state=ProjectState.PROCESSING)
    queued_project = Project(name="Queued", state=ProjectState.QUEUED)
    fresh_project = Project(name="Fresh orphan", state=ProjectState.PROCESSING)
    session.add_all([running_project, queued_project, fresh_project])
    session.flush()
    running = ProcessingJob(
        project_id=running_project.id,
        status=JobStatus.RUNNING,
        celery_task_id="running-task",
        updated_at=old,
    )
    queued = ProcessingJob(
        project_id=queued_project.id,
        status=JobStatus.QUEUED,
        celery_task_id="broker-task",
        updated_at=old,
    )
    fresh = ProcessingJob(
        project_id=fresh_project.id,
        status=JobStatus.RUNNING,
        celery_task_id="lost-task",
        updated_at=datetime.now(UTC),
    )
    session.add_all([running, queued, fresh])
    session.commit()
    monkeypatch.setattr("app.services.reconciliation._active_celery_task_ids", lambda: None)

    reconcile_interrupted_work(get_settings())
    session.expire_all()
    assert session.get(ProcessingJob, running.id).status == JobStatus.RUNNING  # type: ignore[union-attr]

    monkeypatch.setattr("app.services.reconciliation._active_celery_task_ids", lambda: set())
    reconcile_interrupted_work(get_settings())
    session.expire_all()
    assert session.get(ProcessingJob, running.id).status == JobStatus.FAILED  # type: ignore[union-attr]
    assert session.get(ProcessingJob, fresh.id).status == JobStatus.FAILED  # type: ignore[union-attr]
    assert session.get(ProcessingJob, queued.id).status == JobStatus.QUEUED  # type: ignore[union-attr]


def test_processing_setup_failure_reaches_safe_terminal_state(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never leave a claimed job PROCESSING when workspace setup fails before media work."""
    project = _project_with_source(session, ProjectState.QUEUED)
    job = ProcessingJob(project_id=project.id)
    session.add(job)
    session.commit()
    monkeypatch.setattr(
        "app.services.processing.create_project_workspace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("simulated")),
    )
    monkeypatch.setattr("app.services.processing.clear_cancellation_flag", lambda _job_id: None)
    monkeypatch.setattr("app.services.processing.publish_job_event", lambda _job: None)

    perform_processing(job.id, get_settings())

    session.expire_all()
    failed = session.get(ProcessingJob, job.id)
    assert failed is not None and failed.status == JobStatus.FAILED
    assert failed.error_code == "PROCESSING_FAILED"
    assert session.get(Project, project.id).state == ProjectState.FAILED  # type: ignore[union-attr]


def test_export_root_folder_carries_the_project_export_sequence(session: Session) -> None:
    """Name the archive root after the creation date, project type, slug and export number."""
    project = _project_with_source(session, ProjectState.EXPORTING)
    workspace = create_project_workspace(get_settings().data_root, project.id)
    (workspace / "transcript" / "raw-transcript.json").write_text(
        '{"segments": []}', encoding="utf-8"
    )
    earlier = ProjectExport(
        project_id=project.id,
        status=ExportStatus.COMPLETED,
        created_at=datetime(2026, 10, 27, 8, 0, tzinfo=UTC),
    )
    current = ProjectExport(
        project_id=project.id,
        status=ExportStatus.QUEUED,
        created_at=datetime(2026, 10, 28, 9, 0, tzinfo=UTC),
    )
    session.add_all([earlier, current])
    session.commit()

    perform_export(current.id, get_settings())

    session.expire_all()
    done = session.get(ProjectExport, current.id)
    assert done is not None and done.status == ExportStatus.COMPLETED, done.error_code
    assert done.relative_path is not None
    with open_package(get_settings().data_root / done.relative_path) as archive:
        assert archive.root == f"2026-10-28_{project.output_mode.value}_resilience_export-02"
        manifest = json.loads(archive.read("metadata/manifest.json"))
    assert manifest["schemaVersion"] == "0.3.0"
    assert manifest["archiveName"] == archive.root


def test_version_0_2_export_still_downloads_unchanged(client: TestClient, session: Session) -> None:
    """Serve an archive written in the Version 0.2 layout byte for byte; it is never rewritten."""
    project = Project(name="Legacy", state=ProjectState.COMPLETED)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    export = ProjectExport(project_id=project.id, status=ExportStatus.COMPLETED)
    session.add(export)
    session.flush()
    archive_path = workspace / "exports" / f"narrastep-{export.id}.zip"
    with zipfile.ZipFile(archive_path, "w") as legacy:
        legacy.writestr("manifest.json", json.dumps({"schemaVersion": "0.2.0"}))
        legacy.writestr("screenshots/step-001.png", b"png")
        legacy.writestr("llm/llm-input.md", "# Narrastep evidence package\n")
    export.relative_path = archive_path.relative_to(get_settings().data_root).as_posix()
    session.commit()

    response = client.get(f"/api/v1/exports/{export.id}/download")

    assert response.status_code == 200
    assert response.content == archive_path.read_bytes()
    with zipfile.ZipFile(io.BytesIO(response.content)) as served:
        assert served.namelist()[0] == "manifest.json"
        assert json.loads(served.read("manifest.json"))["schemaVersion"] == "0.2.0"
