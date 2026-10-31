"""Startup reconciliation for background work interrupted by process restarts."""

from __future__ import annotations

import logging
import shutil
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select

from app.config import Settings
from app.database import SessionLocal
from app.models import (
    ExportStatus,
    Generation,
    JobStatus,
    ProcessingJob,
    ProjectExport,
    ProjectState,
    utc_now,
)
from app.services.workspaces import ensure_within_root, project_relative_path

logger = logging.getLogger(__name__)


def reconcile_interrupted_work(settings: Settings) -> None:
    """Mark stale non-terminal work failed when no active Celery task can own it."""
    threshold = datetime.now(UTC) - timedelta(seconds=settings.orphaned_job_seconds)
    with SessionLocal() as session:
        stale_jobs = list(
            session.scalars(
                select(ProcessingJob).where(
                    or_(
                        ProcessingJob.status.in_((JobStatus.RUNNING, JobStatus.CANCELLING)),
                        and_(
                            ProcessingJob.status == JobStatus.QUEUED,
                            ProcessingJob.updated_at < threshold,
                        ),
                    ),
                )
            )
        )
        stale_exports = list(
            session.scalars(
                select(ProjectExport).where(
                    or_(
                        ProjectExport.status == ExportStatus.RUNNING,
                        and_(
                            ProjectExport.status == ExportStatus.QUEUED,
                            ProjectExport.updated_at < threshold,
                        ),
                    ),
                )
            )
        )
        stale_generations = list(
            session.scalars(
                select(Generation).where(
                    or_(
                        Generation.status.in_((JobStatus.RUNNING, JobStatus.CANCELLING)),
                        and_(
                            Generation.status == JobStatus.QUEUED,
                            Generation.updated_at < threshold,
                        ),
                    ),
                )
            )
        )
        if not stale_jobs and not stale_exports and not stale_generations:
            return
        active_task_ids = _active_celery_task_ids()
        if active_task_ids is None:
            return
        for job in stale_jobs:
            if job.status == JobStatus.QUEUED and job.celery_task_id:
                # Broker-queued tasks are not visible to inspector until a worker reserves them.
                continue
            if job.celery_task_id and job.celery_task_id in active_task_ids:
                continue
            job.status = JobStatus.FAILED
            job.stage = "failed"
            job.message = "Processing was interrupted by a local service restart."
            job.error_code = "PROCESSING_INTERRUPTED"
            job.error_message = (
                "Processing was interrupted and can be retried from the source video."
            )
            job.progress_version += 1
            job.completed_at = utc_now()
            job.project.state = ProjectState.FAILED
        for export in stale_exports:
            if export.status == ExportStatus.QUEUED and export.celery_task_id:
                continue
            if export.celery_task_id and export.celery_task_id in active_task_ids:
                continue
            _cleanup_export_partial(settings, export)
            export.status = ExportStatus.FAILED
            export.current_stage = "failed"
            export.error_code = "EXPORT_FAILED"
            export.error_message = "Export was interrupted and can be started again."
            export.completed_at = utc_now()
            export.project.state = ProjectState.REVIEW
        for generation in stale_generations:
            if generation.status == JobStatus.QUEUED and generation.celery_task_id:
                continue
            if generation.celery_task_id and generation.celery_task_id in active_task_ids:
                continue
            _cleanup_generation_files(settings, generation)
            generation.status = JobStatus.FAILED
            generation.stage = "failed"
            generation.message = "The document could not be generated."
            generation.error_code = "GENERATION_INTERRUPTED"
            generation.error_message = (
                "The generation was interrupted by a local service restart. Start it again."
            )
            generation.progress_version += 1
            generation.completed_at = utc_now()
        session.commit()


def _cleanup_generation_files(settings: Settings, generation: Generation) -> None:
    """Remove only the interrupted generation's own folder, which holds no finished document."""
    directory = ensure_within_root(
        settings.data_root,
        project_relative_path(generation.project_id) / "generations" / generation.id,
    )
    shutil.rmtree(directory, ignore_errors=True)


def _cleanup_export_partial(settings: Settings, export: ProjectExport) -> None:
    """Remove only the interrupted export's request-owned partial ZIP artifact."""
    workspace = ensure_within_root(settings.data_root, project_relative_path(export.project_id))
    destination = workspace / "exports" / f"narrastep-{export.id}.zip"
    try:
        destination.with_suffix(destination.suffix + ".partial").unlink(missing_ok=True)
    except OSError as error:
        logger.error(
            "Interrupted export cleanup failed for export %s (%s).",
            export.id,
            type(error).__name__,
        )


def _active_celery_task_ids() -> set[str] | None:
    """Collect owned task IDs, or return none when worker inspection is inconclusive."""
    try:
        from app.tasks.celery_app import celery_app

        inspector = celery_app.control.inspect(timeout=1)
        if not inspector.ping():
            return None
        snapshots = [inspector.active(), inspector.reserved(), inspector.scheduled()]
    except Exception:
        return None
    if any(snapshot is None for snapshot in snapshots):
        return None
    task_ids: set[str] = set()
    for snapshot in snapshots:
        if not snapshot:
            continue
        for tasks in snapshot.values():
            for task in tasks:
                request = task.get("request", task)
                task_id = request.get("id")
                if task_id:
                    task_ids.add(str(task_id))
    return task_ids
