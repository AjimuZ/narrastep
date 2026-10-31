"""Durable orchestration of one local document generation: claim, parts, merge, store and stop."""

from __future__ import annotations

import logging
import math
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (
    ExportStatus,
    Generation,
    JobStatus,
    ModelInstallState,
    ProcessingJob,
    Project,
    ProjectExport,
    TranscriptSegment,
    canonical_output_mode,
    utc_now,
)
from app.services.exporting import select_package_screenshots
from app.services.generation_input import (
    GenerationInput,
    GenerationScreenshot,
    GenerationSegment,
    build_generation_input,
)
from app.services.generation_parts import PartBudget, build_merge_input, plan_generation_parts
from app.services.llama_server import LLAMA_SERVER_DIR, LlamaServerUnavailableError
from app.services.llama_session import (
    DEFAULT_CONTEXT_TOKENS,
    GenerationOutOfMemoryError,
    LlamaServerRequestError,
    LlamaServerSession,
    ensure_enough_memory,
    estimate_required_memory_bytes,
    read_available_memory_bytes,
)
from app.services.model_catalog import ModelOption, get_model_option
from app.services.model_download import model_directory
from app.services.model_manager import read_model_state
from app.services.processing import export_segment_snapshot
from app.services.screenshot_materialization import decode_exact_frame, write_png_atomic
from app.services.workspaces import create_project_workspace, ensure_within_root

logger = logging.getLogger(__name__)

# Changes whenever the wording of a prompt or the way parts are merged changes, so a stored
# document says which instructions produced it.
GENERATION_PROMPT_VERSION = "generation-prompts-v1"
GENERATIONS_DIRECTORY = "generations"
DOCUMENT_FILE_NAME = "document.md"

# Estimated tokens kept free in the context for the system prompt and for the answer; the rest of
# the context is the budget of one part. Estimates until P06.5 measures a real run.
SYSTEM_PROMPT_TOKENS = 1200
ANSWER_TOKENS = 2048
PART_BUDGET_TOKENS = DEFAULT_CONTEXT_TOKENS - SYSTEM_PROMPT_TOKENS - ANSWER_TOKENS
# Notes are merged in groups whose estimated size stays below this, so a merge request fits the
# context however many parts there are.
MERGE_INPUT_TOKENS = 5000
_CHARS_PER_TOKEN = 4
# How often a running model request looks for a cancellation.
_CANCEL_POLL_SECONDS = 1.0

LANGUAGE_NAMES = {"en": "English", "it": "Italian"}

# Percent of the whole job at each boundary; the parts share the span between the last two.
_PROGRESS_SERVER = 10
_PROGRESS_PARTS_END = 90
_PROGRESS_SAVING = 97

ERROR_MODEL_UNAVAILABLE = "GENERATION_MODEL_UNAVAILABLE"
ERROR_SERVER_FAILED = "GENERATION_SERVER_FAILED"
ERROR_FAILED = "GENERATION_FAILED"
ERROR_INTERRUPTED = "GENERATION_INTERRUPTED"
ERROR_WORKER_BUSY = "WORKER_BUSY"
ERROR_DISABLED = "LOCAL_GENERATION_DISABLED"


class GenerationFailure(Exception):
    """Stop a generation with a stable public `code` and a readable English `message`.

    The message never holds transcript text, a path or model output.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class GenerationCancelled(Exception):
    """Signal a durable cancellation request reached at a safe point of the job."""


@dataclass(frozen=True, slots=True)
class ModelFiles:
    """The paths of an enabled model's weights and vision projector, and its pinned option."""

    option: ModelOption
    model_path: Path
    projector_path: Path


def require_enabled_model(session: Session, settings: Settings, model_id: str) -> ModelFiles:
    """Return the files of `model_id`, or raise `GenerationFailure` when it cannot run.

    The kill switch must allow generation, the stored state must be `enabled` and every pinned
    file must exist with its pinned size. Hashes were verified when the model was installed and
    are not computed again here: hashing 3 to 5 GB before each run would only delay it.
    """
    if not settings.local_generation_available:
        raise GenerationFailure(ERROR_DISABLED, "Local generation is disabled.")
    option = get_model_option(model_id)
    if read_model_state(session, model_id) is not ModelInstallState.ENABLED:
        raise GenerationFailure(
            ERROR_MODEL_UNAVAILABLE,
            "The generation model is not enabled. Install and enable it first.",
        )
    directory = model_directory(settings, model_id)
    for file in option.files:
        path = directory / file.name
        if not path.is_file() or path.stat().st_size != file.size_bytes:
            raise GenerationFailure(
                ERROR_MODEL_UNAVAILABLE,
                "The generation model files are missing or incomplete. Download or import them "
                "again.",
            )
    projector = next(file for file in option.files if file.name.startswith("mmproj-"))
    weights = next(file for file in option.files if file is not projector)
    return ModelFiles(option, directory / weights.name, directory / projector.name)


def pinned_model_files(option: ModelOption) -> list[dict[str, object]]:
    """Describe the files of `option` as stored with a generation: name, size and SHA-256."""
    return [
        {"name": file.name, "size_bytes": file.size_bytes, "sha256": file.sha256}
        for file in option.files
    ]


def generation_directory(settings: Settings, project_id: str, generation_id: str) -> Path:
    """Return `<data root>/projects/{project_id}/generations/{generation_id}`, below the root."""
    workspace = create_project_workspace(settings.data_root, project_id)
    return ensure_within_root(
        settings.data_root,
        (workspace / GENERATIONS_DIRECTORY / generation_id).relative_to(settings.data_root),
    )


def perform_generation(
    generation_id: str,
    settings: Settings | None = None,
    celery_task_id: str | None = None,
) -> None:
    """Produce the document of one queued generation and record the outcome.

    Claims the generation (a repeated delivery of a finished or running one does nothing), checks
    the model and the free memory, builds the evidence in transcript order, runs one model request
    per planned part and merges the notes when there are several. The document is written to
    `projects/{id}/generations/{generationId}/document.md` with the prompt version and the pinned
    model file hashes. The model server is always stopped. Never raises for a job failure: every
    outcome becomes `completed`, `cancelled` or `failed` with a stable error code. A job that
    finds other work running on the worker fails with `WORKER_BUSY` instead of overlapping it.
    """
    active_settings = settings or get_settings()
    with SessionLocal() as session:
        claimed = _claim_generation(session, generation_id, active_settings, celery_task_id)
        if claimed is None:
            return
        project_id = claimed.project_id
        directory: Path | None = None
        try:
            directory = generation_directory(active_settings, project_id, generation_id)
            _run_generation(session, active_settings, generation_id, directory)
        except GenerationCancelled:
            session.rollback()
            _remove_directory(directory)
            _finish_cancelled(session, generation_id)
        except GenerationFailure as error:
            session.rollback()
            _remove_directory(directory)
            _fail_generation(session, generation_id, error.code, error.message)
        except GenerationOutOfMemoryError as error:
            session.rollback()
            _remove_directory(directory)
            _fail_generation(session, generation_id, error.code, str(error))
        except (LlamaServerUnavailableError, LlamaServerRequestError) as error:
            # The error text can name a path, so only its type is logged.
            logger.error(
                "The generation server failed for generation %s (%s).",
                generation_id,
                type(error).__name__,
            )
            session.rollback()
            _remove_directory(directory)
            _fail_generation(
                session,
                generation_id,
                ERROR_SERVER_FAILED,
                "The local generation server stopped or did not answer. Try again.",
            )
        except Exception as error:
            logger.error(
                "Unexpected generation failure for generation %s (%s).",
                generation_id,
                type(error).__name__,
            )
            session.rollback()
            _remove_directory(directory)
            _fail_generation(
                session, generation_id, ERROR_FAILED, "The document could not be generated."
            )


def _run_generation(
    session: Session, settings: Settings, generation_id: str, directory: Path
) -> None:
    """Run the steps of a claimed generation; raise `GenerationCancelled` or a failure.

    The server session is a context manager, so the process is stopped whichever way this ends.
    """
    generation = _load(session, generation_id)
    if _other_work_is_running(session, generation_id):
        raise GenerationFailure(
            ERROR_WORKER_BUSY,
            "The worker is busy with another job. Start the generation again when it finishes.",
        )
    files = require_enabled_model(session, settings, generation.model_id)
    ensure_enough_memory(
        estimate_required_memory_bytes(files.option), read_available_memory_bytes()
    )
    _raise_if_cancelled(session, generation_id)

    _record_progress(session, generation_id, "preparing", 5, "Collecting the reviewed evidence.")
    work_directory = directory / ".work"
    shutil.rmtree(directory, ignore_errors=True)
    work_directory.mkdir(parents=True)
    project = session.get(Project, generation.project_id)
    if project is None:
        raise GenerationFailure(ERROR_FAILED, "The project of this generation no longer exists.")
    output_mode = canonical_output_mode(project.mode_value)
    segments, screenshots = _load_evidence(session, settings, project, output_mode, work_directory)
    parts = plan_generation_parts(segments, screenshots, PartBudget(max_tokens=PART_BUDGET_TOKENS))
    if not parts:
        raise GenerationFailure(ERROR_FAILED, "The project has no transcript to generate from.")
    _set_parts_total(session, generation_id, len(parts))
    _raise_if_cancelled(session, generation_id)

    _record_progress(
        session, generation_id, "starting_server", _PROGRESS_SERVER, "Starting the local model."
    )
    with LlamaServerSession(
        files.model_path,
        files.projector_path,
        directory=LLAMA_SERVER_DIR,
        threads=settings.generation_threads,
    ) as server:
        notes: list[str] = []
        for number, part in enumerate(parts, start=1):
            request = build_generation_input(
                output_mode,
                list(part.segments),
                list(part.screenshots),
                image_max_edge=settings.generation_image_max_edge,
                language=_language_name(generation.language),
            )
            notes.append(_complete(session, server, generation_id, request))
            done_percent = _PROGRESS_SERVER + int(
                (_PROGRESS_PARTS_END - _PROGRESS_SERVER) * number / len(parts)
            )
            _record_progress(
                session,
                generation_id,
                "generating",
                done_percent,
                f"Part {number} of {len(parts)} is done.",
                parts_completed=number,
            )
            _raise_if_cancelled(session, generation_id)
        if len(notes) > 1:
            _record_progress(
                session,
                generation_id,
                "merging",
                _PROGRESS_PARTS_END,
                f"Merging the notes of {len(parts)} parts.",
            )
        document = _merge_notes(
            session, server, generation_id, output_mode, notes, _language_name(generation.language)
        )

    _record_progress(session, generation_id, "saving", _PROGRESS_SAVING, "Saving the document.")
    _raise_if_cancelled(session, generation_id)
    shutil.rmtree(work_directory, ignore_errors=True)
    target = _write_document(directory, document, files.option, generation.prompt_version)
    _finish_completed(session, settings, generation_id, target)


def _language_name(code: str | None) -> str | None:
    """Return the English name of a language code for the prompt, or the code itself."""
    return LANGUAGE_NAMES.get(code, code) if code else None


def _load(session: Session, generation_id: str) -> Generation:
    """Return the generation row as currently stored; raise `GenerationFailure` when it is gone.

    The row is reloaded because the cancel endpoint changes it from another process and the
    session does not expire objects on commit.
    """
    generation = session.get(Generation, generation_id, populate_existing=True)
    if generation is None:
        raise GenerationFailure(ERROR_FAILED, "The generation no longer exists.")
    return generation


def _load_evidence(
    session: Session,
    settings: Settings,
    project: Project,
    output_mode: str,
    work_directory: Path,
) -> tuple[list[GenerationSegment], list[GenerationScreenshot]]:
    """Return the reviewed segments and the package screenshots of the project, in time order.

    Screenshots are the ones the export selector keeps, with the names a package gives them, so
    the document cites files the user can find. A screenshot stored only as a position in the
    original video is decoded into `work_directory`. Raises `FileNotFoundError` for an image that
    cannot be found. The text is never logged.
    """
    rows = list(
        session.scalars(
            select(TranscriptSegment)
            .where(TranscriptSegment.project_id == project.id, TranscriptSegment.active.is_(True))
            .order_by(TranscriptSegment.sequence)
        )
    )
    snapshots = [export_segment_snapshot(settings, row) for row in rows]
    processing_job = session.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.project_id == project.id, ProcessingJob.status == JobStatus.COMPLETED)
        .order_by(ProcessingJob.created_at.desc())
    )
    processing = processing_job.configuration_snapshot if processing_job else {}
    asset = project.media_asset
    selected = select_package_screenshots(
        snapshots,
        similarity_threshold=settings.duplicate_similarity_threshold,
        selection_strategy=str(processing.get("selectionStrategy", "legacy")),
        output_mode=output_mode,
        duration_seconds=asset.duration_seconds if asset else 0.0,
        budget_profile=str(processing.get("imageBudgetProfile", settings.image_budget_profile)),
    )
    screenshots: list[GenerationScreenshot] = []
    for item in selected:
        if item.source_path is not None and item.source_path.is_file():
            path = item.source_path
        elif item.source_video_path is not None and item.source_timestamp_seconds is not None:
            path = write_png_atomic(
                decode_exact_frame(item.source_video_path, item.source_timestamp_seconds),
                work_directory / item.file_name,
            )
        else:
            raise FileNotFoundError("A selected screenshot asset is unavailable.")
        screenshots.append(GenerationScreenshot(item.file_name, item.timestamp_seconds, path))
    segments = [
        GenerationSegment(
            start_seconds=row.start_seconds,
            end_seconds=row.end_seconds,
            text=row.reviewed_text,
            speaker=row.speaker.value if row.speaker is not None else None,
        )
        for row in rows
        if row.reviewed_text.strip()
    ]
    return segments, screenshots


def _complete(
    session: Session, server: LlamaServerSession, generation_id: str, request: GenerationInput
) -> str:
    """Send one request and return the answer, stopping the server at once on a cancellation.

    The request runs in a helper thread while this thread looks for a cancellation every
    `_CANCEL_POLL_SECONDS`; the database session is only used here. Stopping the server makes the
    pending request fail, so the helper thread ends before this function raises
    `GenerationCancelled`.
    """
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(server.complete, request, max_tokens=ANSWER_TOKENS)
        while True:
            try:
                return future.result(timeout=_CANCEL_POLL_SECONDS)
            except TimeoutError:
                if _cancellation_requested(session, generation_id):
                    server.stop()
                    raise GenerationCancelled from None


def _group_notes(notes: list[str]) -> list[list[str]]:
    """Pack consecutive notes into groups whose estimated size fits one merge request.

    Every group holds at least two notes when there are two or more, so merging always reduces
    the number of notes and ends.
    """
    groups: list[list[str]] = []
    current: list[str] = []
    tokens = 0
    for note in notes:
        cost = math.ceil(len(note) / _CHARS_PER_TOKEN) + 16
        if current and tokens + cost > MERGE_INPUT_TOKENS:
            groups.append(current)
            current, tokens = [], 0
        current.append(note)
        tokens += cost
    if current:
        groups.append(current)
    if len(notes) > 1 and len(groups) == len(notes):
        groups = [notes[index : index + 2] for index in range(0, len(notes), 2)]
    return groups


def _merge_notes(
    session: Session,
    server: LlamaServerSession,
    generation_id: str,
    output_mode: str,
    notes: list[str],
    language: str | None,
) -> str:
    """Return the document: the only note, or the notes merged in as many rounds as needed.

    A single part already answered with the type's sections, so it is the document and no merge
    request is made.
    """
    while len(notes) > 1:
        merged: list[str] = []
        for group in _group_notes(notes):
            if len(group) == 1:
                merged.append(group[0])
                continue
            request = build_merge_input(output_mode, group, language=language)
            merged.append(_complete(session, server, generation_id, request))
            _raise_if_cancelled(session, generation_id)
        notes = merged
    return notes[0]


def _write_document(
    directory: Path, document: str, option: ModelOption, prompt_version: str
) -> Path:
    """Write `document.md` atomically, led by a comment with the prompt version and file hashes.

    The comment is invisible in rendered Markdown and holds no transcript text.
    """
    lines = [
        "<!--",
        "narrastep generated document",
        f"promptVersion: {prompt_version}",
        f"model: {option.model_id} ({option.repository} at {option.revision})",
        "modelFiles:",
        *[f"  - {file.name} sha256:{file.sha256}" for file in option.files],
        "-->",
        "",
        document.strip(),
        "",
    ]
    target = directory / DOCUMENT_FILE_NAME
    partial = directory / f".{DOCUMENT_FILE_NAME}.partial"
    partial.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    os.replace(partial, target)
    return target


def _other_work_is_running(session: Session, generation_id: str) -> bool:
    """Tell whether processing, an export or another generation is running right now.

    The worker runs one task at a time, so this is false unless that invariant is broken (for
    example a stale row left by a crash that reconciliation has not cleared yet).
    """
    running = (JobStatus.RUNNING, JobStatus.CANCELLING)
    processing = session.scalar(
        select(func.count()).select_from(ProcessingJob).where(ProcessingJob.status.in_(running))
    )
    exports = session.scalar(
        select(func.count())
        .select_from(ProjectExport)
        .where(ProjectExport.status == ExportStatus.RUNNING)
    )
    generations = session.scalar(
        select(func.count())
        .select_from(Generation)
        .where(Generation.status.in_(running), Generation.id != generation_id)
    )
    return bool(processing or exports or generations)


def _claim_generation(
    session: Session, generation_id: str, settings: Settings, celery_task_id: str | None
) -> Generation | None:
    """Atomically claim one queued generation so a late-ack redelivery cannot repeat the work."""
    now = utc_now()
    claimed = session.execute(
        update(Generation)
        .where(
            Generation.id == generation_id,
            Generation.status == JobStatus.QUEUED,
            Generation.cancel_requested.is_(False),
        )
        .values(
            status=JobStatus.RUNNING,
            stage="starting",
            message="Preparing the local generation.",
            started_at=now,
            updated_at=now,
            progress_version=Generation.progress_version + 1,
        )
    )
    if getattr(claimed, "rowcount", 0) != 1:
        session.rollback()
        _interrupt_redelivery(session, generation_id, settings, celery_task_id)
        return None
    session.commit()
    generation = session.get(Generation, generation_id)
    if generation is not None:
        session.refresh(generation)
    return generation


def _interrupt_redelivery(
    session: Session, generation_id: str, settings: Settings, celery_task_id: str | None
) -> None:
    """Settle a generation that its own task finds running again after a worker restart."""
    generation = session.get(Generation, generation_id)
    if (
        generation is None
        or celery_task_id is None
        or generation.celery_task_id != celery_task_id
        or generation.status not in {JobStatus.RUNNING, JobStatus.CANCELLING}
    ):
        return
    _remove_directory(generation_directory(settings, generation.project_id, generation.id))
    if generation.cancel_requested or generation.status == JobStatus.CANCELLING:
        _finish_cancelled(session, generation_id)
    else:
        _fail_generation(
            session,
            generation_id,
            ERROR_INTERRUPTED,
            "The generation was interrupted by a local service restart. Start it again.",
        )


def _record_progress(
    session: Session,
    generation_id: str,
    stage: str,
    percent: int,
    message: str,
    *,
    parts_completed: int | None = None,
) -> None:
    """Persist a monotonic progress snapshot; clients poll it, so nothing else is notified."""
    generation = _load(session, generation_id)
    generation.stage = stage
    generation.progress_percent = max(generation.progress_percent, min(100, percent))
    generation.progress_version += 1
    generation.message = message[:300]
    if parts_completed is not None:
        generation.parts_completed = parts_completed
    generation.updated_at = utc_now()
    session.commit()


def _set_parts_total(session: Session, generation_id: str, total: int) -> None:
    """Persist how many parts the planner produced, so progress can be shown per part."""
    generation = _load(session, generation_id)
    generation.parts_total = total
    generation.parts_completed = 0
    generation.progress_version += 1
    generation.updated_at = utc_now()
    session.commit()


def _cancellation_requested(session: Session, generation_id: str) -> bool:
    """Read the durable cancellation flag, which is the only source of truth."""
    session.expire_all()
    generation = session.get(Generation, generation_id)
    return generation is None or generation.cancel_requested


def _raise_if_cancelled(session: Session, generation_id: str) -> None:
    """Turn the durable cancellation request into control flow at a safe boundary."""
    if _cancellation_requested(session, generation_id):
        raise GenerationCancelled


def _finish_completed(
    session: Session, settings: Settings, generation_id: str, document_path: Path
) -> None:
    """Record the stored document, unless a cancellation arrived in the meantime.

    The update only applies to a `processing` row; when the row became `cancelling` meanwhile the
    cancellation wins and `GenerationCancelled` is raised so the file is removed.
    """
    now = utc_now()
    updated = session.execute(
        update(Generation)
        .where(Generation.id == generation_id, Generation.status == JobStatus.RUNNING)
        .values(
            status=JobStatus.COMPLETED,
            stage="completed",
            message="The document is ready.",
            progress_percent=100,
            progress_version=Generation.progress_version + 1,
            relative_path=document_path.relative_to(settings.data_root).as_posix(),
            size_bytes=document_path.stat().st_size,
            completed_at=now,
            updated_at=now,
        )
    )
    if getattr(updated, "rowcount", 0) != 1:
        session.rollback()
        raise GenerationCancelled
    session.commit()


def _finish_cancelled(session: Session, generation_id: str) -> None:
    """Persist a safe cancellation."""
    generation = session.get(Generation, generation_id)
    if generation is None:
        return
    generation.status = JobStatus.CANCELLED
    generation.stage = "cancelled"
    generation.message = "The generation was cancelled."
    generation.progress_version += 1
    generation.completed_at = utc_now()
    session.commit()


def _fail_generation(session: Session, generation_id: str, code: str, message: str) -> None:
    """Persist a terminal failure with a stable code and a message free of transcript text."""
    generation = session.get(Generation, generation_id)
    if generation is None:
        return
    generation.status = JobStatus.FAILED
    generation.stage = "failed"
    generation.message = "The document could not be generated."
    generation.error_code = code
    generation.error_message = message[:500]
    generation.progress_version += 1
    generation.completed_at = utc_now()
    session.commit()


def _remove_directory(directory: Path | None) -> None:
    """Remove the generation's own folder after a cancellation or failure; never raises."""
    if directory is not None:
        shutil.rmtree(directory, ignore_errors=True)
