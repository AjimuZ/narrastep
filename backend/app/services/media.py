"""Safe media inspection and audio extraction helpers for Narrastep."""

from __future__ import annotations

import json
import math
import os
import subprocess
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

SUPPORTED_VIDEO_EXTENSIONS = frozenset({".mp4", ".webm", ".mov", ".mkv"})
VIDEO_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
}


class MediaToolConfig(Protocol):
    """Describe the settings consumed by the media command helpers."""

    @property
    def ffmpeg_path(self) -> str:
        """Return the configured FFmpeg executable path."""

        ...

    @property
    def ffprobe_path(self) -> str:
        """Return the configured FFprobe executable path."""

        ...

    @property
    def ffmpeg_timeout_seconds(self) -> float:
        """Return the maximum FFmpeg execution duration."""

        ...

    @property
    def ffprobe_timeout_seconds(self) -> float:
        """Return the maximum FFprobe execution duration."""

        ...


class MediaServiceError(RuntimeError):
    """Represent a stable media failure without exposing command diagnostics."""

    def __init__(self, code: str, message: str) -> None:
        """Create an error that API services can map to the public error envelope."""

        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class MediaMetadata:
    """Contain the validated metadata needed by processing and API responses."""

    format_name: str
    duration_seconds: float
    size_bytes: int
    width: int
    height: int
    video_codec: str
    audio_codec: str
    frame_rate: float | None
    content_type: str

    def to_dict(self) -> dict[str, str | float | int | None]:
        """Serialize metadata for persistence without leaking a filesystem path."""

        return {
            "formatName": self.format_name,
            "durationSeconds": self.duration_seconds,
            "sizeBytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
            "videoCodec": self.video_codec,
            "audioCodec": self.audio_codec,
            "frameRate": self.frame_rate,
            "contentType": self.content_type,
        }


def run_media_command(
    arguments: Sequence[str],
    *,
    timeout_seconds: float,
    failure_code: str,
    failure_message: str,
) -> subprocess.CompletedProcess[str]:
    """Run one media command as an argument array with a bounded execution time."""

    if isinstance(arguments, (str, bytes)) or not arguments:
        raise ValueError("Media commands require a non-empty argument sequence.")
    if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
        raise ValueError("Media command timeouts must be greater than zero.")

    try:
        completed = subprocess.run(
            [str(argument) for argument in arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
            shell=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaServiceError(failure_code, failure_message) from exc
    except (FileNotFoundError, OSError) as exc:
        raise MediaServiceError(failure_code, failure_message) from exc

    if completed.returncode != 0:
        raise MediaServiceError(failure_code, failure_message)
    return completed


def probe_video(
    source_path: str | Path,
    *,
    config: MediaToolConfig | None = None,
    ffprobe_binary: str | None = None,
    timeout_seconds: float | None = None,
) -> MediaMetadata:
    """Validate a supported source video and return normalized FFprobe metadata."""

    source = Path(source_path)
    extension = _source_video_extension(source)
    if extension not in SUPPORTED_VIDEO_EXTENSIONS:
        raise MediaServiceError(
            "UNSUPPORTED_MEDIA_TYPE",
            "The selected file must be an MP4, WebM, MOV, or MKV video.",
        )
    if not source.is_file():
        raise MediaServiceError("MEDIA_FILE_MISSING", "The source video is unavailable.")

    probe_binary = ffprobe_binary or _first_config_value(
        config, ("ffprobe_path", "ffprobe_binary"), "ffprobe"
    )
    probe_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else _first_config_value(
            config, ("ffprobe_timeout_seconds", "ffmpeg_timeout_seconds"), 30.0
        )
    )
    completed = run_media_command(
        [
            str(probe_binary),
            "-v",
            "error",
            "-show_entries",
            (
                "format=format_name,duration,size:"
                "stream=index,codec_type,codec_name,width,height,r_frame_rate,duration:"
                "stream_disposition=attached_pic"
            ),
            "-of",
            "json",
            str(source),
        ],
        timeout_seconds=float(probe_timeout),
        failure_code="FFPROBE_FAILED",
        failure_message="The video could not be inspected.",
    )

    payload = _parse_probe_payload(completed.stdout)
    format_payload = payload.get("format")
    stream_payloads = payload.get("streams")
    if not isinstance(format_payload, dict) or not isinstance(stream_payloads, list):
        raise MediaServiceError("INVALID_VIDEO", "The selected file is not a valid video.")

    format_name = str(format_payload.get("format_name") or "").lower()
    if not _container_matches_extension(extension, format_name):
        raise MediaServiceError("INVALID_VIDEO", "The selected file is not a valid video.")

    video_stream = _first_stream(stream_payloads, "video")
    if video_stream is None:
        raise MediaServiceError(
            "VIDEO_STREAM_MISSING", "The selected file does not contain a video stream."
        )
    audio_stream = _first_stream(stream_payloads, "audio")
    if audio_stream is None:
        raise MediaServiceError(
            "AUDIO_STREAM_MISSING", "The selected video does not contain an audio track."
        )

    duration = _media_duration(format_payload, stream_payloads)
    width = _positive_int(video_stream.get("width"))
    height = _positive_int(video_stream.get("height"))
    if duration is None or width is None or height is None:
        raise MediaServiceError("INVALID_VIDEO", "The selected file is not a valid video.")

    reported_size = _positive_int(format_payload.get("size"))
    return MediaMetadata(
        format_name=format_name,
        duration_seconds=duration,
        size_bytes=reported_size or source.stat().st_size,
        width=width,
        height=height,
        video_codec=str(video_stream.get("codec_name") or "unknown"),
        audio_codec=str(audio_stream.get("codec_name") or "unknown"),
        frame_rate=_parse_frame_rate(video_stream.get("r_frame_rate")),
        content_type=VIDEO_CONTENT_TYPES[extension],
    )


@dataclass(frozen=True, slots=True)
class StreamSummary:
    """Name the codec of the first video and first audio stream; None when that type is absent."""

    video_codec: str | None
    audio_codec: str | None


def probe_streams(
    source: str | Path,
    *,
    config: MediaToolConfig | None = None,
    protocol_whitelist: str | None = None,
) -> StreamSummary:
    """Return which stream types a media input holds, without the source-video rules.

    `source` may be a path or an FFmpeg URL such as `concatf:` when `protocol_whitelist` names
    the protocols it may open. Unlike `probe_video` it accepts audio-only and video-only inputs
    and needs no duration, so it can inspect joined recording tracks, which carry none. Raises
    `MediaServiceError` with `FFPROBE_FAILED` when FFprobe fails, times out or prints no JSON.
    """

    probe_binary = _first_config_value(config, ("ffprobe_path", "ffprobe_binary"), "ffprobe")
    probe_timeout = _first_config_value(
        config, ("ffprobe_timeout_seconds", "ffmpeg_timeout_seconds"), 30.0
    )
    whitelist = ["-protocol_whitelist", protocol_whitelist] if protocol_whitelist else []
    completed = run_media_command(
        [
            str(probe_binary),
            "-v",
            "error",
            *whitelist,
            "-show_entries",
            "stream=index,codec_type,codec_name:stream_disposition=attached_pic",
            "-of",
            "json",
            str(source),
        ],
        timeout_seconds=float(probe_timeout),
        failure_code="FFPROBE_FAILED",
        failure_message="The recording could not be inspected.",
    )
    streams = _parse_probe_payload(completed.stdout).get("streams")
    if not isinstance(streams, list):
        streams = []
    video = _first_stream(streams, "video")
    audio = _first_stream(streams, "audio")
    return StreamSummary(
        video_codec=str(video.get("codec_name") or "unknown") if video else None,
        audio_codec=str(audio.get("codec_name") or "unknown") if audio else None,
    )


def validate_video(
    source_path: str | Path,
    *,
    config: MediaToolConfig | None = None,
    ffprobe_binary: str | None = None,
    timeout_seconds: float | None = None,
) -> MediaMetadata:
    """Expose source validation under the service name used by processing flows."""

    return probe_video(
        source_path,
        config=config,
        ffprobe_binary=ffprobe_binary,
        timeout_seconds=timeout_seconds,
    )


def extract_pcm_audio(
    source_path: str | Path,
    output_path: str | Path,
    *,
    config: MediaToolConfig | None = None,
    ffmpeg_binary: str | None = None,
    timeout_seconds: float | None = None,
    sample_rate_hz: int = 16_000,
) -> Path:
    """Extract the first audio stream as atomic mono 16-bit PCM WAV output."""

    source = Path(source_path)
    destination = Path(output_path)
    if not source.is_file():
        raise MediaServiceError("MEDIA_FILE_MISSING", "The source video is unavailable.")
    if sample_rate_hz <= 0:
        raise ValueError("The audio sample rate must be greater than zero.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    binary = ffmpeg_binary or _first_config_value(
        config, ("ffmpeg_path", "ffmpeg_binary"), "ffmpeg"
    )
    command_timeout = (
        timeout_seconds
        if timeout_seconds is not None
        else _config_value(config, "ffmpeg_timeout_seconds", 3_600.0)
    )

    try:
        run_media_command(
            [
                str(binary),
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ac",
                "1",
                "-ar",
                str(sample_rate_hz),
                "-f",
                "wav",
                str(partial),
            ],
            timeout_seconds=float(command_timeout),
            failure_code="FFMPEG_FAILED",
            failure_message="The video audio could not be prepared for transcription.",
        )
        if not partial.is_file() or partial.stat().st_size <= 44:
            raise MediaServiceError(
                "FFMPEG_FAILED",
                "The video audio could not be prepared for transcription.",
            )
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)

    return destination


def _config_value(config: object | None, name: str, default: Any) -> Any:
    """Read an optional settings value while preserving standalone defaults."""

    return getattr(config, name, default) if config is not None else default


def _first_config_value(config: object | None, names: Sequence[str], default: Any) -> Any:
    """Read the first available settings alias for cross-environment compatibility."""

    if config is not None:
        for name in names:
            if hasattr(config, name):
                return getattr(config, name)
    return default


def _parse_probe_payload(raw_payload: str) -> dict[str, Any]:
    """Parse FFprobe JSON and convert malformed output into a stable media error."""

    try:
        payload = json.loads(raw_payload)
    except (json.JSONDecodeError, TypeError) as exc:
        raise MediaServiceError("FFPROBE_FAILED", "The video could not be inspected.") from exc
    if not isinstance(payload, dict):
        raise MediaServiceError("FFPROBE_FAILED", "The video could not be inspected.")
    return payload


def _container_matches_extension(extension: str, format_name: str) -> bool:
    """Ensure FFprobe reports a container compatible with the accepted extension."""

    names = {name.strip() for name in format_name.split(",") if name.strip()}
    if extension in {".mp4", ".mov"}:
        return bool(names.intersection({"mov", "mp4", "m4a", "3gp", "3g2", "mj2"}))
    if extension == ".webm":
        return bool(names.intersection({"webm", "matroska"}))
    if extension == ".mkv":
        return bool(names.intersection({"matroska", "webm"}))
    return False


def _source_video_extension(source: Path) -> str:
    """Resolve the video suffix while allowing the upload-only .partial staging marker."""

    extension = source.suffix.lower()
    if extension == ".partial":
        extension = Path(source.stem).suffix.lower()
    return extension


def _first_stream(streams: list[Any], stream_type: str) -> dict[str, Any] | None:
    """Return the first well-formed stream of the requested media type."""

    for stream in streams:
        disposition = stream.get("disposition", {}) if isinstance(stream, dict) else {}
        is_attached_picture = isinstance(disposition, dict) and disposition.get("attached_pic") == 1
        if (
            isinstance(stream, dict)
            and stream.get("codec_type") == stream_type
            and not is_attached_picture
        ):
            return stream
    return None


def _media_duration(format_payload: dict[str, Any], streams: list[Any]) -> float | None:
    """Resolve duration from the container and fall back to individual streams."""

    duration = _positive_float(format_payload.get("duration"))
    if duration is not None:
        return duration
    stream_durations = [
        value
        for stream in streams
        if isinstance(stream, dict)
        for value in [_positive_float(stream.get("duration"))]
        if value is not None
    ]
    return max(stream_durations, default=None)


def _positive_float(value: Any) -> float | None:
    """Convert a finite positive value to float for trusted metadata use."""

    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0 or not math.isfinite(parsed):
        return None
    return parsed


def _positive_int(value: Any) -> int | None:
    """Convert a positive integer-like value without accepting invalid metadata."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _parse_frame_rate(value: Any) -> float | None:
    """Convert FFprobe rational frame-rate text into a positive float."""

    if value is None:
        return None
    text = str(value)
    try:
        if "/" in text:
            numerator, denominator = text.split("/", maxsplit=1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            rate = float(numerator) / denominator_value
        else:
            rate = float(text)
    except ValueError:
        return None
    return rate if rate > 0 and math.isfinite(rate) else None
