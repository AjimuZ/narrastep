"""Celery application and task entry points for local serialized background work."""

from __future__ import annotations

import logging
from typing import Any

from celery import Celery  # type: ignore[import-untyped]
from celery.signals import worker_ready  # type: ignore[import-untyped]

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)
celery_app = Celery(
    "narrastep",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)
celery_app.conf.update(
    task_track_started=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    broker_transport_options={"visibility_timeout": settings.job_visibility_timeout_seconds},
    task_always_eager=settings.celery_always_eager,
    task_eager_propagates=settings.environment == "test",
    accept_content=["json"],
    task_serializer="json",
    result_serializer="json",
)


@celery_app.task(bind=True, name="narrastep.process_project")  # type: ignore[untyped-decorator]
def process_project_task(task: Any, job_id: str) -> None:
    """Delegate one durable processing job to the application orchestration service."""
    from app.services.processing import perform_processing

    perform_processing(job_id, celery_task_id=str(task.request.id))


@celery_app.task(bind=True, name="narrastep.export_project")  # type: ignore[untyped-decorator]
def export_project_task(task: Any, export_id: str) -> None:
    """Delegate one immutable export request to the application orchestration service."""
    from app.services.processing import perform_export

    perform_export(export_id, celery_task_id=str(task.request.id))


@worker_ready.connect  # type: ignore[untyped-decorator]
def reconcile_after_worker_ready(**_: object) -> None:
    """Reconcile stale durable work after the worker control channel is responsive."""
    from app.services.reconciliation import reconcile_interrupted_work

    try:
        reconcile_interrupted_work(settings)
    except Exception as error:
        logger.error("Worker startup reconciliation failed (%s).", type(error).__name__)
