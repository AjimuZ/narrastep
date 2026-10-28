"""Bounded multipart upload streaming directly into project-owned storage."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from fastapi import Request
from python_multipart.multipart import MultipartParser, parse_options_header

from app.config import Settings
from app.errors import ApiError
from app.services.workspaces import sanitize_display_filename

MAX_MULTIPART_HEADER_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class StreamedUpload:
    """Describe one completely streamed source file ready for media validation."""

    display_filename: str
    extension: str
    partial_path: Path
    size_bytes: int


class _MultipartUploadWriter:
    """Receive multipart callbacks and write only the single expected file part."""

    def __init__(self, partial_root: Path, token: str, settings: Settings) -> None:
        """Initialize bounded parser state without opening a file before its headers arrive."""
        self.partial_root = partial_root
        self.token = token
        self.settings = settings
        self.header_field = bytearray()
        self.header_value = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.header_bytes = 0
        self.output: BinaryIO | None = None
        self.partial_path: Path | None = None
        self.display_filename: str | None = None
        self.extension: str | None = None
        self.file_size = 0
        self.file_seen = False
        self.active_file_part = False
        self.header_bytes = 0
        self.completed = False

    @property
    def callbacks(self) -> dict[str, object]:
        """Expose the callback mapping expected by python-multipart."""
        return {
            "on_part_begin": self.on_part_begin,
            "on_part_data": self.on_part_data,
            "on_part_end": self.on_part_end,
            "on_header_field": self.on_header_field,
            "on_header_value": self.on_header_value,
            "on_header_end": self.on_header_end,
            "on_headers_finished": self.on_headers_finished,
            "on_end": self.on_end,
        }

    def on_part_begin(self) -> None:
        """Reset per-part headers before parsing the next multipart item."""
        self.headers = {}
        self.header_field.clear()
        self.header_value.clear()
        self.active_file_part = False

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        """Accumulate a possibly fragmented multipart header name."""
        self._reserve_header_bytes(end - start)
        self.header_field.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        """Accumulate a possibly fragmented multipart header value."""
        self._reserve_header_bytes(end - start)
        self.header_value.extend(data[start:end])

    def _reserve_header_bytes(self, additional_bytes: int) -> None:
        """Reject malformed multipart headers before they can consume unbounded memory."""
        self.header_bytes += additional_bytes
        if self.header_bytes > MAX_MULTIPART_HEADER_BYTES:
            raise ApiError("VIDEO_VALIDATION_FAILED", "The upload headers are too large.", 422)

    def on_header_end(self) -> None:
        """Store one completed part header using a case-insensitive byte key."""
        self.headers[bytes(self.header_field).lower()] = bytes(self.header_value)
        self.header_field.clear()
        self.header_value.clear()

    def on_headers_finished(self) -> None:
        """Validate the file part and open its request-owned partial path exclusively."""
        _, options = parse_options_header(self.headers.get(b"content-disposition", b""))
        part_name = options.get(b"name")
        raw_filename = options.get(b"filename")
        if raw_filename is None:
            return
        if part_name != b"file" or self.file_seen:
            raise ApiError(
                "VIDEO_VALIDATION_FAILED", "Exactly one source video must be uploaded.", 422
            )
        display_filename = sanitize_display_filename(raw_filename.decode("utf-8", errors="replace"))
        extension = Path(display_filename).suffix.lower()
        if extension not in self.settings.allowed_video_extensions:
            raise ApiError(
                "UNSUPPORTED_MEDIA_TYPE",
                "The selected file must be an MP4, WebM, MOV, or MKV video.",
                415,
            )
        partial_path = self.partial_root / f".upload-{self.token}.partial{extension}"
        self.output = partial_path.open("xb")
        self.partial_path = partial_path
        self.display_filename = display_filename
        self.extension = extension
        self.file_seen = True
        self.active_file_part = True

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        """Write one bounded file chunk after size and free-space checks."""
        if not self.active_file_part:
            return
        chunk = data[start:end]
        next_size = self.file_size + len(chunk)
        if next_size > self.settings.max_upload_bytes:
            raise ApiError(
                "UPLOAD_TOO_LARGE",
                f"The upload exceeds the configured {self.settings.max_upload_mb} MB limit.",
                413,
            )
        if (
            shutil.disk_usage(self.settings.data_root).free - len(chunk)
            < self.settings.disk_reserve_bytes
        ):
            raise ApiError(
                "INSUFFICIENT_DISK_SPACE",
                "There is not enough free disk space to store this video safely.",
                507,
            )
        if self.output is None:
            raise ApiError("VIDEO_VALIDATION_FAILED", "The upload body is not valid.", 422)
        self.output.write(chunk)
        self.file_size = next_size

    def on_part_end(self) -> None:
        """Flush the completed file part durably before media probing begins."""
        if not self.active_file_part or self.output is None:
            return
        self.output.flush()
        os.fsync(self.output.fileno())
        self.output.close()
        self.output = None
        self.active_file_part = False

    def on_end(self) -> None:
        """Record that the terminating multipart boundary was parsed."""
        self.completed = True

    def finish(self) -> StreamedUpload:
        """Validate final parser state and return the single streamed file."""
        self.close()
        if (
            not self.completed
            or not self.file_seen
            or self.partial_path is None
            or self.display_filename is None
            or self.extension is None
        ):
            raise ApiError("VIDEO_VALIDATION_FAILED", "The upload body is not valid.", 422)
        if self.file_size == 0:
            raise ApiError("VIDEO_VALIDATION_FAILED", "The uploaded video is empty.", 422)
        return StreamedUpload(
            display_filename=self.display_filename,
            extension=self.extension,
            partial_path=self.partial_path,
            size_bytes=self.file_size,
        )

    def close(self) -> None:
        """Close an unfinished partial file without changing parser outcome."""
        if self.output is not None:
            self.output.close()
            self.output = None

    def cleanup(self) -> None:
        """Remove only the partial artifact owned by this request."""
        self.close()
        if self.partial_path is not None:
            self.partial_path.unlink(missing_ok=True)


async def stream_single_video_upload(
    request: Request,
    partial_root: Path,
    token: str,
    settings: Settings,
) -> StreamedUpload:
    """Parse one multipart file from the ASGI stream without framework temp spooling."""
    media_type, options = parse_options_header(
        request.headers.get("content-type", "").encode("latin-1", errors="replace")
    )
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or boundary is None:
        raise ApiError("VIDEO_VALIDATION_FAILED", "A multipart video upload is required.", 422)
    body_limit = settings.max_upload_bytes + max(settings.upload_chunk_bytes, 1024 * 1024)
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > body_limit:
                raise ApiError(
                    "UPLOAD_TOO_LARGE",
                    f"The upload exceeds the configured {settings.max_upload_mb} MB limit.",
                    413,
                )
        except ValueError:
            raise ApiError(
                "VIDEO_VALIDATION_FAILED", "The upload length is not valid.", 422
            ) from None

    writer = _MultipartUploadWriter(partial_root, token, settings)
    parser = MultipartParser(boundary, writer.callbacks, max_size=body_limit)  # type: ignore[arg-type]
    received = 0
    try:
        async for chunk in request.stream():
            received += len(chunk)
            if received > body_limit:
                raise ApiError(
                    "UPLOAD_TOO_LARGE",
                    f"The upload exceeds the configured {settings.max_upload_mb} MB limit.",
                    413,
                )
            parser.write(chunk)
        parser.finalize()
        return writer.finish()
    except Exception:
        writer.cleanup()
        raise
