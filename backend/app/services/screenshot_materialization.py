"""Atomic local screenshot thumbnail and PNG materialization."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import cast

import cv2
import numpy as np
from numpy.typing import NDArray
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Screenshot
from app.services.pipeline import PipelineServiceError
from app.services.workspaces import create_project_workspace, ensure_within_root

type Frame = NDArray[np.uint8]


def decode_exact_frame(video_path: Path, timestamp_seconds: float) -> Frame:
    """Decode one exact non-negative source timestamp through an isolated video capture."""
    if timestamp_seconds < 0:
        raise ValueError("A screenshot timestamp cannot be negative.")
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            raise PipelineServiceError(
                "FRAME_EXTRACTION_FAILED", "The source video could not be opened."
            )
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_seconds * 1_000)
        success, frame = capture.read()
        if not success or frame is None or not frame.size:
            raise PipelineServiceError(
                "FRAME_EXTRACTION_FAILED", "The requested source frame is unavailable."
            )
        return cast(Frame, frame)
    finally:
        capture.release()


def write_png_atomic(frame: Frame, destination: Path) -> Path:
    """Encode a full-resolution frame and atomically publish the PNG derivative."""
    return _write_encoded(frame, destination, ".png", [cv2.IMWRITE_PNG_COMPRESSION, 3])


def write_thumbnail_atomic(frame: Frame, destination: Path) -> Path:
    """Encode a bounded 640-pixel WebP thumbnail for normal review rendering."""
    height, width = frame.shape[:2]
    long_edge = max(width, height)
    if long_edge > 640:
        ratio = 640 / long_edge
        frame = cast(
            Frame,
            cv2.resize(
                frame,
                (max(1, round(width * ratio)), max(1, round(height * ratio))),
                interpolation=cv2.INTER_AREA,
            ),
        )
    return _write_encoded(frame, destination, ".webp", [cv2.IMWRITE_WEBP_QUALITY, 82])


def ensure_thumbnail(session: Session, screenshot: Screenshot, settings: Settings) -> Path:
    """Return or lazily create the review thumbnail for legacy PNG-backed evidence."""
    if screenshot.thumbnail_relative_path:
        current = ensure_within_root(settings.data_root, screenshot.thumbnail_relative_path)
        if current.is_file():
            return current
    if screenshot.relative_path:
        source = ensure_within_root(settings.data_root, screenshot.relative_path)
        frame = cast(Frame | None, cv2.imread(str(source), cv2.IMREAD_COLOR))
        if frame is None or not frame.size:
            raise PipelineServiceError("PROJECT_ASSET_MISSING", "The screenshot is unavailable.")
    else:
        frame = _decode_screenshot_source(screenshot, settings)
    workspace = create_project_workspace(settings.data_root, screenshot.project_id)
    destination = workspace / "screenshots" / "thumbnails" / f"{screenshot.id}.webp"
    write_thumbnail_atomic(frame, destination)
    screenshot.thumbnail_relative_path = destination.relative_to(settings.data_root).as_posix()
    session.flush()
    return destination


def materialize_png(session: Session, screenshot: Screenshot, settings: Settings) -> Path:
    """Return or atomically reconstruct one full PNG from its authoritative source video."""
    if screenshot.relative_path:
        current = ensure_within_root(settings.data_root, screenshot.relative_path)
        if current.is_file():
            return current
    frame = _decode_screenshot_source(screenshot, settings)
    workspace = create_project_workspace(settings.data_root, screenshot.project_id)
    destination = workspace / "screenshots" / "full" / f"{screenshot.id}.png"
    write_png_atomic(frame, destination)
    screenshot.relative_path = destination.relative_to(settings.data_root).as_posix()
    screenshot.materialization_state = "png_ready"
    session.flush()
    return destination


def materialize_png_to_path(screenshot: Screenshot, settings: Settings, destination: Path) -> Path:
    """Materialize export evidence without changing the persisted screenshot record."""
    if screenshot.relative_path:
        source = ensure_within_root(settings.data_root, screenshot.relative_path)
        if source.is_file():
            frame = cast(Frame | None, cv2.imread(str(source), cv2.IMREAD_COLOR))
            if frame is not None and frame.size:
                return write_png_atomic(frame, destination)
    return write_png_atomic(_decode_screenshot_source(screenshot, settings), destination)


def _decode_screenshot_source(screenshot: Screenshot, settings: Settings) -> Frame:
    """Resolve and decode the project-owned source video for a screenshot timestamp."""
    project = screenshot.project
    asset = project.media_asset
    if asset is None:
        raise PipelineServiceError("PROJECT_ASSET_MISSING", "The source video is unavailable.")
    source = ensure_within_root(settings.data_root, asset.relative_path)
    if not source.is_file():
        raise PipelineServiceError("PROJECT_ASSET_MISSING", "The source video is unavailable.")
    return decode_exact_frame(source, screenshot.timestamp_seconds)


def _write_encoded(frame: Frame, destination: Path, extension: str, options: list[int]) -> Path:
    """Encode one image payload and promote it without exposing a partial derivative."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded, payload = cv2.imencode(extension, frame, options)
    if not encoded:
        raise PipelineServiceError("FRAME_EXTRACTION_FAILED", "A screenshot could not be encoded.")
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    try:
        partial.write_bytes(payload.tobytes())
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination
