"""Recording completion: join chunks, align and mix the tracks, save a validated source video."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import (
    MediaAsset,
    Project,
    ProjectState,
    RecordingChunk,
    RecordingSession,
    RecordingState,
    RecordingTrack,
    utc_now,
)
from app.services.media import (
    MediaMetadata,
    MediaServiceError,
    StreamSummary,
    probe_streams,
    probe_video,
    run_media_command,
)
from app.services.recordings import (
    chunk_relative_path,
    recording_track_relative_path,
    require_open_recording,
)
from app.services.workspaces import ensure_within_root, sanitize_display_filename

logger = logging.getLogger(__name__)

# FFmpeg opens joined tracks through the `concatf` protocol: a list file names every chunk, so
# no joined copy is written and the command line stays short for recordings of any length.
CONCAT_PROTOCOLS = "concatf,file"
# Codecs the WebM muxer accepts. MediaRecorder may also write H.264 (Chrome's
# `video/webm;codecs=h264`); its video is then copied into Matroska instead of WebM.
WEBM_VIDEO_CODECS = frozenset({"vp8", "vp9", "av1"})
MIXED_AUDIO_BITRATE = "128k"
# The kept microphone and system audio tracks use the same bitrate; speaker labels (P05.5)
# compare their loudness, and both stay on the source video's timeline.
TRACK_AUDIO_BITRATE = "96k"
MICROPHONE_FILENAME = "microphone.webm"
SYSTEM_AUDIO_FILENAME = "system.webm"
# Every temporary file of a completion carries this prefix inside `source/`, which the startup
# reconciliation already removes for interrupted uploads (`.upload-*.partial*`).
PARTIAL_PREFIX = ".upload-"
# Public codes for a written source video that FFprobe refuses; other media codes become
# `VIDEO_VALIDATION_FAILED`, as for uploads.
VALIDATION_ERROR_CODES = {
    "AUDIO_STREAM_MISSING": "VIDEO_AUDIO_TRACK_MISSING",
    "FFPROBE_FAILED": "FFPROBE_FAILED",
}


@dataclass(frozen=True, slots=True)
class TrackPlan:
    """Name the chunks of one track that a completion joins, in sequence order.

    `sequences` is always the contiguous run `0..n-1`; an empty tuple means the track is not
    used. `offset_ms` is the stored start offset, or 0 when the recorder never sent one.
    """

    track: RecordingTrack
    sequences: tuple[int, ...]
    stored_bytes: int
    offset_ms: int

    @property
    def used(self) -> bool:
        """Return true when the track contributes at least its first chunk."""
        return bool(self.sequences)


@dataclass(frozen=True, slots=True)
class CompletionResult:
    """Describe a saved recording: its validated source video and which audio tracks were kept."""

    asset: MediaAsset
    microphone_kept: bool
    system_audio_kept: bool = False


def plan_track(
    track: RecordingTrack,
    chunks: Sequence[tuple[int, int]],
    offset_ms: int | None,
    *,
    stop_at_gap: bool,
) -> TrackPlan:
    """Choose the chunks of one track from its stored `(sequence, size_bytes)` pairs.

    Every stored chunk must form one run starting at 0. With `stop_at_gap` the track keeps only
    the run before its first missing sequence (none when chunk 0 is missing), because a WebM
    stream cannot be decoded reliably across a hole. Without it any missing sequence raises
    `RECORDING_INCOMPLETE` (409) with the track, the first missing sequence and the missing
    count, so the recorder can upload what it still holds and try again.
    """
    ordered = sorted(chunks)
    run: list[int] = []
    run_bytes = 0
    for sequence, size_bytes in ordered:
        if sequence != len(run):
            break
        run.append(sequence)
        run_bytes += size_bytes
    if len(run) != len(ordered) and not stop_at_gap:
        highest = ordered[-1][0]
        raise ApiError(
            "RECORDING_INCOMPLETE",
            "Some parts of this recording were not uploaded yet.",
            409,
            details={
                "track": track.value,
                "firstMissingSequence": len(run),
                "missingCount": highest + 1 - len(ordered),
            },
        )
    return TrackPlan(track, tuple(run), run_bytes, offset_ms or 0)


def microphone_alignment_filter(screen_offset_ms: int, microphone_offset_ms: int) -> str:
    """Return the FFmpeg audio filters that place the microphone on the screen track's timeline.

    The source video starts with the screen track, so the microphone moves by the difference of
    the two start offsets: a later microphone is delayed with silence, an earlier one loses its
    head. `asetpts` first makes the microphone's own first sample time 0, so the result does not
    depend on the timestamps the browser wrote.
    """
    shift_ms = microphone_offset_ms - screen_offset_ms
    filters = ["asetpts=PTS-STARTPTS"]
    if shift_ms > 0:
        filters.append(f"adelay=delays={shift_ms}:all=1")
    elif shift_ms < 0:
        filters.append(f"atrim=start={-shift_ms / 1000:.3f}")
        filters.append("asetpts=PTS-STARTPTS")
    # Fills dropped or late audio frames with silence so the track keeps its timeline.
    filters.append("aresample=async=1")
    return ",".join(filters)


def build_source_command(
    ffmpeg: str,
    screen_input: str,
    microphone_input: str | None,
    *,
    screen_has_audio: bool,
    alignment: str,
    container: str,
    output: Path,
) -> list[str]:
    """Return the argument array that writes the source video from the joined tracks.

    The screen video is copied without re-encoding. With both audio sources the microphone is
    aligned and added to the system audio (`normalize=0` keeps both at their recorded level
    instead of halving them); with one source that source is used alone, and system audio alone is
    copied. Mixed audio is padded with silence and `-shortest` ends the output with the video, so
    a microphone that stops early or runs longer never changes the video's duration. Requires at
    least one audio source.
    """
    if microphone_input is None and not screen_has_audio:
        raise ValueError("A source video needs at least one audio source.")
    command = [ffmpeg, "-nostdin", "-v", "error", "-y"]
    command += ["-protocol_whitelist", CONCAT_PROTOCOLS, "-i", screen_input]
    if microphone_input is not None:
        command += ["-protocol_whitelist", CONCAT_PROTOCOLS, "-i", microphone_input]
    command += ["-map", "0:v:0"]
    if microphone_input is None:
        command += ["-map", "0:a:0", "-c:v", "copy", "-c:a", "copy"]
    else:
        if screen_has_audio:
            graph = (
                f"[0:a:0]aresample=async=1[system];[1:a:0]{alignment}[microphone];"
                "[system][microphone]amix=inputs=2:duration=longest:dropout_transition=0:"
                "normalize=0,apad[audio]"
            )
        else:
            graph = f"[1:a:0]{alignment},apad[audio]"
        command += ["-filter_complex", graph, "-map", "[audio]"]
        command += ["-c:v", "copy", "-c:a", "libopus", "-b:a", MIXED_AUDIO_BITRATE, "-shortest"]
    command += ["-f", container, str(output)]
    return command


def build_microphone_command(
    ffmpeg: str, microphone_input: str, *, alignment: str, output: Path
) -> list[str]:
    """Return the argument array that writes the aligned microphone track as WebM Opus.

    The kept track uses the same alignment as the mix, so time `t` in it is time `t` in the
    source video; speaker labels (P05.5) compare the two without an offset.
    """
    return [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-protocol_whitelist",
        CONCAT_PROTOCOLS,
        "-i",
        microphone_input,
        "-map",
        "0:a:0",
        "-af",
        alignment,
        "-c:a",
        "libopus",
        "-b:a",
        TRACK_AUDIO_BITRATE,
        "-f",
        "webm",
        str(output),
    ]


def build_system_audio_command(ffmpeg: str, screen_input: str, *, output: Path) -> list[str]:
    """Return the argument array that writes the screen track's system audio alone as WebM Opus.

    It uses the same `aresample=async=1` as the mix and starts at the screen track's first
    timestamp, like the video, so time `t` in it is time `t` in the source video. Speaker labels
    (P05.5) compare its loudness with the kept microphone. Requires system audio in the screen
    track.
    """
    return [
        ffmpeg,
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-protocol_whitelist",
        CONCAT_PROTOCOLS,
        "-i",
        screen_input,
        "-map",
        "0:a:0",
        "-af",
        "aresample=async=1",
        "-c:a",
        "libopus",
        "-b:a",
        TRACK_AUDIO_BITRATE,
        "-f",
        "webm",
        str(output),
    ]


def complete_recording(
    session: Session,
    recording: RecordingSession,
    project: Project,
    *,
    name: str | None,
    stop_at_gap: bool,
    workspace: Path,
    token: str,
    settings: Settings,
) -> CompletionResult:
    """Turn an open recording into the project's validated source video and save the session.

    Requires the caller to hold the project operation lock, an open session and a `draft`
    project without a video. Steps: plan both tracks from SQLite (a screen chunk 0 is required),
    check free disk space for a copy of the stored bytes, join each track's chunks with FFmpeg's
    `concatf` protocol, probe them, write `source/source.webm` (`.mkv` for non-WebM video) with
    the aligned mix, `source/microphone.webm` with the aligned microphone and, when both audio
    sources exist, `source/system.webm` with the system audio alone (for speaker labels), validate
    the source video with the upload rules, then commit in one transaction: the session becomes
    `completed`, its chunk rows are deleted, the `MediaAsset` is created and the project becomes
    `uploaded` (renamed when `name` is sent). No processing job is created. After the commit
    the chunk files are removed.

    Raises `RECORDING_INCOMPLETE` (409) for missing chunks or chunks that arrived meanwhile,
    `INVALID_PROJECT_STATE` / `SOURCE_VIDEO_ALREADY_EXISTS` (409) for a saved session or a
    project that already has a video, `INSUFFICIENT_DISK_SPACE` (507),
    `VIDEO_VALIDATION_FAILED`, `VIDEO_AUDIO_TRACK_MISSING` or `FFPROBE_FAILED` (422) for media
    that cannot form a source video, and `FFMPEG_FAILED` (500) when FFmpeg fails. On any failure
    the session stays open with all its chunks, and no output file is left behind.
    """
    require_open_recording(recording)
    if project.media_asset is not None:
        raise ApiError(
            "SOURCE_VIDEO_ALREADY_EXISTS", "This project already has a source video.", 409
        )
    screen, microphone = _plan_tracks(session, recording, stop_at_gap=stop_at_gap)
    stored_chunks = _chunk_count(session, recording.id)
    _require_disk_space(settings, screen.stored_bytes + microphone.stored_bytes)
    # Ends the read transaction so chunk uploads are not blocked while FFmpeg runs.
    session.commit()

    source_dir = workspace / "source"
    source_dir.mkdir(parents=True, exist_ok=True)
    temporary: list[Path] = []
    promoted: list[Path] = []
    try:
        screen_input = _write_chunk_list(settings, source_dir, token, recording.project_id, screen)
        temporary.append(screen_input)
        screen_streams = _probe_track(settings, screen_input)
        if screen_streams.video_codec is None:
            raise ApiError(
                "VIDEO_VALIDATION_FAILED", "The screen recording has no video stream.", 422
            )
        microphone_input: Path | None = None
        if microphone.used:
            microphone_input = _write_chunk_list(
                settings, source_dir, token, recording.project_id, microphone
            )
            temporary.append(microphone_input)
            if _probe_track(settings, microphone_input).audio_codec is None:
                raise ApiError(
                    "VIDEO_VALIDATION_FAILED", "The microphone recording has no audio.", 422
                )
        screen_has_audio = screen_streams.audio_codec is not None
        if microphone_input is None and not screen_has_audio:
            raise ApiError(
                "VIDEO_AUDIO_TRACK_MISSING",
                "The recording has neither system audio nor a microphone track.",
                422,
            )

        extension, container = (
            (".webm", "webm")
            if screen_streams.video_codec in WEBM_VIDEO_CODECS
            else (".mkv", "matroska")
        )
        alignment = microphone_alignment_filter(screen.offset_ms, microphone.offset_ms)
        source_partial = source_dir / f"{PARTIAL_PREFIX}{token}.partial{extension}"
        temporary.append(source_partial)
        _run_ffmpeg(
            settings,
            build_source_command(
                settings.ffmpeg_path,
                _concat_url(screen_input),
                _concat_url(microphone_input) if microphone_input else None,
                screen_has_audio=screen_has_audio,
                alignment=alignment,
                container=container,
                output=source_partial,
            ),
        )
        microphone_partial: Path | None = None
        if microphone_input is not None:
            microphone_partial = source_dir / f"{PARTIAL_PREFIX}{token}.partial-microphone.webm"
            temporary.append(microphone_partial)
            _run_ffmpeg(
                settings,
                build_microphone_command(
                    settings.ffmpeg_path,
                    _concat_url(microphone_input),
                    alignment=alignment,
                    output=microphone_partial,
                ),
            )
        # The system audio alone is only needed to tell the microphone apart from the call, so it
        # is kept only when both sources exist.
        system_partial: Path | None = None
        if microphone_input is not None and screen_has_audio:
            system_partial = source_dir / f"{PARTIAL_PREFIX}{token}.partial-system.webm"
            temporary.append(system_partial)
            _run_ffmpeg(
                settings,
                build_system_audio_command(
                    settings.ffmpeg_path, _concat_url(screen_input), output=system_partial
                ),
            )
        metadata = _validate_source(settings, source_partial)

        project_name = name or project.name
        stored_filename = f"source{extension}"
        destination = source_dir / stored_filename
        microphone_destination = source_dir / MICROPHONE_FILENAME
        system_destination = source_dir / SYSTEM_AUDIO_FILENAME
        _lock_for_completion(session, recording.id)
        if _chunk_count(session, recording.id) != stored_chunks:
            raise ApiError(
                "RECORDING_INCOMPLETE",
                "New parts of this recording arrived while it was being saved. Save it again.",
                409,
            )
        session.execute(delete(RecordingChunk).where(RecordingChunk.recording_id == recording.id))
        asset = MediaAsset(
            project_id=project.id,
            display_filename=sanitize_display_filename(f"{project_name}{extension}"),
            stored_filename=stored_filename,
            relative_path=destination.relative_to(settings.data_root).as_posix(),
            content_type=metadata.content_type,
            size_bytes=source_partial.stat().st_size,
            duration_seconds=metadata.duration_seconds,
            width=metadata.width,
            height=metadata.height,
            container_format=metadata.format_name,
            metadata_json={
                **metadata.to_dict(),
                "recording": {
                    "screenChunks": len(screen.sequences),
                    "microphoneChunks": len(microphone.sequences),
                    # Shift applied to the microphone; null when no microphone was used.
                    "microphoneShiftMs": (
                        microphone.offset_ms - screen.offset_ms if microphone.used else None
                    ),
                    "systemAudio": screen_has_audio,
                    "microphoneTrack": microphone_partial is not None,
                    # True when `source/system.webm` holds the system audio alone (P05.5).
                    "systemTrack": system_partial is not None,
                },
            },
        )
        session.add(asset)
        project.name = project_name
        project.state = ProjectState.UPLOADED
        session.flush()
        os.replace(source_partial, destination)
        promoted.append(destination)
        if microphone_partial is not None:
            os.replace(microphone_partial, microphone_destination)
            promoted.append(microphone_destination)
        if system_partial is not None:
            os.replace(system_partial, system_destination)
            promoted.append(system_destination)
        session.commit()
    except Exception:
        session.rollback()
        for path in promoted:
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)
    _remove_chunk_storage(settings.data_root, recording.project_id)
    session.refresh(asset)
    return CompletionResult(
        asset=asset,
        microphone_kept=microphone_partial is not None,
        system_audio_kept=system_partial is not None,
    )


def _plan_tracks(
    session: Session, recording: RecordingSession, *, stop_at_gap: bool
) -> tuple[TrackPlan, TrackPlan]:
    """Plan the screen and microphone tracks; raises `RECORDING_INCOMPLETE` without screen video."""
    rows = session.execute(
        select(RecordingChunk.track, RecordingChunk.sequence, RecordingChunk.size_bytes).where(
            RecordingChunk.recording_id == recording.id
        )
    ).all()
    plans = {
        track: plan_track(
            track,
            [(row.sequence, row.size_bytes) for row in rows if row.track == track],
            (
                recording.screen_start_offset_ms
                if track == RecordingTrack.SCREEN
                else recording.microphone_start_offset_ms
            ),
            stop_at_gap=stop_at_gap,
        )
        for track in RecordingTrack
    }
    screen = plans[RecordingTrack.SCREEN]
    if not screen.used:
        raise ApiError(
            "RECORDING_INCOMPLETE",
            "The start of the screen recording was not uploaded.",
            409,
            details={"track": RecordingTrack.SCREEN.value, "firstMissingSequence": 0},
        )
    return screen, plans[RecordingTrack.MICROPHONE]


def _chunk_count(session: Session, recording_id: str) -> int:
    """Return how many chunk rows the session has, to detect uploads during a completion."""
    return int(
        session.scalar(
            select(func.count())
            .select_from(RecordingChunk)
            .where(RecordingChunk.recording_id == recording_id)
        )
        or 0
    )


def _require_disk_space(settings: Settings, needed_bytes: int) -> None:
    """Refuse a completion whose outputs, about the size of the chunks, would cross the reserve."""
    if shutil.disk_usage(settings.data_root).free - needed_bytes < settings.disk_reserve_bytes:
        raise ApiError(
            "INSUFFICIENT_DISK_SPACE",
            "There is not enough free disk space to save this recording safely.",
            507,
        )


def _write_chunk_list(
    settings: Settings, source_dir: Path, token: str, project_id: str, plan: TrackPlan
) -> Path:
    """Write the `concatf` list of one track: one `file:` URL per chunk, in sequence order.

    Paths are built from the validated project ID and sequence numbers only. The `file:` prefix
    stops FFmpeg from reading a protocol name out of the data root path.
    """
    path = source_dir / f"{PARTIAL_PREFIX}{token}.partial-{plan.track.value}.txt"
    chunk_paths = (
        ensure_within_root(
            settings.data_root, chunk_relative_path(project_id, plan.track, sequence)
        )
        for sequence in plan.sequences
    )
    lines = [f"file:{chunk_path}" for chunk_path in chunk_paths]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _concat_url(list_path: Path) -> str:
    """Return the FFmpeg input URL that reads every chunk named in `list_path` as one stream."""
    return f"concatf:{list_path}"


def _probe_track(settings: Settings, list_path: Path) -> StreamSummary:
    """Probe a joined track; FFprobe failures become `VIDEO_VALIDATION_FAILED` (422)."""
    try:
        return probe_streams(
            _concat_url(list_path), config=settings, protocol_whitelist=CONCAT_PROTOCOLS
        )
    except MediaServiceError as error:
        raise ApiError(
            "VIDEO_VALIDATION_FAILED", "The recording could not be read as WebM media.", 422
        ) from error


def _run_ffmpeg(settings: Settings, command: list[str]) -> None:
    """Run one completion command; failures and timeouts become `FFMPEG_FAILED` (500)."""
    try:
        run_media_command(
            command,
            timeout_seconds=float(settings.ffmpeg_timeout_seconds),
            failure_code="FFMPEG_FAILED",
            failure_message="The recording could not be converted into a video.",
        )
    except MediaServiceError as error:
        # Only the error class is logged: FFmpeg diagnostics may contain local paths.
        logger.error("Recording conversion failed (%s).", error.code)
        raise ApiError("FFMPEG_FAILED", error.message, 500) from error


def _validate_source(settings: Settings, path: Path) -> MediaMetadata:
    """Validate the written source video with the same FFprobe rules as an uploaded video."""
    try:
        return probe_video(path, config=settings)
    except MediaServiceError as error:
        code = VALIDATION_ERROR_CODES.get(error.code, "VIDEO_VALIDATION_FAILED")
        raise ApiError(code, "The saved recording did not pass video validation.", 422) from error


def _lock_for_completion(session: Session, recording_id: str) -> None:
    """Mark the session completed as the transaction's first write, taking SQLite's write lock.

    A chunk upload locks the same row with `state = recording`, so after this statement no
    upload can commit, and an upload that committed during the conversion is visible to the
    count that follows. Raises `INVALID_PROJECT_STATE` when the session was saved meanwhile and
    `RECORDING_NOT_FOUND` when it was removed.
    """
    now = utc_now()
    result = session.execute(
        update(RecordingSession)
        .where(
            RecordingSession.id == recording_id,
            RecordingSession.state == RecordingState.RECORDING,
        )
        .values(state=RecordingState.COMPLETED, updated_at=now)
        .execution_options(synchronize_session=False)
    )
    if getattr(result, "rowcount", 0) == 1:
        return
    exists = session.scalar(select(RecordingSession.id).where(RecordingSession.id == recording_id))
    if exists is None:
        raise ApiError("RECORDING_NOT_FOUND", "The recording was not found.", 404)
    raise ApiError(
        "INVALID_PROJECT_STATE", "This recording is already saved and cannot change.", 409
    )


def _remove_chunk_storage(data_root: Path, project_id: str) -> None:
    """Delete the chunk folders of a saved recording; failures are logged and left to startup.

    The chunk rows are already gone, so the startup reconciliation removes any chunk file this
    step leaves behind.
    """
    try:
        recording_root = ensure_within_root(
            data_root, recording_track_relative_path(project_id, RecordingTrack.SCREEN).parent
        )
        shutil.rmtree(recording_root, ignore_errors=False)
    except FileNotFoundError:
        return
    except (OSError, ApiError) as error:
        logger.error("Chunk cleanup failed for project %s (%s).", project_id, type(error).__name__)
