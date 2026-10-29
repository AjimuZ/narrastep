"""Recording sessions: per-track chunk storage, resume summaries and startup cleanup (ADR 0010)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Request
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import RecordingChunk, RecordingSession, RecordingState, RecordingTrack, utc_now
from app.services.workspaces import (
    create_project_workspace,
    ensure_within_root,
    project_relative_path,
)

logger = logging.getLogger(__name__)

# Six digits in the file name keep chunks in sequence order when listed; at 5 s per chunk the
# highest sequence covers about 58 days of recording.
MAX_CHUNK_SEQUENCE = 999_999
# Offsets above one day cannot come from a real recorder and are rejected as invalid input.
MAX_START_OFFSET_MS = 24 * 60 * 60 * 1000
MISSING_SEQUENCE_LIMIT = 100
CHUNK_FILE_PATTERN = re.compile(r"(\d{6})\.webm")
PARTIAL_PREFIX = ".chunk-"
PARTIAL_SUFFIX = ".partial"
# Every WebM file starts with the EBML magic number. Only the first chunk of a MediaRecorder
# stream carries the header; later chunks continue its clusters and cannot be probed alone, so
# FFprobe validation happens on the joined track at completion (P05.2).
EBML_MAGIC = b"\x1a\x45\xdf\xa3"
ACCEPTED_CHUNK_MEDIA_TYPES = frozenset({"video/webm", "audio/webm", "application/octet-stream"})


@dataclass(frozen=True, slots=True)
class StoredChunk:
    """Describe the outcome of one chunk upload; `duplicate` means nothing new was stored."""

    track: RecordingTrack
    sequence: int
    size_bytes: int
    duplicate: bool


def recording_track_relative_path(project_id: str, track: RecordingTrack) -> Path:
    """Return `projects/{project_id}/recording/{track}`; raises `INVALID_PROJECT_ID` on a bad ID."""
    return project_relative_path(project_id) / "recording" / RecordingTrack(track).value


def chunk_relative_path(project_id: str, track: RecordingTrack, sequence: int) -> Path:
    """Return the data-root-relative path of one chunk file.

    Requires `0 <= sequence <= MAX_CHUNK_SEQUENCE`; raises `ValueError` otherwise. The path is
    built only from a validated project UUID, a track enum value and an integer, so no user text
    reaches the filesystem.
    """
    if not 0 <= sequence <= MAX_CHUNK_SEQUENCE:
        raise ValueError("Chunk sequence is out of range.")
    return recording_track_relative_path(project_id, track) / f"{sequence:06d}.webm"


def create_recording_storage(data_root: Path, project_id: str) -> Path:
    """Create the project workspace plus one chunk directory per track; returns the workspace."""
    workspace = create_project_workspace(data_root, project_id)
    for track in RecordingTrack:
        ensure_within_root(data_root, recording_track_relative_path(project_id, track)).mkdir(
            parents=True, exist_ok=True
        )
    return workspace


def default_recording_name(now: datetime | None = None) -> str:
    """Return `Recording {YYYY-MM-DD HH:MM}` in the server's local time zone."""
    moment = (now or datetime.now(UTC)).astimezone()
    return f"Recording {moment:%Y-%m-%d %H:%M}"


def is_interrupted(
    recording: RecordingSession, settings: Settings, now: datetime | None = None
) -> bool:
    """Return true when an open session has received no chunk for the configured time.

    Activity is the last stored chunk, or the creation time before the first one. A paused
    recorder emits no chunks, so a long pause also reads as interrupted; the recorder screen that
    owns the session knows better and does not rely on this flag. Completed sessions are never
    interrupted.
    """
    if recording.state != RecordingState.RECORDING:
        return False
    last_activity = _aware(recording.last_chunk_at or recording.created_at)
    threshold = timedelta(seconds=settings.recording_interrupted_after_seconds)
    return (now or utc_now()) - last_activity > threshold


def project_recording_payload(
    recording: RecordingSession | None, settings: Settings
) -> dict[str, object] | None:
    """Return the short recording summary embedded in project responses, or null."""
    if recording is None:
        return None
    return {
        "id": recording.id,
        "state": recording.state.value,
        "interrupted": is_interrupted(recording, settings),
    }


def recording_payload(
    session: Session, recording: RecordingSession, settings: Settings
) -> dict[str, object]:
    """Return the full session state with one summary per track, both tracks always listed."""
    rows = session.execute(
        select(RecordingChunk.track, RecordingChunk.sequence, RecordingChunk.size_bytes)
        .where(RecordingChunk.recording_id == recording.id)
        .order_by(RecordingChunk.track, RecordingChunk.sequence)
    ).all()
    tracks = []
    for track in RecordingTrack:
        track_rows = [row for row in rows if row.track == track]
        tracks.append(
            _track_summary(
                track,
                [row.sequence for row in track_rows],
                sum(row.size_bytes for row in track_rows),
                _start_offset(recording, track),
            )
        )
    return {
        "id": recording.id,
        "projectId": recording.project_id,
        "state": recording.state.value,
        "interrupted": is_interrupted(recording, settings),
        "storedBytes": sum(row.size_bytes for row in rows),
        "maxBytes": settings.max_upload_bytes,
        "createdAt": recording.created_at,
        "updatedAt": recording.updated_at,
        "lastChunkAt": recording.last_chunk_at,
        "tracks": tracks,
    }


def require_open_recording(recording: RecordingSession) -> None:
    """Raise `INVALID_PROJECT_STATE` (409) unless the session still accepts chunks or discard."""
    if recording.state != RecordingState.RECORDING:
        raise ApiError(
            "INVALID_PROJECT_STATE", "This recording is already saved and cannot change.", 409
        )


async def store_recording_chunk(
    request: Request,
    session: Session,
    recording: RecordingSession,
    track: RecordingTrack,
    sequence: int,
    start_offset_ms: int | None,
    settings: Settings,
) -> StoredChunk:
    """Stream one chunk body to a private partial file and store it atomically.

    Requires an open session, a WebM or octet-stream `Content-Type`, a non-empty body no larger
    than `NARRASTEP_RECORDING_MAX_CHUNK_MB`, and for sequence 0 the WebM header. A sequence that is
    already stored with the same bytes is a no-op (`duplicate=True`); different bytes, or a start
    offset other than the one stored for the track, raise `RECORDING_CHUNK_CONFLICT`. Raises
    `UPLOAD_TOO_LARGE` when the chunk or the whole session would exceed its limit,
    `INSUFFICIENT_DISK_SPACE` when the free-space reserve would be crossed, and
    `RECORDING_NOT_FOUND` when the session disappears meanwhile. On any failure
    nothing new is left on disk or in SQLite.
    """
    require_open_recording(recording)
    _require_chunk_media_type(request)
    _reject_declared_oversize(request, settings)
    recording_id = recording.id
    final_path = ensure_within_root(
        settings.data_root, chunk_relative_path(recording.project_id, track, sequence)
    )
    directory = final_path.parent
    # Ends the implicit read so no stale snapshot is held while the body streams.
    session.commit()
    if not directory.is_dir():
        # Never recreate it: a missing directory means the session was discarded meanwhile or
        # the workspace was removed by hand; either way the chunk has nowhere safe to go.
        raise ApiError("PROJECT_ASSET_MISSING", "The recording storage is unavailable.", 409)
    partial_path = directory / f"{PARTIAL_PREFIX}{uuid.uuid4().hex}{PARTIAL_SUFFIX}"
    try:
        size_bytes, digest = await _stream_chunk(request, partial_path, sequence, settings)
        return _commit_chunk(
            session,
            recording_id,
            track,
            sequence,
            size_bytes,
            digest,
            start_offset_ms,
            partial_path,
            final_path,
            settings,
        )
    finally:
        partial_path.unlink(missing_ok=True)


def reconcile_recording_storage(session: Session, data_root: Path) -> None:
    """Remove partial chunk files and chunk files that SQLite does not list, after a restart.

    A chunk file without a row comes from a write interrupted between the rename and the commit;
    the recorder still holds that chunk and uploads it again. Only names this module writes are
    touched. Failures are logged without paths or content and never stop the startup.
    """
    recordings = session.execute(select(RecordingSession.id, RecordingSession.project_id)).all()
    removed = 0
    for recording_id, project_id in recordings:
        stored = {
            (track, sequence)
            for track, sequence in session.execute(
                select(RecordingChunk.track, RecordingChunk.sequence).where(
                    RecordingChunk.recording_id == recording_id
                )
            )
        }
        try:
            for track in RecordingTrack:
                directory = ensure_within_root(
                    data_root, recording_track_relative_path(project_id, track)
                )
                if not directory.is_dir():
                    continue
                for entry in directory.iterdir():
                    match = CHUNK_FILE_PATTERN.fullmatch(entry.name)
                    is_partial = entry.name.startswith(PARTIAL_PREFIX) and entry.name.endswith(
                        PARTIAL_SUFFIX
                    )
                    is_orphan = match is not None and (track, int(match.group(1))) not in stored
                    if entry.is_file() and (is_partial or is_orphan):
                        entry.unlink(missing_ok=True)
                        removed += 1
        except (OSError, ApiError) as error:
            logger.error(
                "Recording storage recovery failed for recording %s (%s).",
                recording_id,
                type(error).__name__,
            )
    if removed:
        logger.info("Recording storage recovery removed %d incomplete chunk files.", removed)


def _commit_chunk(
    session: Session,
    recording_id: str,
    track: RecordingTrack,
    sequence: int,
    size_bytes: int,
    digest: str,
    start_offset_ms: int | None,
    partial_path: Path,
    final_path: Path,
    settings: Settings,
) -> StoredChunk:
    """Record a streamed chunk in SQLite and move its file into place in one transaction.

    The first statement updates the session row and so takes SQLite's write lock: concurrent
    uploads (both tracks, or the same sequence twice) are serialized here, and the state check,
    the offset check, the duplicate lookup and the session total all see every committed chunk.
    The file is renamed only after the row is flushed and is removed again if the commit fails,
    so a file without a row can only remain after a crash; startup removes it.
    """
    moved = False
    try:
        _lock_open_recording(session, recording_id)
        _apply_start_offset(session, recording_id, track, start_offset_ms)
        existing = session.scalar(
            select(RecordingChunk).where(
                RecordingChunk.recording_id == recording_id,
                RecordingChunk.track == track,
                RecordingChunk.sequence == sequence,
            )
        )
        if existing is not None:
            if existing.sha256 != digest or existing.size_bytes != size_bytes:
                raise ApiError(
                    "RECORDING_CHUNK_CONFLICT",
                    "This chunk was already stored with different content.",
                    409,
                )
            session.commit()
            return StoredChunk(track, sequence, size_bytes, duplicate=True)
        session.add(
            RecordingChunk(
                recording_id=recording_id,
                track=track,
                sequence=sequence,
                size_bytes=size_bytes,
                sha256=digest,
            )
        )
        session.flush()
        total = session.scalar(
            select(func.coalesce(func.sum(RecordingChunk.size_bytes), 0)).where(
                RecordingChunk.recording_id == recording_id
            )
        )
        if int(total or 0) > settings.max_upload_bytes:
            raise _session_too_large(settings)
        try:
            os.replace(partial_path, final_path)
        except FileNotFoundError as error:
            # A concurrent discard staged the workspace for removal while the body streamed.
            raise _recording_not_found() from error
        moved = True
        session.commit()
    except Exception:
        session.rollback()
        if moved:
            final_path.unlink(missing_ok=True)
        raise
    return StoredChunk(track, sequence, size_bytes, duplicate=False)


def _lock_open_recording(session: Session, recording_id: str) -> None:
    """Touch the session's activity time, which takes the write lock and proves it is open.

    Raises `RECORDING_NOT_FOUND` when the row is gone and `INVALID_PROJECT_STATE` when it is no
    longer recording.
    """
    now = utc_now()
    touched = session.execute(
        update(RecordingSession)
        .where(
            RecordingSession.id == recording_id,
            RecordingSession.state == RecordingState.RECORDING,
        )
        .values(last_chunk_at=now, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if getattr(touched, "rowcount", 0) == 1:
        return
    state = session.scalar(
        select(RecordingSession.state).where(RecordingSession.id == recording_id)
    )
    if state is None:
        raise _recording_not_found()
    raise ApiError(
        "INVALID_PROJECT_STATE", "This recording is already saved and cannot change.", 409
    )


def _apply_start_offset(
    session: Session, recording_id: str, track: RecordingTrack, start_offset_ms: int | None
) -> None:
    """Store a track's start offset the first time it is sent; a different later value conflicts."""
    if start_offset_ms is None:
        return
    column = (
        RecordingSession.screen_start_offset_ms
        if track == RecordingTrack.SCREEN
        else RecordingSession.microphone_start_offset_ms
    )
    session.execute(
        update(RecordingSession)
        .where(RecordingSession.id == recording_id, column.is_(None))
        .values({column: start_offset_ms})
        .execution_options(synchronize_session=False)
    )
    stored = session.scalar(select(column).where(RecordingSession.id == recording_id))
    if stored != start_offset_ms:
        raise ApiError(
            "RECORDING_CHUNK_CONFLICT",
            "This track already has a different start offset.",
            409,
        )


async def _stream_chunk(
    request: Request, partial_path: Path, sequence: int, settings: Settings
) -> tuple[int, str]:
    """Write the request body to `partial_path` and return its size and SHA-256 hex digest.

    Enforces the chunk limit and disk reserve on every piece, fsyncs before returning, and
    rejects an empty body or a first chunk without the WebM header. The caller removes the
    partial file.
    """
    digest = hashlib.sha256()
    size_bytes = 0
    header = b""
    with partial_path.open("xb") as output:
        async for piece in request.stream():
            if not piece:
                continue
            size_bytes += len(piece)
            if size_bytes > settings.recording_max_chunk_bytes:
                raise _chunk_too_large(settings)
            if (
                shutil.disk_usage(settings.data_root).free - len(piece)
                < settings.disk_reserve_bytes
            ):
                raise ApiError(
                    "INSUFFICIENT_DISK_SPACE",
                    "There is not enough free disk space to store this recording safely.",
                    507,
                )
            if len(header) < len(EBML_MAGIC):
                header += piece[: len(EBML_MAGIC) - len(header)]
            digest.update(piece)
            output.write(piece)
        output.flush()
        os.fsync(output.fileno())
    if size_bytes == 0:
        raise ApiError("VIDEO_VALIDATION_FAILED", "The recording chunk is empty.", 422)
    if sequence == 0 and header != EBML_MAGIC:
        raise ApiError("UNSUPPORTED_MEDIA_TYPE", "A recording must start with a WebM header.", 415)
    return size_bytes, digest.hexdigest()


def _require_chunk_media_type(request: Request) -> None:
    """Accept WebM or octet-stream bodies, with or without codec parameters."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in ACCEPTED_CHUNK_MEDIA_TYPES:
        raise ApiError(
            "UNSUPPORTED_MEDIA_TYPE",
            "Recording chunks must be sent as video/webm or audio/webm.",
            415,
        )


def _reject_declared_oversize(request: Request, settings: Settings) -> None:
    """Refuse a body whose declared length already exceeds the chunk limit, before reading it."""
    content_length = request.headers.get("content-length")
    if content_length is None:
        return
    try:
        declared = int(content_length)
    except ValueError:
        raise ApiError("VIDEO_VALIDATION_FAILED", "The chunk length is not valid.", 422) from None
    if declared > settings.recording_max_chunk_bytes:
        raise _chunk_too_large(settings)


def _track_summary(
    track: RecordingTrack, sequences: list[int], stored_bytes: int, start_offset_ms: int | None
) -> dict[str, object]:
    """Summarize ascending, unique `sequences` of one track for resume."""
    missing: list[int] = []
    missing_count = 0
    expected = 0
    for sequence in sequences:
        gap = sequence - expected
        if gap > 0:
            missing_count += gap
            room = MISSING_SEQUENCE_LIMIT - len(missing)
            missing.extend(range(expected, expected + min(gap, max(room, 0))))
        expected = sequence + 1
    return {
        "track": track.value,
        "chunkCount": len(sequences),
        "storedBytes": stored_bytes,
        "nextSequence": expected,
        "missingSequences": missing,
        "missingCount": missing_count,
        "startOffsetMs": start_offset_ms,
    }


def _start_offset(recording: RecordingSession, track: RecordingTrack) -> int | None:
    """Return the stored start offset of one track."""
    if track == RecordingTrack.SCREEN:
        return recording.screen_start_offset_ms
    return recording.microphone_start_offset_ms


def _aware(value: datetime) -> datetime:
    """Treat naive timestamps loaded from SQLite as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _chunk_too_large(settings: Settings) -> ApiError:
    """Build the stable error for a chunk above `NARRASTEP_RECORDING_MAX_CHUNK_MB`."""
    return ApiError(
        "UPLOAD_TOO_LARGE",
        f"A recording chunk exceeds the configured {settings.recording_max_chunk_mb} MB limit.",
        413,
    )


def _session_too_large(settings: Settings) -> ApiError:
    """Build the stable error for a session above the upload limit shared with uploads."""
    return ApiError(
        "UPLOAD_TOO_LARGE",
        f"The recording exceeds the configured {settings.max_upload_mb} MB limit.",
        413,
    )


def _recording_not_found() -> ApiError:
    """Build the stable not-found error for a missing or discarded recording session."""
    return ApiError("RECORDING_NOT_FOUND", "The recording was not found.", 404)
