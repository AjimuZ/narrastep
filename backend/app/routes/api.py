"""Version 1 HTTP routes for the complete local Narrastep workflow."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from fastapi.responses import FileResponse, StreamingResponse
from redis import Redis
from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.client import PubSub
from redis.exceptions import RedisError
from sqlalchemy import desc, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import __version__
from app.config import Settings, get_settings
from app.database import SessionLocal, get_session
from app.errors import ApiError, error_response
from app.models import (
    ExportStatus,
    JobStatus,
    MediaAsset,
    ProcessingJob,
    Project,
    ProjectExport,
    ProjectState,
    Screenshot,
    SegmentScreenshotAssociation,
    TranscriptSegment,
    new_uuid,
    utc_now,
)
from app.schemas import (
    ErrorEnvelope,
    ExportCreate,
    ExportEstimateResponse,
    ExportListResponse,
    ExportResponse,
    HealthResponse,
    JobResponse,
    MediaAssetResponse,
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectUpdate,
    ScreenshotAssociationResponse,
    ScreenshotFromVideoRequest,
    ScreenshotResponse,
    SegmentMergeRequest,
    SegmentResponse,
    SegmentSplitRequest,
    SegmentUpdate,
    serialize_utc,
)
from app.services.exporting import ExportRequest, estimate_export_package
from app.services.media import MediaServiceError, validate_video
from app.services.pipeline import (
    PipelineServiceError,
    calculate_exposure_penalty,
    calculate_laplacian_sharpness,
    compute_perceptual_hash,
)
from app.services.processing import export_segment_snapshot
from app.services.progress import publish_cancellation_flag, publish_job_event
from app.services.reviewing import (
    mark_project_review_changed,
    merge_segments,
    segment_response_data,
    select_primary_screenshot,
    split_segment,
)
from app.services.screenshot_materialization import (
    decode_exact_frame,
    ensure_thumbnail,
    materialize_png,
    write_png_atomic,
    write_thumbnail_atomic,
)
from app.services.uploads import stream_single_video_upload
from app.services.workspaces import (
    acquire_project_operation,
    create_project_workspace,
    ensure_within_root,
    purge_staged_workspace,
    release_project_operation,
    restore_staged_workspace,
    stage_project_workspace_removal,
)

router = APIRouter(
    prefix="/api/v1",
    responses={422: {"model": ErrorEnvelope, "description": "Stable validation error"}},
)
ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLING)
logger = logging.getLogger(__name__)


@router.get("/health", response_model=HealthResponse)
def health(
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    """Verify that the API process and authoritative SQLite store are available."""
    session.execute(text("SELECT 1"))
    redis_status = "ok" if _redis_available(settings, timeout_seconds=0.25) else "unavailable"
    worker_status = (
        "ok" if redis_status == "ok" and _worker_available(timeout_seconds=0.25) else "unavailable"
    )
    return {
        "status": "ok" if redis_status == worker_status == "ok" else "degraded",
        "version": __version__,
        "database": "ok",
        "redis": redis_status,
        "worker": worker_status,
    }


@router.get("/projects", response_model=ProjectListResponse)
def list_projects(session: Session = Depends(get_session)) -> dict[str, object]:
    """List all projects newest first for the local dashboard."""
    projects = list(session.scalars(select(Project).order_by(desc(Project.updated_at))))
    return {
        "items": [_project_payload(session, project) for project in projects],
        "total": len(projects),
    }


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    request: ProjectCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Create project metadata and its isolated workspace as one recoverable operation."""
    project = Project(
        name=request.name,
        description=request.description,
        source_language=request.source_language,
        output_mode=request.mode,
    )
    session.add(project)
    session.flush()
    workspace = create_project_workspace(settings.data_root, project.id)
    try:
        session.commit()
    except Exception:
        session.rollback()
        shutil.rmtree(workspace, ignore_errors=True)
        raise
    session.refresh(project)
    return _project_payload(session, project)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    """Load one persisted project so feature routes recover correctly after refresh."""
    return _project_payload(session, _require_project(session, project_id))


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    request: ProjectUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Apply only supplied editable metadata while preserving source evidence."""
    project = _require_project(session, project_id)
    reservation = acquire_project_operation(settings.data_root, project.id, "update")
    try:
        session.refresh(project)
        _reject_busy_project(project)
        updates = request.model_dump(exclude_unset=True)
        requested_language = updates.get("source_language")
        if (
            requested_language is not None
            and requested_language != project.source_language
            and project.state
            not in {
                ProjectState.DRAFT,
                ProjectState.UPLOADED,
                ProjectState.FAILED,
            }
        ):
            raise ApiError(
                "INVALID_PROJECT_STATE",
                "Source language can only change before processing or before a retry.",
                409,
            )
        if "mode" in updates:
            project.output_mode = updates.pop("mode")
        for field, value in updates.items():
            setattr(project, field, value)
        mark_project_review_changed(project)
        session.commit()
        session.refresh(project)
        return _project_payload(session, project)
    finally:
        release_project_operation(reservation)


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Stage project files before database deletion so failures remain recoverable."""
    project = _require_project(session, project_id)
    reservation = acquire_project_operation(settings.data_root, project.id, "delete")
    try:
        session.refresh(project)
        _reject_busy_project(project)
        staged = stage_project_workspace_removal(settings.data_root, project.id)
        try:
            session.delete(project)
            session.commit()
        except Exception:
            session.rollback()
            restore_staged_workspace(settings.data_root, project.id, staged)
            raise
        try:
            purge_staged_workspace(staged)
        except OSError as error:
            # The committed database deletion is authoritative; startup retries this purge.
            logger.warning(
                "Staged workspace purge will be retried for project %s (%s).",
                project.id,
                type(error).__name__,
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        release_project_operation(reservation)


@router.post(
    "/projects/{project_id}/video",
    response_model=MediaAssetResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {"file": {"type": "string", "format": "binary"}},
                    }
                }
            },
        }
    },
)
async def upload_video(
    project_id: str,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Stream multipart bytes directly to project storage and promote one valid video."""
    project = _require_project(session, project_id)
    _reject_busy_project(project)
    reservation = acquire_project_operation(settings.data_root, project.id, "upload")
    uploaded = None
    destination: Path | None = None
    promoted = False
    try:
        session.expire(project, ["media_asset"])
        if project.media_asset is not None:
            raise ApiError(
                "SOURCE_VIDEO_ALREADY_EXISTS", "This project already has a source video.", 409
            )
        workspace = reservation.workspace_path
        uploaded = await stream_single_video_upload(
            request, workspace / "source", reservation.token, settings
        )
        stored_filename = f"source{uploaded.extension}"
        destination = workspace / "source" / stored_filename
        try:
            metadata = validate_video(uploaded.partial_path, config=settings)
        except MediaServiceError as error:
            raise _media_api_error(error) from error
        os.replace(uploaded.partial_path, destination)
        promoted = True
        asset = MediaAsset(
            project_id=project.id,
            display_filename=uploaded.display_filename,
            stored_filename=stored_filename,
            relative_path=destination.relative_to(settings.data_root).as_posix(),
            content_type=metadata.content_type,
            size_bytes=uploaded.size_bytes,
            duration_seconds=metadata.duration_seconds,
            width=metadata.width,
            height=metadata.height,
            container_format=metadata.format_name,
            metadata_json=metadata.to_dict(),
        )
        project.state = ProjectState.UPLOADED
        session.add(asset)
        session.commit()
        session.refresh(asset)
        return _media_payload(asset)
    except Exception:
        session.rollback()
        if promoted and destination is not None:
            destination.unlink(missing_ok=True)
        raise
    finally:
        if uploaded is not None:
            uploaded.partial_path.unlink(missing_ok=True)
        release_project_operation(reservation)


@router.get(
    "/projects/{project_id}/video",
    response_class=Response,
    responses={
        200: {
            "description": "Complete source video",
            "headers": {"Accept-Ranges": {"schema": {"type": "string"}}},
            "content": {
                media_type: {"schema": {"type": "string", "format": "binary"}}
                for media_type in ("video/mp4", "video/webm", "video/quicktime", "video/x-matroska")
            },
        },
        206: {
            "description": "Single byte range",
            "headers": {
                "Accept-Ranges": {"schema": {"type": "string"}},
                "Content-Range": {"schema": {"type": "string"}},
                "Content-Length": {"schema": {"type": "string"}},
            },
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        },
        416: {
            "model": ErrorEnvelope,
            "description": "Unsatisfiable byte range",
            "headers": {"Content-Range": {"schema": {"type": "string"}}},
        },
    },
)
def stream_video(
    project_id: str,
    request: Request,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Serve the validated source with single-byte-range support for native seeking."""
    project = _require_project(session, project_id)
    asset = project.media_asset
    if asset is None:
        raise ApiError("PROJECT_ASSET_MISSING", "The project source video is unavailable.", 404)
    path = ensure_within_root(settings.data_root, asset.relative_path)
    if not path.is_file():
        raise ApiError("PROJECT_ASSET_MISSING", "The project source video is unavailable.", 404)
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    if not range_header:
        return FileResponse(
            path,
            media_type=asset.content_type,
            filename=asset.display_filename,
            headers={"Accept-Ranges": "bytes", "Content-Disposition": "inline"},
        )
    try:
        start, end = _parse_byte_range(range_header, file_size)
    except ApiError as error:
        response = error_response(error)
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Content-Range"] = f"bytes */{file_size}"
        return response
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
        "Content-Disposition": "inline",
    }
    return StreamingResponse(
        _file_range(path, start, length),
        status_code=status.HTTP_206_PARTIAL_CONTENT,
        media_type=asset.content_type,
        headers=headers,
    )


@router.post(
    "/projects/{project_id}/process",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_processing(
    project_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Persist a queued processing job before handing it to the local Celery worker."""
    project = _require_project(session, project_id)
    reservation = acquire_project_operation(settings.data_root, project.id, "process")
    try:
        session.refresh(project)
        return _queue_processing_job(session, project, settings)
    finally:
        release_project_operation(reservation)


def _queue_processing_job(
    session: Session, project: Project, settings: Settings
) -> dict[str, object]:
    """Validate, reserve, persist, and dispatch one processing attempt."""
    if project.media_asset is None:
        raise ApiError("PROJECT_ASSET_MISSING", "Upload a valid source video first.", 409)
    if _active_job(session, project.id) is not None:
        raise ApiError("PROJECT_BUSY", "This project already has active processing work.", 409)
    if project.state not in {ProjectState.UPLOADED, ProjectState.FAILED}:
        raise ApiError(
            "INVALID_PROJECT_STATE", "This project cannot be processed in its current state.", 409
        )
    _ensure_worker_available(settings)
    transition = session.execute(
        update(Project)
        .where(
            Project.id == project.id,
            Project.state.in_((ProjectState.UPLOADED, ProjectState.FAILED)),
        )
        .values(state=ProjectState.QUEUED, updated_at=utc_now())
    )
    if getattr(transition, "rowcount", 0) != 1:
        session.rollback()
        session.refresh(project)
        if project.state in {
            ProjectState.QUEUED,
            ProjectState.PROCESSING,
            ProjectState.EXPORTING,
        }:
            raise ApiError("PROJECT_BUSY", "The project has active background work.", 409)
        raise ApiError(
            "INVALID_PROJECT_STATE", "This project cannot be processed in its current state.", 409
        )
    task_id = str(uuid.uuid4())
    effective_ocr_mode = (
        settings.ocr_mode if settings.screenshot_selection_strategy == "adaptive_v1" else "off"
    )
    job = ProcessingJob(
        project_id=project.id,
        celery_task_id=task_id,
        configuration_snapshot={
            "algorithmVersion": (
                "adaptive_v1.0"
                if settings.screenshot_selection_strategy == "adaptive_v1"
                else "legacy_v1"
            ),
            "selectionStrategy": settings.screenshot_selection_strategy,
            "ocrMode": effective_ocr_mode,
            "imageBudgetProfile": settings.image_budget_profile,
            "tesseractPath": settings.tesseract_path,
            "screenshotAlternatives": settings.screenshot_alternatives,
            "projectMode": project.output_mode.value,
            "directDuplicateThreshold": settings.adaptive_direct_duplicate_threshold,
            "borderlineDuplicateThreshold": settings.adaptive_borderline_duplicate_threshold,
            "borderlineSsimThreshold": settings.adaptive_borderline_ssim_threshold,
            "ocrTokenJaccardThreshold": settings.adaptive_ocr_token_jaccard_threshold,
        },
    )
    session.add(job)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ApiError(
            "PROJECT_BUSY", "This project already has active processing work.", 409
        ) from error
    session.refresh(job)
    try:
        from app.tasks.celery_app import process_project_task

        process_project_task.apply_async(args=(job.id,), task_id=task_id)
    except Exception as error:
        session.expire_all()
        current_job = _require_job(session, job.id)
        if current_job.status == JobStatus.QUEUED:
            current_job.status = JobStatus.FAILED
            current_job.stage = "failed"
            current_job.message = "The local worker is unavailable."
            current_job.error_code = "WORKER_UNAVAILABLE"
            current_job.error_message = "The local processing worker is unavailable."
            current_job.completed_at = utc_now()
            current_job.project.state = ProjectState.FAILED
            session.commit()
        else:
            return _job_payload(current_job)
        raise ApiError(
            "WORKER_UNAVAILABLE", "The local processing worker is unavailable.", 503
        ) from error
    session.expire_all()
    return _job_payload(_require_job(session, job.id))


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    """Return the latest authoritative processing snapshot for polling clients."""
    return _job_payload(_require_job(session, job_id))


@router.get(
    "/jobs/{job_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Versioned progress event stream",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        }
    },
)
async def stream_job_events(
    job_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Stream database-resynchronized progress with event IDs and bounded heartbeats."""
    with SessionLocal() as session:
        _require_job(session, job_id)
    try:
        requested_version = int(last_event_id) if last_event_id is not None else -1
    except ValueError:
        requested_version = -1
    return StreamingResponse(
        _job_event_stream(job_id, requested_version, settings),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def cancel_job(job_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    """Persist cooperative cancellation and revoke work that has not started yet."""
    job = _require_job(session, job_id)
    if job.status not in ACTIVE_JOB_STATUSES:
        return _job_payload(job)
    job.cancel_requested = True
    job.progress_version += 1
    project = job.project
    if job.status == JobStatus.QUEUED:
        if job.celery_task_id:
            from app.tasks.celery_app import celery_app

            try:
                celery_app.control.revoke(job.celery_task_id, terminate=False)
            except Exception:
                # The durable flag remains authoritative when Redis is temporarily unavailable.
                logger.warning("Queued task revoke could not be published for job %s.", job.id)
        job.status = JobStatus.CANCELLED
        job.stage = "cancelled"
        job.message = "Processing was cancelled before it started."
        job.completed_at = utc_now()
        project.state = ProjectState.UPLOADED
    else:
        job.status = JobStatus.CANCELLING
        job.message = "Waiting for the current processing unit to stop safely."
    session.commit()
    session.refresh(job)
    if job.status == JobStatus.CANCELLING:
        publish_cancellation_flag(job.id)
    publish_job_event(job)
    return _job_payload(job)


@router.get("/projects/{project_id}/segments", response_model=list[SegmentResponse])
def list_segments(
    project_id: str, session: Session = Depends(get_session)
) -> list[dict[str, object]]:
    """Return active transcript segments in stable review order."""
    _require_project(session, project_id)
    segments = list(
        session.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.project_id == project_id, TranscriptSegment.active.is_(True))
            .order_by(TranscriptSegment.sequence)
        )
    )
    return [segment_response_data(segment) for segment in segments]


@router.get("/segments/{segment_id}", response_model=SegmentResponse)
def get_segment(segment_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    """Load one transcript segment for direct review links and API clients."""
    return segment_response_data(_require_segment(session, segment_id))


@router.patch("/segments/{segment_id}", response_model=SegmentResponse)
def update_segment(
    segment_id: str,
    request: SegmentUpdate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Save reviewed text explicitly while never modifying raw transcript evidence."""
    segment = _require_segment(session, segment_id)
    reservation = acquire_project_operation(settings.data_root, segment.project_id, "review")
    try:
        session.refresh(segment)
        if not segment.active:
            raise ApiError(
                "INVALID_SEGMENT_OPERATION", "Only an active segment can be edited.", 409
            )
        _reject_busy_project(segment.project)
        segment.reviewed_text = request.reviewed_text
        mark_project_review_changed(segment.project)
        session.commit()
        session.refresh(segment)
        return segment_response_data(segment)
    finally:
        release_project_operation(reservation)


@router.post("/segments/{segment_id}/split", response_model=list[SegmentResponse])
def split_review_segment(
    segment_id: str,
    request: SegmentSplitRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> list[dict[str, object]]:
    """Apply an exact reviewed-text and timestamp split in one transaction."""
    segment = _require_segment(session, segment_id)
    reservation = acquire_project_operation(settings.data_root, segment.project_id, "review")
    try:
        session.refresh(segment)
        left, right = split_segment(session, segment, request)
        return [segment_response_data(left), segment_response_data(right)]
    finally:
        release_project_operation(reservation)


@router.post("/segments/merge", response_model=SegmentResponse)
def merge_review_segments(
    request: SegmentMergeRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Merge an ordered contiguous selection and retain source lineage."""
    first_segment = _require_segment(session, request.segment_ids[0])
    reservation = acquire_project_operation(settings.data_root, first_segment.project_id, "review")
    try:
        session.expire_all()
        return segment_response_data(merge_segments(session, request))
    finally:
        release_project_operation(reservation)


@router.get(
    "/segments/{segment_id}/screenshots", response_model=list[ScreenshotAssociationResponse]
)
def list_segment_screenshots(
    segment_id: str, session: Session = Depends(get_session)
) -> list[dict[str, object]]:
    """Return ranked screenshot associations for one active review segment."""
    segment = _require_segment(session, segment_id)
    return [
        _association_payload(item)
        for item in sorted(
            segment.screenshot_associations, key=lambda association: association.rank
        )
    ]


@router.get("/projects/{project_id}/screenshots", response_model=list[ScreenshotResponse])
def list_screenshots(
    project_id: str, session: Session = Depends(get_session)
) -> list[dict[str, object]]:
    """Return all generated project screenshots for review diagnostics."""
    _require_project(session, project_id)
    screenshots = list(
        session.scalars(
            select(Screenshot)
            .where(Screenshot.project_id == project_id)
            .order_by(Screenshot.timestamp_seconds)
        )
    )
    return [_screenshot_payload(item) for item in screenshots]


@router.put(
    "/segments/{segment_id}/primary-screenshot/{screenshot_id}",
    response_model=ScreenshotAssociationResponse,
)
def set_primary_screenshot(
    segment_id: str,
    screenshot_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Select one same-project candidate as the segment's explicit primary evidence."""
    segment = _require_segment(session, segment_id)
    reservation = acquire_project_operation(settings.data_root, segment.project_id, "review")
    try:
        session.refresh(segment)
        screenshot = session.get(Screenshot, screenshot_id)
        if screenshot is None or screenshot.project_id != segment.project_id:
            raise ApiError(
                "INVALID_SCREENSHOT_ASSOCIATION",
                "The screenshot is not available for this project.",
                422,
            )
        try:
            materialize_png(session, screenshot, settings)
        except PipelineServiceError as error:
            raise ApiError(error.code, error.message, 404) from error
        segment = select_primary_screenshot(session, segment, screenshot_id)
        association = next(
            item for item in segment.screenshot_associations if item.screenshot_id == screenshot_id
        )
        return _association_payload(association)
    finally:
        release_project_operation(reservation)


@router.post(
    "/segments/{segment_id}/screenshot-from-video",
    response_model=ScreenshotAssociationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_screenshot_from_video(
    segment_id: str,
    request: ScreenshotFromVideoRequest,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Create and select one exact project-owned source frame for a review segment."""
    segment = _require_segment(session, segment_id)
    reservation = acquire_project_operation(settings.data_root, segment.project_id, "review")
    full_path: Path | None = None
    thumbnail_path: Path | None = None
    committed = False
    try:
        session.refresh(segment)
        project = segment.project
        asset = project.media_asset
        if asset is None:
            raise ApiError("PROJECT_ASSET_MISSING", "The source video is unavailable.", 404)
        if asset.duration_seconds is None:
            raise ApiError(
                "SOURCE_DURATION_UNAVAILABLE",
                "The source video duration is unavailable.",
                422,
            )
        if request.timestamp_seconds > asset.duration_seconds:
            raise ApiError(
                "INVALID_SCREENSHOT_TIMESTAMP",
                "The screenshot timestamp must be inside the source video.",
                422,
            )
        source_path = ensure_within_root(settings.data_root, asset.relative_path)
        try:
            frame = decode_exact_frame(source_path, request.timestamp_seconds)
        except PipelineServiceError as error:
            raise ApiError(error.code, error.message, 422) from error
        screenshot_id = new_uuid()
        workspace = create_project_workspace(settings.data_root, project.id)
        full_path = workspace / "screenshots" / "full" / f"{screenshot_id}.png"
        thumbnail_path = workspace / "screenshots" / "thumbnails" / f"{screenshot_id}.webp"
        write_png_atomic(frame, full_path)
        write_thumbnail_atomic(frame, thumbnail_path)
        height, width = frame.shape[:2]
        sharpness = calculate_laplacian_sharpness(frame)
        exposure_penalty = calculate_exposure_penalty(frame)
        quality = sharpness / (sharpness + 100.0) * 0.6 + (1 - exposure_penalty) * 0.4
        screenshot = Screenshot(
            id=screenshot_id,
            project_id=project.id,
            relative_path=full_path.relative_to(settings.data_root).as_posix(),
            thumbnail_relative_path=thumbnail_path.relative_to(settings.data_root).as_posix(),
            materialization_state="png_ready",
            selection_reason="manual_recovery",
            cluster_id=f"manual-{screenshot_id}",
            is_mandatory=False,
            timestamp_seconds=request.timestamp_seconds,
            perceptual_hash=compute_perceptual_hash(frame),
            quality_score=quality,
            width=width,
            height=height,
        )
        session.add(screenshot)
        session.flush()
        updated_segment = select_primary_screenshot(session, segment, screenshot.id)
        association = next(
            item
            for item in updated_segment.screenshot_associations
            if item.screenshot_id == screenshot.id
        )
        association.coverage_score = 1.0
        association.role_rank_reason = "manual_recovery"
        session.commit()
        committed = True
        session.refresh(association)
        return _association_payload(association)
    except Exception:
        session.rollback()
        if full_path is not None and not committed:
            full_path.unlink(missing_ok=True)
        if thumbnail_path is not None and not committed:
            thumbnail_path.unlink(missing_ok=True)
        raise
    finally:
        release_project_operation(reservation)


@router.delete("/segments/{segment_id}/primary-screenshot", status_code=status.HTTP_204_NO_CONTENT)
def remove_primary_screenshot(
    segment_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Clear the primary role while retaining generated candidates for later selection."""
    segment = _require_segment(session, segment_id)
    reservation = acquire_project_operation(settings.data_root, segment.project_id, "review")
    try:
        session.refresh(segment)
        select_primary_screenshot(session, segment, None)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        release_project_operation(reservation)


@router.get(
    "/screenshots/{screenshot_id}/thumbnail",
    response_class=Response,
    responses={
        200: {
            "description": "Inline WebP screenshot thumbnail",
            "content": {"image/webp": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def screenshot_thumbnail(
    screenshot_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Serve or lazily create one lightweight project-owned review thumbnail."""
    screenshot = session.get(Screenshot, screenshot_id)
    if screenshot is None:
        raise ApiError("PROJECT_ASSET_MISSING", "The screenshot is unavailable.", 404)
    try:
        path = ensure_thumbnail(session, screenshot, settings)
        session.commit()
    except PipelineServiceError as error:
        session.rollback()
        raise ApiError(error.code, error.message, 404) from error
    return FileResponse(
        path,
        media_type="image/webp",
        filename=f"screenshot-{screenshot.id}.webp",
        content_disposition_type="inline",
    )


@router.get(
    "/screenshots/{screenshot_id}/content",
    response_class=Response,
    responses={
        200: {
            "description": "Inline PNG screenshot",
            "content": {"image/png": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def screenshot_content(
    screenshot_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Serve one project-owned screenshot from safe local storage."""
    screenshot = session.get(Screenshot, screenshot_id)
    if screenshot is None:
        raise ApiError("PROJECT_ASSET_MISSING", "The screenshot is unavailable.", 404)
    try:
        path = materialize_png(session, screenshot, settings)
        session.commit()
    except PipelineServiceError as error:
        session.rollback()
        raise ApiError(error.code, error.message, 404) from error
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"screenshot-{screenshot.id}.png",
        content_disposition_type="inline",
    )


@router.get(
    "/projects/{project_id}/export-estimate",
    response_model=ExportEstimateResponse,
)
def export_estimate(
    project_id: str,
    include_original_video: bool = Query(False, alias="includeOriginalVideo"),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Estimate immutable export size and image reuse through the real selector."""
    project = _require_project(session, project_id)
    asset = project.media_asset
    if asset is None:
        raise ApiError("PROJECT_ASSET_MISSING", "The source video is unavailable.", 404)
    segments = list(
        session.scalars(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.project_id == project.id,
                TranscriptSegment.active.is_(True),
            )
            .order_by(TranscriptSegment.sequence)
        )
    )
    latest_job = session.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.project_id == project.id,
            ProcessingJob.status == JobStatus.COMPLETED,
        )
        .order_by(desc(ProcessingJob.created_at))
    )
    snapshot = latest_job.configuration_snapshot if latest_job else {}
    workspace = create_project_workspace(settings.data_root, project.id)
    source_path = ensure_within_root(settings.data_root, asset.relative_path)
    estimate = estimate_export_package(
        ExportRequest(
            export_id="estimate",
            project={"id": project.id},
            source={"durationSeconds": asset.duration_seconds},
            segments=[export_segment_snapshot(settings, segment) for segment in segments],
            raw_transcript_path=workspace / "transcript" / "raw-transcript.json",
            destination_path=workspace / "exports" / ".estimate.zip",
            output_mode=project.output_mode.value,
            original_video_path=source_path,
            include_original_video=include_original_video,
            chatgpt_max_files_per_batch=settings.chatgpt_max_files_per_batch,
            duplicate_similarity_threshold=settings.duplicate_similarity_threshold,
            selection_strategy=str(snapshot.get("selectionStrategy", "legacy")),
            image_budget_profile=str(
                snapshot.get("imageBudgetProfile", settings.image_budget_profile)
            ),
            duration_seconds=asset.duration_seconds,
        )
    )
    return {
        "canonicalImageCount": estimate.canonical_image_count,
        "batchCount": estimate.batch_count,
        "batchImageCopyCount": estimate.batch_image_copy_count,
        "estimatedZipBytes": estimate.estimated_zip_bytes,
        "isApproximate": True,
        "redundancyWarning": estimate.redundancy_warning,
    }


@router.post(
    "/projects/{project_id}/exports",
    response_model=ExportResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_export(
    project_id: str,
    request: ExportCreate,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    """Persist an immutable export request before queueing local ZIP generation."""
    project = _require_project(session, project_id)
    reservation = acquire_project_operation(settings.data_root, project.id, "export")
    try:
        session.refresh(project)
        return _queue_export(session, project, request, settings)
    finally:
        release_project_operation(reservation)


def _queue_export(
    session: Session,
    project: Project,
    request: ExportCreate,
    settings: Settings,
) -> dict[str, object]:
    """Validate, reserve, persist, and dispatch one immutable export request."""
    if project.state not in {ProjectState.REVIEW, ProjectState.COMPLETED}:
        raise ApiError("INVALID_PROJECT_STATE", "Complete processing before exporting.", 409)
    active_export = session.scalar(
        select(ProjectExport).where(
            ProjectExport.project_id == project.id,
            ProjectExport.status.in_((ExportStatus.QUEUED, ExportStatus.RUNNING)),
        )
    )
    if active_export is not None:
        raise ApiError("PROJECT_BUSY", "This project already has an active export.", 409)
    _ensure_worker_available(settings)
    transition = session.execute(
        update(Project)
        .where(
            Project.id == project.id,
            Project.state.in_((ProjectState.REVIEW, ProjectState.COMPLETED)),
        )
        .values(state=ProjectState.EXPORTING, updated_at=utc_now())
    )
    if getattr(transition, "rowcount", 0) != 1:
        session.rollback()
        session.refresh(project)
        if project.state == ProjectState.EXPORTING:
            raise ApiError("PROJECT_BUSY", "This project already has an active export.", 409)
        raise ApiError("INVALID_PROJECT_STATE", "Complete processing before exporting.", 409)
    task_id = str(uuid.uuid4())
    export = ProjectExport(
        project_id=project.id,
        include_original_video=request.include_original_video,
        celery_task_id=task_id,
    )
    session.add(export)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ApiError("PROJECT_BUSY", "This project already has an active export.", 409) from error
    session.refresh(export)
    try:
        from app.tasks.celery_app import export_project_task

        export_project_task.apply_async(args=(export.id,), task_id=task_id)
    except Exception as error:
        session.expire_all()
        current_export = _require_export(session, export.id)
        if current_export.status == ExportStatus.QUEUED:
            current_export.status = ExportStatus.FAILED
            current_export.current_stage = "failed"
            current_export.error_code = "WORKER_UNAVAILABLE"
            current_export.error_message = "The local export worker is unavailable."
            current_export.completed_at = utc_now()
            current_export.project.state = ProjectState.REVIEW
            session.commit()
        else:
            return _export_payload(current_export)
        raise ApiError(
            "WORKER_UNAVAILABLE", "The local export worker is unavailable.", 503
        ) from error
    session.expire_all()
    return _export_payload(_require_export(session, export.id))


@router.get("/projects/{project_id}/exports", response_model=ExportListResponse)
def list_exports(project_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    """Return immutable export history newest first for progress and download UI."""
    _require_project(session, project_id)
    exports = list(
        session.scalars(
            select(ProjectExport)
            .where(ProjectExport.project_id == project_id)
            .order_by(desc(ProjectExport.created_at))
        )
    )
    return {"items": [_export_payload(item) for item in exports], "total": len(exports)}


@router.get("/exports/{export_id}", response_model=ExportResponse)
def get_export(export_id: str, session: Session = Depends(get_session)) -> dict[str, object]:
    """Return one export progress record for direct polling clients."""
    return _export_payload(_require_export(session, export_id))


@router.get(
    "/exports/{export_id}/download",
    response_class=Response,
    responses={
        200: {
            "description": "Immutable export archive",
            "content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}},
        }
    },
)
def download_export(
    export_id: str,
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Download a completed immutable ZIP while rejecting partial or missing artifacts."""
    export = _require_export(session, export_id)
    if export.status != ExportStatus.COMPLETED or not export.relative_path:
        raise ApiError("EXPORT_NOT_READY", "The export is not ready for download.", 409)
    path = ensure_within_root(settings.data_root, export.relative_path)
    if not path.is_file():
        raise ApiError("PROJECT_ASSET_MISSING", "The export file is unavailable.", 404)
    return FileResponse(path, media_type="application/zip", filename=f"narrastep-{export.id}.zip")


def _require_project(session: Session, project_id: str) -> Project:
    """Load a project or raise the stable not-found business error."""
    project = session.get(Project, project_id)
    if project is None:
        raise ApiError("PROJECT_NOT_FOUND", "The project was not found.", 404)
    return project


def _require_job(session: Session, job_id: str) -> ProcessingJob:
    """Load a processing job or raise a stable not-found error."""
    job = session.get(ProcessingJob, job_id)
    if job is None:
        raise ApiError("JOB_NOT_FOUND", "The processing job was not found.", 404)
    return job


def _require_segment(session: Session, segment_id: str) -> TranscriptSegment:
    """Load a transcript segment or raise a stable not-found error."""
    segment = session.get(TranscriptSegment, segment_id)
    if segment is None:
        raise ApiError("SEGMENT_NOT_FOUND", "The transcript segment was not found.", 404)
    return segment


def _require_export(session: Session, export_id: str) -> ProjectExport:
    """Load an export record or raise a stable not-found error."""
    export = session.get(ProjectExport, export_id)
    if export is None:
        raise ApiError("EXPORT_NOT_FOUND", "The export was not found.", 404)
    return export


def _reject_busy_project(project: Project) -> None:
    """Prevent destructive or conflicting changes during active background work."""
    if project.state in {ProjectState.QUEUED, ProjectState.PROCESSING, ProjectState.EXPORTING}:
        raise ApiError("PROJECT_BUSY", "The project has active background work.", 409)


def _active_job(session: Session, project_id: str) -> ProcessingJob | None:
    """Return the newest active processing job for project response and conflict checks."""
    return session.scalar(
        select(ProcessingJob)
        .where(
            ProcessingJob.project_id == project_id,
            ProcessingJob.status.in_(ACTIVE_JOB_STATUSES),
        )
        .order_by(desc(ProcessingJob.created_at))
    )


def _latest_job(session: Session, project_id: str) -> ProcessingJob | None:
    """Return the newest processing attempt for terminal failure recovery views."""
    return session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.project_id == project_id)
        .order_by(desc(ProcessingJob.created_at))
    )


def _project_payload(session: Session, project: Project) -> dict[str, object]:
    """Map internal project naming to the stable frontend-facing contract."""
    active_job = _active_job(session, project.id)
    visible_job = (
        active_job
        if active_job is not None or project.state != ProjectState.FAILED
        else _latest_job(session, project.id)
    )
    asset = project.media_asset
    active_segment_count = session.scalar(
        select(func.count())
        .select_from(TranscriptSegment)
        .where(
            TranscriptSegment.project_id == project.id,
            TranscriptSegment.active.is_(True),
        )
    )
    screenshot_count = session.scalar(
        select(func.count()).select_from(Screenshot).where(Screenshot.project_id == project.id)
    )
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "mode": project.output_mode.value,
        "sourceLanguage": project.source_language,
        "detectedLanguage": project.detected_language,
        "status": project.state.value,
        "createdAt": project.created_at,
        "updatedAt": project.updated_at,
        "videoAssetId": asset.id if asset else None,
        "activeJobId": visible_job.id if visible_job else None,
        "videoAsset": _media_payload(asset) if asset else None,
        "activeSegmentCount": active_segment_count or 0,
        "screenshotCount": screenshot_count or 0,
    }


def _media_payload(asset: MediaAsset) -> dict[str, object]:
    """Map stored media metadata without exposing local filenames or paths."""
    return {
        "id": asset.id,
        "projectId": asset.project_id,
        "assetType": "video",
        "originalFilename": asset.display_filename,
        "mimeType": asset.content_type,
        "sizeBytes": asset.size_bytes,
        "durationSeconds": asset.duration_seconds,
        "width": asset.width,
        "height": asset.height,
        "createdAt": asset.created_at,
    }


def _job_payload(job: ProcessingJob) -> dict[str, object]:
    """Map durable progress fields to the browser polling and SSE contract."""
    snapshot = job.configuration_snapshot or {}
    return {
        "id": job.id,
        "projectId": job.project_id,
        "status": job.status.value,
        "currentStage": job.stage,
        "progressPercent": job.progress_percent,
        "message": job.message,
        "errorCode": job.error_code,
        "errorMessage": job.error_message,
        "startedAt": serialize_utc(job.started_at) if job.started_at else None,
        "completedAt": serialize_utc(job.completed_at) if job.completed_at else None,
        "createdAt": serialize_utc(job.created_at),
        "version": job.progress_version,
        "selectionStrategy": snapshot.get("selectionStrategy", "legacy"),
        "ocrMode": snapshot.get("ocrMode", "off"),
        "imageBudgetProfile": snapshot.get("imageBudgetProfile", "balanced"),
        "metrics": job.metrics_summary or {},
    }


def _screenshot_payload(
    screenshot: Screenshot,
    association: SegmentScreenshotAssociation | None = None,
) -> dict[str, object]:
    """Map screenshot metadata with optional segment-specific selection details."""
    return {
        "id": screenshot.id,
        "projectId": screenshot.project_id,
        "timestampSeconds": screenshot.timestamp_seconds,
        "width": screenshot.width,
        "height": screenshot.height,
        "qualityScore": screenshot.quality_score,
        "isCandidate": True,
        "role": "primary" if association and association.is_primary else "alternative",
        "confidence": association.score if association else None,
        "isUserSelected": association.user_selected if association else False,
        "selectionReason": screenshot.selection_reason,
        "visualChangeScore": screenshot.visual_change_score,
        "textChangeScore": screenshot.text_change_score,
        "clusterId": screenshot.cluster_id,
        "isMandatory": screenshot.is_mandatory,
        "materializationState": screenshot.materialization_state,
        "sharedSegmentCount": sum(
            1 for item in screenshot.segment_associations if item.segment.active
        ),
        "createdAt": screenshot.created_at,
    }


def _association_payload(association: SegmentScreenshotAssociation) -> dict[str, object]:
    """Embed screenshot metadata in its segment-specific association contract."""
    return {
        "id": association.id,
        "segmentId": association.segment_id,
        "screenshotId": association.screenshot_id,
        "role": "primary" if association.is_primary else "alternative",
        "confidence": association.score,
        "coverageScore": association.coverage_score,
        "roleRankReason": association.role_rank_reason,
        "isUserSelected": association.user_selected,
        "screenshot": _screenshot_payload(association.screenshot, association),
        "createdAt": association.created_at,
    }


def _export_payload(export: ProjectExport) -> dict[str, object]:
    """Map export persistence to the frontend history and progress contract."""
    return {
        "id": export.id,
        "projectId": export.project_id,
        "status": export.status.value,
        "includeOriginalVideo": export.include_original_video,
        "progressPercent": export.progress_percent,
        "currentStage": export.current_stage,
        "errorCode": export.error_code,
        "errorMessage": export.error_message,
        "sizeBytes": export.size_bytes,
        "createdAt": export.created_at,
        "completedAt": export.completed_at,
        "metrics": export.metrics_summary or {},
    }


def _media_api_error(error: MediaServiceError) -> ApiError:
    """Translate media-layer codes into stable HTTP status and public code choices."""
    mappings = {
        "AUDIO_STREAM_MISSING": ("VIDEO_AUDIO_TRACK_MISSING", 422),
        "VIDEO_STREAM_MISSING": ("VIDEO_VALIDATION_FAILED", 422),
        "INVALID_VIDEO": ("VIDEO_VALIDATION_FAILED", 422),
        "UNSUPPORTED_MEDIA_TYPE": ("UNSUPPORTED_MEDIA_TYPE", 415),
        "FFPROBE_FAILED": ("FFPROBE_FAILED", 422),
    }
    code, status_code = mappings.get(error.code, ("VIDEO_VALIDATION_FAILED", 422))
    return ApiError(code, error.message, status_code)


def _redis_available(settings: Settings, timeout_seconds: float) -> bool:
    """Check broker reachability within a small bounded health or dispatch budget."""
    try:
        client = Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
        )
        available = bool(client.ping())
        client.close()
        return available
    except RedisError:
        return False


def _worker_available(timeout_seconds: float) -> bool:
    """Return whether at least one Celery worker answers a bounded control ping."""
    try:
        from app.tasks.celery_app import celery_app

        replies = celery_app.control.inspect(timeout=timeout_seconds).ping()
        return bool(replies)
    except Exception:
        return False


def _ensure_worker_available(settings: Settings) -> None:
    """Reject queue requests before state changes when local processing is unavailable."""
    if settings.environment == "test" or settings.celery_always_eager:
        return
    if not _redis_available(settings, timeout_seconds=1.0):
        raise ApiError("REDIS_UNAVAILABLE", "The local processing broker is unavailable.", 503)
    if not _worker_available(timeout_seconds=1.0):
        raise ApiError("WORKER_UNAVAILABLE", "The local processing worker is unavailable.", 503)


def _parse_byte_range(value: str, file_size: int) -> tuple[int, int]:
    """Parse exactly one RFC-style byte range and reject unsatisfiable requests."""
    if not value.startswith("bytes=") or "," in value:
        raise ApiError("INVALID_BYTE_RANGE", "The requested byte range is not satisfiable.", 416)
    range_value = value[6:].strip()
    try:
        start_text, end_text = range_value.split("-", maxsplit=1)
        if not start_text:
            suffix_length = int(end_text)
            if suffix_length <= 0:
                raise ValueError
            start = max(0, file_size - suffix_length)
            end = file_size - 1
        else:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
    except (TypeError, ValueError):
        raise ApiError(
            "INVALID_BYTE_RANGE", "The requested byte range is not satisfiable.", 416
        ) from None
    if start < 0 or start >= file_size or end < start:
        raise ApiError("INVALID_BYTE_RANGE", "The requested byte range is not satisfiable.", 416)
    return start, min(end, file_size - 1)


def _file_range(path: Path, start: int, length: int) -> Generator[bytes, None, None]:
    """Yield only the requested portion of a local file in bounded chunks."""
    remaining = length
    with path.open("rb") as source:
        source.seek(start)
        while remaining > 0:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


async def _job_event_stream(
    job_id: str, last_version: int, settings: Settings
) -> AsyncGenerator[str, None]:
    """Use Redis wakeups plus periodic SQLite resync, versioned snapshots, and heartbeats."""
    delivered = last_version
    heartbeat_elapsed: float = float(settings.sse_heartbeat_seconds)
    first = True
    redis_resources: tuple[AsyncRedis, PubSub] | None = None
    redis_retry_allowed = True
    try:
        while True:
            with SessionLocal() as session:
                job = session.get(ProcessingJob, job_id)
                if job is None:
                    return
                terminal = job.status in {
                    JobStatus.COMPLETED,
                    JobStatus.FAILED,
                    JobStatus.CANCELLED,
                    JobStatus.INTERRUPTED,
                }
                if first or job.progress_version > delivered:
                    event_name = (
                        "snapshot"
                        if first
                        else (
                            "completed"
                            if job.status == JobStatus.COMPLETED
                            else "failed"
                            if terminal
                            else "progress"
                        )
                    )
                    payload = json.dumps(_job_payload(job), separators=(",", ":"))
                    yield f"id: {job.progress_version}\nevent: {event_name}\ndata: {payload}\n\n"
                    delivered = job.progress_version
                    heartbeat_elapsed = 0.0
                    first = False
                if terminal:
                    return

            if redis_resources is None and redis_retry_allowed:
                redis_resources = await _open_job_pubsub(settings, job_id)
                redis_retry_allowed = False
            fallback_seconds = max(2.0, settings.sse_poll_seconds)
            wait_seconds = min(
                fallback_seconds,
                max(0.01, settings.sse_heartbeat_seconds - heartbeat_elapsed),
            )
            started = asyncio.get_running_loop().time()
            if redis_resources is None:
                await asyncio.sleep(min(settings.sse_poll_seconds, wait_seconds))
            else:
                _, pubsub = redis_resources
                try:
                    await pubsub.get_message(ignore_subscribe_messages=True, timeout=wait_seconds)
                except RedisError:
                    await _close_job_pubsub(redis_resources)
                    redis_resources = None
            heartbeat_elapsed += asyncio.get_running_loop().time() - started
            if heartbeat_elapsed >= settings.sse_heartbeat_seconds:
                yield ": heartbeat\n\n"
                heartbeat_elapsed = 0.0
                redis_retry_allowed = True
    finally:
        if redis_resources is not None:
            await _close_job_pubsub(redis_resources)


async def _open_job_pubsub(settings: Settings, job_id: str) -> tuple[AsyncRedis, PubSub] | None:
    """Subscribe to best-effort progress wakeups without making Redis authoritative."""
    client = AsyncRedis.from_url(
        settings.redis_url,
        socket_connect_timeout=1,
        socket_timeout=1,
    )
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(f"job:{job_id}")
        return client, pubsub
    except RedisError:
        await pubsub.aclose()
        await client.aclose()
        return None


async def _close_job_pubsub(resources: tuple[AsyncRedis, PubSub]) -> None:
    """Close Redis stream resources safely after terminal state or transport failure."""
    client, pubsub = resources
    try:
        await pubsub.unsubscribe()
    except RedisError:
        pass
    await pubsub.aclose()  # type: ignore[no-untyped-call]
    await client.aclose()
