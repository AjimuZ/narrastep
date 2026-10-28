"""Durable processing progress updates with best-effort Redis notification."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import JobStatus, ProcessingJob, utc_now
from app.schemas import serialize_utc

logger = logging.getLogger(__name__)


def job_event_payload(job: ProcessingJob) -> dict[str, Any]:
    """Build the small camel-case event persisted clients need to render progress."""
    return {
        "id": job.id,
        "projectId": job.project_id,
        "status": job.status.value,
        "currentStage": job.stage,
        "progressPercent": job.progress_percent,
        "message": job.message,
        "version": job.progress_version,
        "errorCode": job.error_code,
        "errorMessage": job.error_message,
        "updatedAt": _isoformat(job.updated_at),
        "completedAt": _isoformat(job.completed_at),
    }


def _isoformat(value: datetime | None) -> str | None:
    """Serialize an optional timestamp consistently for Redis and SSE payloads."""
    return serialize_utc(value) if value is not None else None


def publish_job_event(job: ProcessingJob) -> None:
    """Publish a persisted snapshot while treating Redis as a non-authoritative channel."""
    settings = get_settings()
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        client.publish(f"job:{job.id}", json.dumps(job_event_payload(job), separators=(",", ":")))
        client.close()
    except RedisError:
        # SQLite remains authoritative; reconnecting clients always receive its latest snapshot.
        logger.warning("Progress notification could not be published for job %s.", job.id)


def publish_cancellation_flag(job_id: str) -> None:
    """Publish a short-lived cancellation hint while SQLite remains authoritative."""
    settings = get_settings()
    if settings.environment == "test":
        return
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        client.set(
            f"job:{job_id}:cancelled",
            "1",
            ex=settings.job_visibility_timeout_seconds,
        )
        client.close()
    except RedisError:
        logger.warning("Cancellation hint could not be published for job %s.", job_id)


def clear_cancellation_flag(job_id: str) -> None:
    """Remove a transient cancellation hint after any durable terminal outcome."""
    settings = get_settings()
    if settings.environment == "test":
        return
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        client.delete(f"job:{job_id}:cancelled")
        client.close()
    except RedisError:
        logger.warning("Cancellation hint could not be cleared for job %s.", job_id)


def update_job_progress(
    session: Session,
    job: ProcessingJob,
    *,
    stage: str,
    progress_percent: int,
    status: JobStatus | None = None,
    message: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    completed: bool = False,
) -> ProcessingJob:
    """Persist a monotonic progress snapshot before notifying live clients."""
    job.stage = stage
    job.progress_percent = max(job.progress_percent, min(100, progress_percent))
    job.progress_version += 1
    job.updated_at = utc_now()
    if status is not None:
        job.status = status
    if message is not None:
        job.message = message[:300]
    if error_code is not None:
        job.error_code = error_code
    if error_message is not None:
        job.error_message = error_message[:500]
    if completed:
        job.completed_at = utc_now()
    session.add(job)
    session.commit()
    session.refresh(job)
    publish_job_event(job)
    return job


def cancellation_requested(session: Session, job_id: str) -> bool:
    """Consult durable SQLite state and the best-effort Redis cancellation hint."""
    session.expire_all()
    job = session.get(ProcessingJob, job_id)
    if job is None or job.cancel_requested:
        return True
    settings = get_settings()
    if settings.environment == "test":
        return False
    try:
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
        cancelled = client.get(f"job:{job_id}:cancelled") is not None
        client.close()
        return cancelled
    except RedisError:
        return False
