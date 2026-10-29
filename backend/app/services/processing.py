"""Durable orchestration for the local transcription, screenshot, and export jobs."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (
    ExportStatus,
    JobStatus,
    ProcessingJob,
    Project,
    ProjectExport,
    ProjectState,
    Screenshot,
    SegmentLineage,
    SegmentScreenshotAssociation,
    SpeakerLabel,
    SpeakerSource,
    TranscriptSegment,
    canonical_output_mode,
    utc_now,
)
from app.schemas import serialize_utc
from app.services.adaptive_selection import extract_adaptive_frames_to_directory
from app.services.diagnostics import METRICS_VERSION, MetricsCollector
from app.services.export_names import export_zip_file_name
from app.services.exporting import ExportRequest, ExportSegment, build_export_package
from app.services.media import MediaServiceError, extract_pcm_audio, validate_video
from app.services.pipeline import (
    PersistedFrameCandidate,
    PipelineServiceError,
    TranscriptionResult,
    extract_deduplicated_frames_to_directory,
    generate_frame_times,
    rank_candidates_for_segments,
    transcribe_audio,
    write_raw_transcript,
)
from app.services.progress import (
    cancellation_requested,
    clear_cancellation_flag,
    publish_job_event,
    update_job_progress,
)
from app.services.speaker_labels import label_segments, speaker_tracks_for
from app.services.workspaces import create_project_workspace, ensure_within_root

logger = logging.getLogger(__name__)


class ProcessingCancelled(PipelineServiceError):
    """Stop processing at safe unit boundaries after a durable user request."""

    def __init__(self) -> None:
        """Create an internal cancellation signal that survives pipeline error boundaries."""
        super().__init__("PROCESSING_CANCELLED", "Processing was cancelled.")


def perform_processing(
    job_id: str,
    settings: Settings | None = None,
    celery_task_id: str | None = None,
) -> None:
    """Run the complete real local pipeline and persist review-ready evidence."""
    active_settings = settings or get_settings()
    with SessionLocal() as session:
        job = _claim_processing_job(session, job_id, active_settings, celery_task_id)
        if job is None:
            return
        project = job.project
        processing_settings = _settings_from_job_snapshot(active_settings, job)
        processing_snapshot = job.configuration_snapshot or {}
        # A job queued before the project-type migration may still carry a legacy value.
        project_mode = canonical_output_mode(
            str(processing_snapshot.get("projectMode", project.mode_value))
        )
        metrics = MetricsCollector(
            project_id=project.id,
            operation_id=job.id,
            operation="processing",
            strategy=processing_settings.screenshot_selection_strategy,
        )
        workspace: Path | None = None
        audio_path: Path | None = None
        raw_transcript_path: Path | None = None
        screenshot_root: Path | None = None
        processing_root: Path | None = None
        try:
            if project.media_asset is None:
                raise PipelineServiceError(
                    "PROJECT_ASSET_MISSING", "The source video is unavailable."
                )
            workspace = create_project_workspace(active_settings.data_root, project.id)
            source_path = ensure_within_root(
                active_settings.data_root, project.media_asset.relative_path
            )
            audio_path = workspace / "audio" / "normalized-audio.wav"
            raw_transcript_path = workspace / "transcript" / "raw-transcript.json"
            screenshot_root = workspace / "screenshots"
            processing_root = workspace / ".processing" / job.id
            shutil.rmtree(processing_root, ignore_errors=True)
            _raise_if_cancelled(session, job.id)

            metrics.start_stage("validation")
            metadata = validate_video(source_path, config=processing_settings)
            metrics.finish_stage("validation", logger)
            update_job_progress(
                session,
                job,
                stage="extracting_audio",
                progress_percent=10,
                message="Source metadata is ready. Preparing transcription audio.",
            )
            _raise_if_cancelled(session, job.id)

            metrics.start_stage("audio_extraction")
            extract_pcm_audio(source_path, audio_path, config=processing_settings)
            metrics.finish_stage("audio_extraction", logger)
            update_job_progress(
                session,
                job,
                stage="transcribing",
                progress_percent=20,
                message="Audio is ready. Running local transcription.",
            )
            _raise_if_cancelled(session, job.id)

            metrics.start_stage("transcription")
            transcription = transcribe_audio(
                audio_path,
                config=processing_settings,
                source_language=project.source_language,
                glossary=project.glossary or [],
                duration_seconds=metadata.duration_seconds,
                cancellation_check=lambda: _raise_if_cancelled(session, job.id),
                progress_callback=lambda ratio: _update_transcription_progress(session, job, ratio),
            )
            write_raw_transcript(transcription, raw_transcript_path)
            metrics.set("glossaryTermCount", len(project.glossary or []))
            metrics.set("transcriptSegmentCount", len(transcription.segments))
            metrics.finish_stage("transcription", logger)
            speakers = _speaker_labels(
                project, transcription, processing_root, processing_settings, metrics
            )
            _raise_if_cancelled(session, job.id)
            update_job_progress(
                session,
                job,
                stage=(
                    "scanning_frames"
                    if processing_settings.screenshot_selection_strategy == "adaptive_v1"
                    else "extracting_frames"
                ),
                progress_percent=60,
                message=(
                    "The transcript is ready. Scanning for visual changes."
                    if processing_settings.screenshot_selection_strategy == "adaptive_v1"
                    else "The transcript is ready. Extracting screenshot candidates."
                ),
            )
            _raise_if_cancelled(session, job.id)

            if processing_settings.screenshot_selection_strategy == "adaptive_v1":
                candidates = extract_adaptive_frames_to_directory(
                    source_path,
                    metadata.duration_seconds,
                    transcription.segments,
                    processing_root / "screenshots",
                    project_mode=project_mode,
                    detected_language=transcription.language,
                    config=processing_settings,
                    metrics=metrics,
                    cancellation_check=lambda: _raise_if_cancelled(session, job.id),
                    progress_callback=lambda stage, completed, total: _update_adaptive_progress(
                        session, job, stage, completed, total
                    ),
                )
            else:
                metrics.start_stage("candidate_generation")
                frame_times = generate_frame_times(
                    metadata.duration_seconds,
                    transcription.segments,
                    config=processing_settings,
                )
                metrics.set("candidateTimestampCount", len(frame_times))
                metrics.finish_stage("candidate_generation", logger)
                metrics.start_stage("frame_extraction")
                candidates = extract_deduplicated_frames_to_directory(
                    source_path,
                    frame_times,
                    processing_root / "screenshots",
                    config=processing_settings,
                    neighbor_offset_seconds=processing_settings.frame_neighbor_seconds,
                    cancellation_check=lambda: _raise_if_cancelled(session, job.id),
                    progress_callback=lambda completed, total: _update_frame_progress(
                        session, job, completed, total
                    ),
                    decoded_frame_callback=lambda count: metrics.increment(
                        "decodedFrameCount", count
                    ),
                    provisional_candidate_callback=lambda: metrics.increment(
                        "provisionalCandidateCount"
                    ),
                )
                metrics.set("clusterRepresentativeCount", len(candidates))
                metrics.set("persistedThumbnailCount", 0)
                metrics.set("persistedPngCount", len(candidates))
                metrics.set(
                    "diskBytesPng",
                    sum(
                        candidate.image_path.stat().st_size
                        for candidate in candidates
                        if candidate.image_path.is_file()
                    ),
                )
                metrics.finish_stage("frame_extraction", logger)
            if processing_settings.screenshot_selection_strategy == "legacy":
                update_job_progress(
                    session,
                    job,
                    stage="deduplicating_frames",
                    progress_percent=75,
                    message="Candidate frames are ready. Finalizing consecutive deduplication.",
                )
                _raise_if_cancelled(session, job.id)

            update_job_progress(
                session,
                job,
                stage="associating_frames",
                progress_percent=83,
                message="Consecutive duplicate screens are ready for segment ranking.",
            )
            _raise_if_cancelled(session, job.id)

            metrics.start_stage("review_persistence")
            _replace_review_evidence(
                session,
                project.id,
                screenshot_root,
                transcription,
                candidates,
                processing_settings,
                speakers,
            )
            metrics.finish_stage("review_persistence", logger)
            metrics.set("screenshotRowCount", len(candidates))
            metrics.set(
                "associationCount",
                sum(
                    1 + len(selection.alternatives)
                    for selection in rank_candidates_for_segments(
                        list(
                            session.scalars(
                                select(TranscriptSegment)
                                .where(
                                    TranscriptSegment.project_id == project.id,
                                    TranscriptSegment.active.is_(True),
                                )
                                .order_by(TranscriptSegment.sequence)
                            )
                        ),
                        candidates,
                        config=processing_settings,
                    )
                    if selection.primary is not None
                ),
            )
            project.detected_language = transcription.language
            update_job_progress(
                session,
                job,
                stage="saving_results",
                progress_percent=92,
                message="Review evidence is associated and safely persisted.",
            )
            _raise_if_cancelled(session, job.id)

            project.state = ProjectState.REVIEW
            _record_processing_metrics(
                session,
                job,
                workspace,
                metrics,
                "completed",
            )
            update_job_progress(
                session,
                job,
                stage="completed",
                progress_percent=100,
                status=JobStatus.COMPLETED,
                message="Processing completed. The project is ready for review.",
                completed=True,
            )
            clear_cancellation_flag(job.id)
            shutil.rmtree(processing_root, ignore_errors=True)
        except ProcessingCancelled:
            session.rollback()
            _cleanup_processing_attempt(
                session,
                project.id,
                screenshot_root,
                audio_path,
                raw_transcript_path,
                processing_root,
            )
            _record_processing_metrics(session, job, workspace, metrics, "cancelled")
            _complete_cancellation(session, job)
        except (MediaServiceError, PipelineServiceError) as error:
            session.rollback()
            _cleanup_processing_attempt(
                session,
                project.id,
                screenshot_root,
                audio_path,
                raw_transcript_path,
                processing_root,
            )
            code = _stable_processing_code(error.code)
            _record_processing_metrics(session, job, workspace, metrics, "failed")
            _fail_processing(session, job, code, error.message)
        except Exception as error:
            logger.error(
                "Unexpected processing failure for job %s (%s).",
                job.id,
                type(error).__name__,
            )
            session.rollback()
            _cleanup_processing_attempt(
                session,
                project.id,
                screenshot_root,
                audio_path,
                raw_transcript_path,
                processing_root,
            )
            _record_processing_metrics(session, job, workspace, metrics, "failed")
            _fail_processing(
                session,
                job,
                "PROCESSING_FAILED",
                "Local processing could not be completed.",
            )


def perform_export(
    export_id: str,
    settings: Settings | None = None,
    celery_task_id: str | None = None,
) -> None:
    """Build one immutable ZIP from the current reviewed project snapshot."""
    active_settings = settings or get_settings()
    with SessionLocal() as session:
        export = _claim_export(session, export_id, active_settings, celery_task_id)
        if export is None:
            return
        project = export.project
        latest_processing_job = session.scalar(
            select(ProcessingJob)
            .where(
                ProcessingJob.project_id == project.id,
                ProcessingJob.status == JobStatus.COMPLETED,
            )
            .order_by(ProcessingJob.created_at.desc())
        )
        processing_snapshot = (
            latest_processing_job.configuration_snapshot if latest_processing_job else {}
        )
        metrics = MetricsCollector(
            project_id=project.id,
            operation_id=export.id,
            operation="export",
            strategy=str(processing_snapshot.get("selectionStrategy", "legacy")),
        )
        workspace: Path | None = None
        destination: Path | None = None
        try:
            if project.media_asset is None:
                raise FileNotFoundError("Source metadata is unavailable.")
            workspace = create_project_workspace(active_settings.data_root, project.id)
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
            export_segments = [
                export_segment_snapshot(active_settings, segment) for segment in segments
            ]
            source_path = ensure_within_root(
                active_settings.data_root, project.media_asset.relative_path
            )
            destination = workspace / "exports" / f"narrastep-{export.id}.zip"
            if destination.exists():
                raise FileExistsError("An immutable export artifact already exists.")
            if export.download_name is None:
                # Queued before the name was stored (see `assign_export_name`).
                assign_export_name(session, export, project)
                session.commit()
            request = ExportRequest(
                export_id=export.id,
                archive_name=(export.download_name or "").removesuffix(".zip"),
                project={
                    "id": project.id,
                    "name": project.name,
                    "description": project.description,
                    "mode": project.mode_value,
                    "sourceLanguage": project.source_language,
                    "detectedLanguage": project.detected_language,
                    "createdAt": serialize_utc(project.created_at),
                    "updatedAt": serialize_utc(project.updated_at),
                },
                source={
                    "assetId": project.media_asset.id,
                    "originalFilename": project.media_asset.display_filename,
                    "mimeType": project.media_asset.content_type,
                    "sizeBytes": project.media_asset.size_bytes,
                    "durationSeconds": project.media_asset.duration_seconds,
                    "width": project.media_asset.width,
                    "height": project.media_asset.height,
                    "containerFormat": project.media_asset.container_format,
                    "videoCodec": project.media_asset.metadata_json.get("videoCodec"),
                    "audioCodec": project.media_asset.metadata_json.get("audioCodec"),
                    "frameRate": project.media_asset.metadata_json.get("frameRate"),
                    "createdAt": serialize_utc(project.media_asset.created_at),
                },
                segments=export_segments,
                raw_transcript_path=workspace / "transcript" / "raw-transcript.json",
                destination_path=destination,
                output_mode=project.mode_value,
                original_video_path=source_path,
                include_original_video=export.include_original_video,
                created_at=export.created_at,
                chatgpt_max_files_per_batch=active_settings.chatgpt_max_files_per_batch,
                duplicate_similarity_threshold=(active_settings.duplicate_similarity_threshold),
                selection_strategy=str(processing_snapshot.get("selectionStrategy", "legacy")),
                image_budget_profile=str(
                    processing_snapshot.get(
                        "imageBudgetProfile", active_settings.image_budget_profile
                    )
                ),
                duration_seconds=project.media_asset.duration_seconds,
            )
            export.current_stage = "building_package"
            export.progress_percent = 50
            session.commit()
            metrics.start_stage("package_generation")
            build_export_package(request, metrics)
            metrics.finish_stage("package_generation", logger)
            session.refresh(export)
            session.refresh(project)
            if export.status != ExportStatus.RUNNING or project.state != ProjectState.EXPORTING:
                destination.unlink(missing_ok=True)
                return
            export.status = ExportStatus.COMPLETED
            export.current_stage = "completed"
            export.progress_percent = 100
            export.relative_path = destination.relative_to(active_settings.data_root).as_posix()
            export.size_bytes = destination.stat().st_size
            metrics.set("zipBytes", export.size_bytes)
            _record_export_metrics(
                session,
                export,
                workspace,
                metrics,
                "completed",
            )
            export.completed_at = utc_now()
            project.state = ProjectState.COMPLETED
            session.commit()
        except Exception as error:
            logger.error(
                "Unexpected export failure for export %s (%s).",
                export.id,
                type(error).__name__,
            )
            session.rollback()
            session.refresh(export)
            session.refresh(project)
            if export.status != ExportStatus.RUNNING:
                return
            if destination is not None:
                destination.with_suffix(destination.suffix + ".partial").unlink(missing_ok=True)
            export.status = ExportStatus.FAILED
            export.current_stage = "failed"
            if isinstance(error, FileNotFoundError):
                export.error_code = "PROJECT_ASSET_MISSING"
                export.error_message = "An export evidence asset is unavailable."
            else:
                export.error_code = "EXPORT_FAILED"
                export.error_message = "The export package could not be generated."
            export.completed_at = utc_now()
            _record_export_metrics(session, export, workspace, metrics, "failed")
            project.state = ProjectState.REVIEW
            session.commit()


def next_export_sequence(session: Session, project_id: str) -> int:
    """Return the number for a new export of the project: one more than any existing one.

    Counts rows as well as stored numbers, so exports without a number still raise it. Failed
    exports keep their number. The one-active-export rule serializes callers per project.
    """
    count = session.scalar(
        select(func.count())
        .select_from(ProjectExport)
        .where(ProjectExport.project_id == project_id)
    )
    highest = session.scalar(
        select(func.max(ProjectExport.sequence)).where(ProjectExport.project_id == project_id)
    )
    return max(int(count or 0), int(highest or 0)) + 1


def assign_export_name(session: Session, export: ProjectExport, project: Project) -> None:
    """Fix the export's number and ZIP name from the project's current type and name.

    Requires `export.created_at` to be set. Idempotent: an existing sequence is kept, and the name
    is built from it. The name is stored because later project renames must not change what an
    existing export is called; the caller commits. A row without a number (inserted outside the
    API) is numbered by its position among the project's exports by creation time.
    """
    if export.sequence is None:
        export.sequence = _export_sequence(session, export)
    export.download_name = export_zip_file_name(
        export.created_at,
        canonical_output_mode(project.mode_value),
        project.name,
        export.sequence,
    )


def _export_sequence(session: Session, export: ProjectExport) -> int:
    """Return the 1-based position of an export among its project's exports by creation time.

    Fallback for rows without a stored number; ties on `created_at` are broken by id.
    """
    count = session.scalar(
        select(func.count())
        .select_from(ProjectExport)
        .where(
            ProjectExport.project_id == export.project_id,
            or_(
                ProjectExport.created_at < export.created_at,
                and_(
                    ProjectExport.created_at == export.created_at,
                    ProjectExport.id <= export.id,
                ),
            ),
        )
    )
    return max(1, int(count or 0))


def _claim_processing_job(
    session: Session,
    job_id: str,
    settings: Settings,
    celery_task_id: str | None,
) -> ProcessingJob | None:
    """Atomically claim one queued job so late-ack redelivery cannot repeat processing."""
    now = utc_now()
    claimed = session.execute(
        update(ProcessingJob)
        .where(
            ProcessingJob.id == job_id,
            ProcessingJob.status == JobStatus.QUEUED,
            ProcessingJob.cancel_requested.is_(False),
            ProcessingJob.project.has(Project.state == ProjectState.QUEUED),
        )
        .values(
            status=JobStatus.RUNNING,
            stage="validating",
            message="Validating the source video.",
            started_at=now,
            updated_at=now,
            progress_version=ProcessingJob.progress_version + 1,
        )
    )
    if getattr(claimed, "rowcount", 0) != 1:
        session.rollback()
        _interrupt_processing_redelivery(session, job_id, settings, celery_task_id)
        return None
    job = session.get(ProcessingJob, job_id)
    if job is None:
        session.rollback()
        return None
    project_claim = session.execute(
        update(Project)
        .where(Project.id == job.project_id, Project.state == ProjectState.QUEUED)
        .values(state=ProjectState.PROCESSING, updated_at=now)
    )
    if getattr(project_claim, "rowcount", 0) != 1:
        session.rollback()
        return None
    session.commit()
    session.refresh(job)
    clear_cancellation_flag(job.id)
    publish_job_event(job)
    return job


def _claim_export(
    session: Session,
    export_id: str,
    settings: Settings,
    celery_task_id: str | None,
) -> ProjectExport | None:
    """Atomically claim only a queued export and never rebuild a terminal artifact."""
    claimed = session.execute(
        update(ProjectExport)
        .where(
            ProjectExport.id == export_id,
            ProjectExport.status == ExportStatus.QUEUED,
            ProjectExport.project.has(Project.state == ProjectState.EXPORTING),
        )
        .values(
            status=ExportStatus.RUNNING,
            current_stage="collecting_evidence",
            progress_percent=10,
            updated_at=utc_now(),
        )
    )
    if getattr(claimed, "rowcount", 0) != 1:
        session.rollback()
        _interrupt_export_redelivery(session, export_id, settings, celery_task_id)
        return None
    session.commit()
    export = session.get(ProjectExport, export_id)
    if export is not None:
        session.refresh(export)
    return export


def _interrupt_processing_redelivery(
    session: Session,
    job_id: str,
    settings: Settings,
    celery_task_id: str | None,
) -> None:
    """Resolve same-task late-ack redelivery instead of leaving durable work running."""
    job = session.get(ProcessingJob, job_id)
    if (
        job is None
        or celery_task_id is None
        or job.celery_task_id != celery_task_id
        or job.status not in {JobStatus.RUNNING, JobStatus.CANCELLING}
    ):
        return
    try:
        workspace = create_project_workspace(settings.data_root, job.project_id)
        _cleanup_processing_attempt(
            session,
            job.project_id,
            workspace / "screenshots",
            workspace / "audio" / "normalized-audio.wav",
            workspace / "transcript" / "raw-transcript.json",
            workspace / ".processing" / job.id,
        )
    except Exception as error:
        session.rollback()
        logger.error(
            "Interrupted processing cleanup could not start for job %s (%s).",
            job.id,
            type(error).__name__,
        )
    if job.cancel_requested or job.status == JobStatus.CANCELLING:
        job.status = JobStatus.CANCELLED
        job.stage = "cancelled"
        job.message = "Processing cancellation completed after a local service restart."
        job.error_code = None
        job.error_message = None
        job.project.state = ProjectState.UPLOADED
    else:
        job.status = JobStatus.FAILED
        job.stage = "failed"
        job.message = "Processing was interrupted by a local service restart."
        job.error_code = "PROCESSING_INTERRUPTED"
        job.error_message = "Processing was interrupted and can be retried from the source video."
        job.project.state = ProjectState.FAILED
    job.progress_version += 1
    job.completed_at = utc_now()
    session.commit()
    session.refresh(job)
    clear_cancellation_flag(job.id)
    publish_job_event(job)


def _interrupt_export_redelivery(
    session: Session,
    export_id: str,
    settings: Settings,
    celery_task_id: str | None,
) -> None:
    """Fail same-task export redelivery without rebuilding or overwriting an artifact."""
    export = session.get(ProjectExport, export_id)
    if (
        export is None
        or celery_task_id is None
        or export.celery_task_id != celery_task_id
        or export.status != ExportStatus.RUNNING
    ):
        return
    workspace = create_project_workspace(settings.data_root, export.project_id)
    destination = workspace / "exports" / f"narrastep-{export.id}.zip"
    destination.with_suffix(destination.suffix + ".partial").unlink(missing_ok=True)
    export.status = ExportStatus.FAILED
    export.current_stage = "failed"
    export.error_code = "EXPORT_FAILED"
    export.error_message = "Export was interrupted and can be started again."
    export.completed_at = utc_now()
    export.project.state = ProjectState.REVIEW
    session.commit()


def _update_transcription_progress(session: Session, job: ProcessingJob, ratio: float) -> None:
    """Persist completed transcription work without advancing beyond its stage boundary."""
    progress = min(59, 20 + int(max(0.0, min(1.0, ratio)) * 40))
    if progress <= job.progress_percent:
        return
    update_job_progress(
        session,
        job,
        stage="transcribing",
        progress_percent=progress,
        message="Local transcription is in progress.",
    )


def _update_frame_progress(
    session: Session, job: ProcessingJob, completed: int, total: int
) -> None:
    """Persist completed frame sampling work while limiting database update frequency."""
    if total <= 0:
        return
    progress = min(74, 60 + int(completed / total * 15))
    if progress <= job.progress_percent:
        return
    update_job_progress(
        session,
        job,
        stage="extracting_frames",
        progress_percent=progress,
        message="Screenshot candidates are being extracted.",
    )


def _update_adaptive_progress(
    session: Session,
    job: ProcessingJob,
    stage: str,
    completed: int,
    total: int,
) -> None:
    """Map adaptive scan and fine extraction work into monotonic durable progress."""
    if total <= 0:
        return
    ranges = {
        "scanning_frames": (60, 66, "Scanning the video for visual changes."),
        "extracting_frames": (66, 73, "Extracting stable frames from interest windows."),
        "global_clustering": (73, 77, "Clustering reusable visual evidence."),
        "coverage_selection": (77, 80, "Selecting transcript and visual coverage."),
        "thumbnail_persistence": (80, 83, "Saving lightweight review thumbnails."),
    }
    start, end, message = ranges.get(stage, (60, 78, "Selecting screenshot evidence."))
    progress = min(end, start + int(completed / total * (end - start)))
    if progress <= job.progress_percent:
        return
    update_job_progress(
        session,
        job,
        stage=stage,
        progress_percent=progress,
        message=message,
    )


def _settings_from_job_snapshot(settings: Settings, job: ProcessingJob) -> Settings:
    """Apply the validated behavior snapshot captured when the job was queued."""
    snapshot = job.configuration_snapshot or {}
    strategy = snapshot.get("selectionStrategy", settings.screenshot_selection_strategy)
    ocr_mode = snapshot.get("ocrMode", settings.ocr_mode)
    budget_profile = snapshot.get("imageBudgetProfile", settings.image_budget_profile)
    tesseract_path = snapshot.get("tesseractPath", settings.tesseract_path)
    screenshot_alternatives = snapshot.get(
        "screenshotAlternatives", settings.screenshot_alternatives
    )
    updates: dict[str, object] = {}
    if strategy in {"legacy", "adaptive_v1"}:
        updates["screenshot_selection_strategy"] = strategy
    if ocr_mode in {"off", "selective"}:
        updates["ocr_mode"] = ocr_mode
    if budget_profile in {"compact", "balanced", "detailed"}:
        updates["image_budget_profile"] = budget_profile
    if isinstance(tesseract_path, str) and tesseract_path:
        updates["tesseract_path"] = tesseract_path
    if isinstance(screenshot_alternatives, int) and 0 <= screenshot_alternatives <= 20:
        updates["screenshot_alternatives"] = screenshot_alternatives
    threshold_fields = {
        "directDuplicateThreshold": "adaptive_direct_duplicate_threshold",
        "borderlineDuplicateThreshold": "adaptive_borderline_duplicate_threshold",
        "borderlineSsimThreshold": "adaptive_borderline_ssim_threshold",
        "ocrTokenJaccardThreshold": "adaptive_ocr_token_jaccard_threshold",
    }
    for snapshot_name, setting_name in threshold_fields.items():
        value = snapshot.get(snapshot_name)
        if isinstance(value, (int, float)) and 0 <= value <= 1:
            updates[setting_name] = float(value)
    return settings.model_copy(update=updates)


def _record_processing_metrics(
    session: Session,
    job: ProcessingJob,
    workspace: Path | None,
    metrics: MetricsCollector,
    terminal_status: str,
) -> None:
    """Persist safe numeric diagnostics before the matching terminal job transaction."""
    current = session.get(ProcessingJob, job.id) or job
    snapshot = metrics.snapshot(terminal_status)
    current.metrics_summary = snapshot
    current.metrics_version = METRICS_VERSION
    if workspace is not None:
        destination = workspace / "diagnostics" / f"processing-{job.id}.json"
        metrics.write_atomic(destination, terminal_status)
        current.diagnostics_relative_path = destination.relative_to(workspace.parents[1]).as_posix()
    session.flush()


def _record_export_metrics(
    session: Session,
    export: ProjectExport,
    workspace: Path | None,
    metrics: MetricsCollector,
    terminal_status: str,
) -> None:
    """Persist safe export counts and timings with the immutable export lifecycle."""
    current = session.get(ProjectExport, export.id) or export
    current.metrics_summary = metrics.snapshot(terminal_status)
    current.metrics_version = METRICS_VERSION
    if workspace is not None:
        destination = workspace / "diagnostics" / f"export-{export.id}.json"
        metrics.write_atomic(destination, terminal_status)
        current.diagnostics_relative_path = destination.relative_to(workspace.parents[1]).as_posix()
    session.flush()


def _speaker_labels(
    project: Project,
    transcription: TranscriptionResult,
    processing_root: Path,
    settings: Settings,
    metrics: MetricsCollector,
) -> list[SpeakerLabel | None] | None:
    """Return one You / Others label (or None) per transcribed segment of an in-app recording.

    Returns None without measuring when the project's video has no kept microphone and system
    audio tracks (uploads, recordings with one audio source, recordings saved before P05.5).
    Labels are an aid for review, so a track that cannot be decoded or measured does not fail
    processing: the error code is logged, `speakerLabelFailed` is set in the metrics and every
    segment stays unlabelled. Counts per outcome go into the processing metrics.
    """
    if project.media_asset is None:
        return None
    tracks = speaker_tracks_for(project.media_asset, settings.data_root)
    if tracks is None:
        return None
    metrics.start_stage("speaker_labels")
    try:
        labels = label_segments(
            tracks,
            [(segment.start_seconds, segment.end_seconds) for segment in transcription.segments],
            processing_root / "speakers",
            config=settings,
        )
    except MediaServiceError as error:
        # Only the code is logged: FFmpeg diagnostics may contain local paths.
        logger.warning("Speaker labels skipped for project %s (%s).", project.id, error.code)
        metrics.set("speakerLabelFailed", 1)
        metrics.finish_stage("speaker_labels", logger)
        return None
    metrics.set("speakerLabelYouCount", labels.count(SpeakerLabel.YOU))
    metrics.set("speakerLabelOthersCount", labels.count(SpeakerLabel.OTHERS))
    metrics.set("speakerLabelUnlabelledCount", labels.count(None))
    metrics.finish_stage("speaker_labels", logger)
    return labels


def _replace_review_evidence(
    session: Session,
    project_id: str,
    screenshot_root: Path,
    transcription: TranscriptionResult,
    candidates: list[PersistedFrameCandidate],
    settings: Settings,
    speakers: list[SpeakerLabel | None] | None = None,
) -> None:
    """Replace derived rows and files while preserving the validated source video.

    `speakers`, when given, holds one automatic label (or None) per transcribed segment in the
    same order; without it every segment is stored unlabelled.
    """
    session.execute(
        delete(SegmentLineage).where(
            SegmentLineage.child_segment_id.in_(
                select(TranscriptSegment.id).where(TranscriptSegment.project_id == project_id)
            )
        )
    )
    session.execute(
        delete(SegmentScreenshotAssociation).where(
            SegmentScreenshotAssociation.segment_id.in_(
                select(TranscriptSegment.id).where(TranscriptSegment.project_id == project_id)
            )
        )
    )
    session.execute(delete(TranscriptSegment).where(TranscriptSegment.project_id == project_id))
    session.execute(delete(Screenshot).where(Screenshot.project_id == project_id))
    session.flush()
    shutil.rmtree(screenshot_root, ignore_errors=True)
    screenshot_root.mkdir(parents=True, exist_ok=True)

    domain_segments: list[TranscriptSegment] = []
    if speakers is not None and len(speakers) != len(transcription.segments):
        raise ValueError("Speaker labels must match the transcribed segments one to one.")
    for index, source in enumerate(transcription.segments):
        speaker = speakers[index] if speakers is not None else None
        segment = TranscriptSegment(
            project_id=project_id,
            sequence=source.source_index,
            start_seconds=source.start_seconds,
            end_seconds=source.end_seconds,
            raw_text=source.text,
            reviewed_text=source.text,
            raw_words=[word.to_dict() for word in source.words],
            confidence=source.confidence,
            speaker=speaker,
            speaker_source=SpeakerSource.AUTOMATIC if speaker is not None else None,
        )
        session.add(segment)
        domain_segments.append(segment)
    session.flush()

    screenshots_by_candidate: dict[int, Screenshot] = {}
    for index, candidate in enumerate(candidates):
        if candidate.materialization_state == "thumbnail_only":
            output = screenshot_root / "thumbnails" / f"frame-{index + 1:04d}.webp"
        else:
            output = screenshot_root / f"frame-{index + 1:04d}.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate.image_path, output)
        screenshot = Screenshot(
            project_id=project_id,
            relative_path=(
                output.relative_to(settings.data_root).as_posix()
                if candidate.materialization_state == "png_ready"
                else None
            ),
            thumbnail_relative_path=(
                output.relative_to(settings.data_root).as_posix()
                if candidate.materialization_state == "thumbnail_only"
                else None
            ),
            materialization_state=candidate.materialization_state,
            selection_reason=candidate.selection_reason,
            visual_change_score=candidate.visual_change_score,
            text_change_score=candidate.text_change_score,
            cluster_id=candidate.cluster_id,
            is_mandatory=candidate.is_mandatory,
            timestamp_seconds=candidate.timestamp_seconds,
            perceptual_hash=candidate.perceptual_hash,
            quality_score=candidate.quality_score,
            width=candidate.width,
            height=candidate.height,
        )
        session.add(screenshot)
        screenshots_by_candidate[id(candidate)] = screenshot
    session.flush()

    selections = rank_candidates_for_segments(domain_segments, candidates, config=settings)
    for segment, selection in zip(domain_segments, selections, strict=True):
        ranked = ([selection.primary] if selection.primary is not None else []) + list(
            selection.alternatives
        )
        for rank, selected in enumerate(ranked):
            if selected is None:
                continue
            screenshot = screenshots_by_candidate[id(selected.candidate)]
            session.add(
                SegmentScreenshotAssociation(
                    segment_id=segment.id,
                    screenshot_id=screenshot.id,
                    is_primary=rank == 0,
                    rank=rank,
                    score=selected.score,
                    coverage_score=getattr(selected.candidate, "coverage_score", None),
                    role_rank_reason=(
                        getattr(selected.candidate, "role_rank_reason", None)
                        or ("automatic_primary" if rank == 0 else "temporal_alternative")
                    ),
                    user_selected=False,
                )
            )
    session.flush()


def export_segment_snapshot(settings: Settings, segment: TranscriptSegment) -> ExportSegment:
    """Capture one active segment and its optional primary screenshot for export."""
    primary = next(
        (item for item in segment.screenshot_associations if item.is_primary),
        None,
    )
    screenshot = primary.screenshot if primary is not None else None
    screenshot_path = (
        ensure_within_root(settings.data_root, screenshot.relative_path)
        if screenshot and screenshot.relative_path
        else None
    )
    thumbnail_path = (
        ensure_within_root(settings.data_root, screenshot.thumbnail_relative_path)
        if screenshot and screenshot.thumbnail_relative_path
        else None
    )
    source_video_path = (
        ensure_within_root(settings.data_root, segment.project.media_asset.relative_path)
        if screenshot and segment.project.media_asset
        else None
    )
    return ExportSegment(
        id=segment.id,
        sequence=segment.sequence,
        start_seconds=segment.start_seconds,
        end_seconds=segment.end_seconds,
        raw_text=segment.raw_text,
        reviewed_text=segment.reviewed_text,
        speaker=segment.speaker,
        confidence=segment.confidence,
        screenshot_id=screenshot.id if screenshot else None,
        screenshot_path=screenshot_path,
        screenshot_thumbnail_path=thumbnail_path,
        screenshot_source_video_path=source_video_path,
        screenshot_timestamp_seconds=screenshot.timestamp_seconds if screenshot else None,
        screenshot_perceptual_hash=screenshot.perceptual_hash if screenshot else None,
        screenshot_quality_score=screenshot.quality_score if screenshot else None,
        screenshot_association_score=primary.score if primary else None,
        screenshot_user_selected=primary.user_selected if primary else False,
        screenshot_is_mandatory=screenshot.is_mandatory if screenshot else False,
        screenshot_selection_reason=screenshot.selection_reason if screenshot else None,
        screenshot_width=screenshot.width if screenshot else None,
        screenshot_height=screenshot.height if screenshot else None,
    )


def _raise_if_cancelled(session: Session, job_id: str) -> None:
    """Convert the durable cancellation flag into local control flow at safe boundaries."""
    if cancellation_requested(session, job_id):
        raise ProcessingCancelled()


def _complete_cancellation(session: Session, job: ProcessingJob) -> None:
    """Finalize a cooperative cancellation and restore the project to uploaded state."""
    job.status = JobStatus.CANCELLED
    job.stage = "cancelled"
    job.message = "Processing was cancelled safely."
    job.progress_version += 1
    job.completed_at = utc_now()
    job.project.state = ProjectState.UPLOADED
    session.commit()
    session.refresh(job)
    clear_cancellation_flag(job.id)
    publish_job_event(job)


def _fail_processing(session: Session, job: ProcessingJob, code: str, message: str) -> None:
    """Persist a safe terminal failure without exposing command output or transcript text."""
    job.status = JobStatus.FAILED
    job.stage = "failed"
    job.message = "Processing could not be completed."
    job.error_code = code
    job.error_message = message[:500]
    job.progress_version += 1
    job.completed_at = utc_now()
    job.project.state = ProjectState.FAILED
    session.commit()
    session.refresh(job)
    clear_cancellation_flag(job.id)
    publish_job_event(job)


def _stable_processing_code(code: str) -> str:
    """Map service-specific failures to documented stable public processing codes."""
    if code in {"PROJECT_ASSET_MISSING", "MEDIA_FILE_MISSING"}:
        return "PROJECT_ASSET_MISSING"
    if code.startswith("TRANSCRIPTION"):
        return "TRANSCRIPTION_FAILED"
    if code in {"AUDIO_STREAM_MISSING"}:
        return "VIDEO_AUDIO_TRACK_MISSING"
    if code.startswith("FFMPEG"):
        return "FFMPEG_FAILED"
    if code.startswith("FFPROBE"):
        return "FFPROBE_FAILED"
    if code.startswith("FRAME"):
        return "FRAME_EXTRACTION_FAILED"
    if code in {"OCR_UNAVAILABLE", "OCR_LANGUAGE_UNAVAILABLE", "OCR_FAILED"}:
        return code
    return "VIDEO_VALIDATION_FAILED"


def _cleanup_audio(audio_path: Path) -> None:
    """Remove the reproducible PCM derivative after cancellation or failure."""
    audio_path.unlink(missing_ok=True)


def _cleanup_processing_attempt(
    session: Session,
    project_id: str,
    screenshot_root: Path | None,
    audio_path: Path | None,
    raw_transcript_path: Path | None,
    processing_root: Path | None,
) -> None:
    """Best-effort cleanup partial evidence without masking the durable terminal state."""
    try:
        if processing_root is not None:
            shutil.rmtree(processing_root, ignore_errors=True)
        if screenshot_root is None or audio_path is None or raw_transcript_path is None:
            return
        _cleanup_partial_derivatives(
            session, project_id, screenshot_root, audio_path, raw_transcript_path
        )
    except Exception as error:
        session.rollback()
        logger.error(
            "Partial derivative cleanup failed for project %s (%s).",
            project_id,
            type(error).__name__,
        )


def _cleanup_partial_derivatives(
    session: Session,
    project_id: str,
    screenshot_root: Path,
    audio_path: Path,
    raw_transcript_path: Path,
) -> None:
    """Remove incomplete review derivatives while retaining the original source video."""
    _cleanup_audio(audio_path)
    raw_transcript_path.unlink(missing_ok=True)
    shutil.rmtree(screenshot_root, ignore_errors=True)
    screenshot_root.mkdir(parents=True, exist_ok=True)
    session.execute(
        delete(SegmentLineage).where(
            SegmentLineage.child_segment_id.in_(
                select(TranscriptSegment.id).where(TranscriptSegment.project_id == project_id)
            )
        )
    )
    session.execute(
        delete(SegmentScreenshotAssociation).where(
            SegmentScreenshotAssociation.segment_id.in_(
                select(TranscriptSegment.id).where(TranscriptSegment.project_id == project_id)
            )
        )
    )
    session.execute(delete(TranscriptSegment).where(TranscriptSegment.project_id == project_id))
    session.execute(delete(Screenshot).where(Screenshot.project_id == project_id))
    session.commit()
