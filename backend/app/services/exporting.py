"""Atomic construction of immutable Narrastep 0.2 ZIP packages."""

from __future__ import annotations

import json
import math
import os
import shutil
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from app.schemas import serialize_utc
from app.services.diagnostics import MetricsCollector
from app.services.export_guides import build_batch_guide, build_batch_task, build_generation_prompt
from app.services.pipeline import perceptual_hash_similarity
from app.services.screenshot_materialization import decode_exact_frame, write_png_atomic


@dataclass(frozen=True, slots=True)
class ExportSegment:
    """Carry the reviewed evidence needed to render one export step."""

    id: str
    sequence: int
    start_seconds: float
    end_seconds: float
    raw_text: str
    reviewed_text: str
    speaker: str | None
    confidence: float | None
    screenshot_id: str | None = None
    screenshot_path: Path | None = None
    screenshot_thumbnail_path: Path | None = None
    screenshot_source_video_path: Path | None = None
    screenshot_timestamp_seconds: float | None = None
    screenshot_perceptual_hash: str | None = None
    screenshot_quality_score: float | None = None
    screenshot_association_score: float | None = None
    screenshot_user_selected: bool = False
    screenshot_is_mandatory: bool = False
    screenshot_selection_reason: str | None = None
    screenshot_width: int | None = None
    screenshot_height: int | None = None


@dataclass(frozen=True, slots=True)
class ExportRequest:
    """Carry a complete filesystem-safe snapshot for one immutable export."""

    export_id: str
    project: dict[str, Any]
    source: dict[str, Any]
    segments: list[ExportSegment]
    raw_transcript_path: Path
    destination_path: Path
    output_mode: str
    original_video_path: Path | None = None
    include_original_video: bool = False
    created_at: datetime | None = None
    extra_manifest: dict[str, Any] = field(default_factory=dict)
    chatgpt_max_files_per_batch: int = 20
    duplicate_similarity_threshold: float = 0.92
    selection_strategy: str = "legacy"
    image_budget_profile: str = "balanced"
    duration_seconds: float = 0.0


@dataclass(frozen=True, slots=True)
class ExportedScreenshot:
    """Describe one unique screenshot artifact included in the export package."""

    id: str
    source_screenshot_id: str
    timestamp_seconds: float | None
    relative_path: str


@dataclass(frozen=True, slots=True)
class RenderedExportSegment:
    """Pair one complete transcript segment with its optional exported screenshot."""

    segment: ExportSegment
    screenshot: ExportedScreenshot | None


@dataclass(frozen=True, slots=True)
class _ScreenshotGroup:
    """Aggregate every segment that references the same persisted primary screenshot."""

    source_screenshot_id: str
    source_path: Path | None
    thumbnail_path: Path | None
    source_video_path: Path | None
    timestamp_seconds: float | None
    perceptual_hash: str | None
    quality_score: float
    association_score: float
    user_selected: bool
    is_mandatory: bool
    selection_reason: str | None
    width: int | None
    height: int | None
    segments: tuple[ExportSegment, ...]

    @property
    def first_sequence(self) -> int:
        """Return the earliest transcript position supported by this screenshot."""
        return min(segment.sequence for segment in self.segments)


@dataclass(frozen=True, slots=True)
class _ScreenshotSelection:
    """Carry selected representatives and their segment-level reuse mapping."""

    representatives: tuple[_ScreenshotGroup, ...]
    representative_by_segment_id: dict[str, _ScreenshotGroup]


@dataclass(frozen=True, slots=True)
class _ChatGptBatch:
    """Carry one chronological and self-contained ChatGPT upload unit."""

    rendered_segments: tuple[RenderedExportSegment, ...]
    screenshots: tuple[ExportedScreenshot, ...]


@dataclass(frozen=True, slots=True)
class ExportEstimate:
    """Expose counts and a conservative local size estimate before ZIP generation."""

    canonical_image_count: int
    batch_count: int
    batch_image_copy_count: int
    estimated_zip_bytes: int
    redundancy_warning: bool


# `task_briefing` keeps the Version 0.2 `technical_walkthrough` budget (ADR 0009).
MODE_BUDGETS = {
    "tutorial": (1.2, 2.0),
    "task_briefing": (1.5, 2.2),
    "general": (0.8, 1.5),
    "meeting": (0.4, 1.0),
}
BUDGET_MULTIPLIERS = {"compact": 0.75, "balanced": 1.0, "detailed": 1.25}


def build_export_package(request: ExportRequest, metrics: MetricsCollector | None = None) -> Path:
    """Build a complete package in temporary storage and atomically publish its ZIP."""
    request.destination_path.parent.mkdir(parents=True, exist_ok=True)
    if request.destination_path.exists():
        raise FileExistsError("An immutable export artifact already exists.")
    partial_zip = request.destination_path.with_suffix(request.destination_path.suffix + ".partial")
    partial_zip.unlink(missing_ok=True)
    try:
        with tempfile.TemporaryDirectory(
            prefix=f"export-{request.export_id}-", dir=request.destination_path.parent
        ) as temporary_directory:
            package_root = Path(temporary_directory) / "package"
            package_root.mkdir()
            rendered_segments = _render_screenshots(request, package_root, metrics)
            manifest = _build_manifest(request, rendered_segments)
            _write_json(package_root / "manifest.json", manifest)
            transcript_root = package_root / "transcript"
            transcript_root.mkdir()
            _copy_raw_transcript(
                request.raw_transcript_path, transcript_root / "raw-transcript.json"
            )
            (transcript_root / "reviewed-transcript.md").write_text(
                _build_reviewed_markdown(request, rendered_segments), encoding="utf-8"
            )
            (transcript_root / "subtitles.srt").write_text(
                _build_srt(rendered_segments), encoding="utf-8"
            )
            llm_root = package_root / "llm"
            llm_root.mkdir()
            (llm_root / "llm-input.md").write_text(
                _build_llm_input(request, rendered_segments), encoding="utf-8"
            )
            (llm_root / "generation-prompt.md").write_text(
                build_generation_prompt(request.output_mode), encoding="utf-8"
            )
            _write_chatgpt_batches(request, rendered_segments, package_root, metrics)
            metadata_root = package_root / "metadata"
            metadata_root.mkdir()
            _write_json(metadata_root / "source.json", request.source)
            if request.include_original_video and request.original_video_path is not None:
                source_root = package_root / "source"
                source_root.mkdir()
                shutil.copy2(
                    request.original_video_path,
                    source_root / f"original-video{request.original_video_path.suffix.lower()}",
                )
            _write_zip(package_root, partial_zip)
            os.replace(partial_zip, request.destination_path)
    finally:
        partial_zip.unlink(missing_ok=True)
    return request.destination_path


def estimate_export_package(request: ExportRequest) -> ExportEstimate:
    """Estimate export counts and bytes with the same selector used by package generation."""
    selection = _select_screenshot_groups(
        request.segments,
        similarity_threshold=request.duplicate_similarity_threshold,
        selection_strategy=request.selection_strategy,
        output_mode=request.output_mode,
        duration_seconds=request.duration_seconds,
        budget_profile=request.image_budget_profile,
    )
    batch_count, batch_copy_count = _estimate_batch_shape(
        request.segments,
        selection,
        request.chatgpt_max_files_per_batch,
    )
    estimated_bytes = 32 * 1024
    if request.raw_transcript_path.is_file():
        estimated_bytes += request.raw_transcript_path.stat().st_size
    if request.include_original_video and request.original_video_path is not None:
        if request.original_video_path.is_file():
            estimated_bytes += request.original_video_path.stat().st_size
    for group in selection.representatives:
        if group.source_path is not None and group.source_path.is_file():
            estimated_bytes += group.source_path.stat().st_size
            continue
        thumbnail_bytes = (
            group.thumbnail_path.stat().st_size
            if group.thumbnail_path is not None and group.thumbnail_path.is_file()
            else 0
        )
        pixel_estimate = round((group.width or 0) * (group.height or 0) * 0.75)
        estimated_bytes += max(thumbnail_bytes, pixel_estimate)
    group_count = len(_build_screenshot_groups(request.segments))
    hard_cap = _automatic_image_hard_cap(request.output_mode, request.duration_seconds)
    return ExportEstimate(
        canonical_image_count=len(selection.representatives),
        batch_count=batch_count,
        batch_image_copy_count=batch_copy_count,
        estimated_zip_bytes=estimated_bytes,
        redundancy_warning=(
            batch_copy_count > len(selection.representatives)
            or (request.selection_strategy == "adaptive_v1" and group_count > hard_cap)
        ),
    )


def _render_screenshots(
    request: ExportRequest,
    package_root: Path,
    metrics: MetricsCollector | None = None,
) -> list[RenderedExportSegment]:
    """Select and normalize every distinct reusable screenshot required by the review."""
    screenshot_root = package_root / "screenshots"
    screenshot_root.mkdir()
    selection = _select_screenshot_groups(
        request.segments,
        similarity_threshold=request.duplicate_similarity_threshold,
        selection_strategy=request.selection_strategy,
        output_mode=request.output_mode,
        duration_seconds=request.duration_seconds,
        budget_profile=request.image_budget_profile,
    )
    artifacts_by_source_id: dict[str, ExportedScreenshot] = {}
    for group in selection.representatives:
        filename = f"step-{group.first_sequence + 1:03d}.png"
        destination = screenshot_root / filename
        if group.source_path is not None and group.source_path.is_file():
            with Image.open(group.source_path) as image:
                image.convert("RGB").save(destination, format="PNG", optimize=True)
        elif group.source_video_path is not None and group.timestamp_seconds is not None:
            write_png_atomic(
                decode_exact_frame(group.source_video_path, group.timestamp_seconds), destination
            )
        else:
            raise FileNotFoundError("A selected screenshot asset is unavailable.")
        artifacts_by_source_id[group.source_screenshot_id] = ExportedScreenshot(
            id=destination.stem,
            source_screenshot_id=group.source_screenshot_id,
            timestamp_seconds=group.timestamp_seconds,
            relative_path=f"screenshots/{filename}",
        )

    if metrics is not None:
        metrics.set("exportCanonicalImageCount", len(selection.representatives))
        metrics.set(
            "diskBytesPng",
            sum(path.stat().st_size for path in screenshot_root.glob("*.png")),
        )

    return [
        RenderedExportSegment(
            segment=segment,
            screenshot=(
                artifacts_by_source_id[representative.source_screenshot_id]
                if (representative := selection.representative_by_segment_id.get(segment.id))
                else None
            ),
        )
        for segment in request.segments
    ]


def _select_screenshot_groups(
    segments: list[ExportSegment],
    *,
    similarity_threshold: float,
    selection_strategy: str = "legacy",
    output_mode: str = "general",
    duration_seconds: float = 0.0,
    budget_profile: str = "balanced",
) -> _ScreenshotSelection:
    """Preserve manual evidence and select every distinct automatic screenshot representative."""
    if not 0 <= similarity_threshold <= 1:
        raise ValueError("The screenshot similarity threshold must be between zero and one.")

    groups = _build_screenshot_groups(segments)
    manual_groups = sorted(
        (group for group in groups if group.user_selected),
        key=lambda group: (group.first_sequence, group.source_screenshot_id),
    )
    automatic_groups = sorted(
        (group for group in groups if not group.user_selected),
        key=_automatic_group_priority,
    )
    mandatory_groups = sorted(
        (group for group in automatic_groups if group.is_mandatory),
        key=_automatic_group_priority,
    )
    automatic_groups = [group for group in automatic_groups if not group.is_mandatory]
    representatives = list(manual_groups) + mandatory_groups
    representative_by_segment_id = {
        segment.id: group for group in representatives for segment in group.segments
    }

    automatic_limit = len(automatic_groups)
    if selection_strategy == "adaptive_v1":
        automatic_limit = _automatic_image_target(output_mode, duration_seconds, budget_profile)
    selected_automatic_count = 0
    for group in automatic_groups:
        representative = next(
            (
                selected
                for selected in representatives
                if _groups_are_visually_similar(group, selected, similarity_threshold)
            ),
            None,
        )
        if representative is None:
            if selected_automatic_count >= automatic_limit:
                continue
            representative = group
            representatives.append(group)
            selected_automatic_count += 1
        for segment in group.segments:
            representative_by_segment_id[segment.id] = representative

    return _ScreenshotSelection(
        representatives=tuple(
            sorted(
                representatives,
                key=lambda group: (group.first_sequence, group.source_screenshot_id),
            )
        ),
        representative_by_segment_id=representative_by_segment_id,
    )


def _automatic_image_target(output_mode: str, duration_seconds: float, budget_profile: str) -> int:
    """Calculate the mode target after the compact or detailed multiplier."""
    target_per_minute, hard_per_minute = MODE_BUDGETS.get(output_mode, MODE_BUDGETS["general"])
    minutes = max(duration_seconds / 60.0, 1 / 60)
    multiplier = BUDGET_MULTIPLIERS.get(budget_profile, 1.0)
    target = math.ceil(minutes * target_per_minute * multiplier)
    hard_cap = math.ceil(minutes * hard_per_minute)
    return max(1, min(max(1, hard_cap), max(1, target)))


def _automatic_image_hard_cap(output_mode: str, duration_seconds: float) -> int:
    """Calculate the mode hard cap used only for estimate warnings and mandatory overflow."""
    _, hard_per_minute = MODE_BUDGETS.get(output_mode, MODE_BUDGETS["general"])
    return max(1, math.ceil(max(duration_seconds / 60.0, 1 / 60) * hard_per_minute))


def _estimate_batch_shape(
    segments: list[ExportSegment],
    selection: _ScreenshotSelection,
    max_files_per_batch: int,
) -> tuple[int, int]:
    """Count chronological batches and repeated image copies without creating files."""
    if max_files_per_batch < 2:
        raise ValueError("A ChatGPT batch must allow one Markdown file and at least one image.")
    capacity = max_files_per_batch - 1
    batches = 0
    copies = 0
    current: set[str] = set()
    for segment in segments:
        representative = selection.representative_by_segment_id.get(segment.id)
        screenshot_id = representative.source_screenshot_id if representative else None
        if screenshot_id is not None and screenshot_id not in current and len(current) >= capacity:
            batches += 1
            copies += len(current)
            current = set()
        if screenshot_id is not None:
            current.add(screenshot_id)
    batches += 1
    copies += len(current)
    return batches, copies


def _write_chatgpt_batches(
    request: ExportRequest,
    rendered: list[RenderedExportSegment],
    package_root: Path,
    metrics: MetricsCollector | None = None,
) -> None:
    """Write chronological upload folders without changing the canonical export evidence."""
    batches = _build_chatgpt_batches(
        rendered,
        max_files_per_batch=request.chatgpt_max_files_per_batch,
    )
    batches_root = package_root / "chatgpt-batches"
    batches_root.mkdir()
    if metrics is not None:
        metrics.set("batchCount", len(batches))
        metrics.set("exportBatchCopyCount", sum(len(batch.screenshots) for batch in batches))
    (batches_root / "README.md").write_text(
        build_batch_guide(request.output_mode, request.chatgpt_max_files_per_batch, len(batches)),
        encoding="utf-8",
    )

    for batch_index, batch in enumerate(batches, start=1):
        batch_root = batches_root / f"batch-{batch_index:03d}"
        batch_root.mkdir()
        for screenshot in batch.screenshots:
            source = package_root / screenshot.relative_path
            shutil.copy2(source, batch_root / Path(screenshot.relative_path).name)
        (batch_root / "batch-input.md").write_text(
            _build_chatgpt_batch_input(
                request,
                batch,
                batch_index=batch_index,
                batch_count=len(batches),
            ),
            encoding="utf-8",
        )


def _build_chatgpt_batches(
    rendered: list[RenderedExportSegment], *, max_files_per_batch: int
) -> list[_ChatGptBatch]:
    """Split consecutive steps before a new image would exceed one upload batch."""
    if max_files_per_batch < 2:
        raise ValueError("A ChatGPT batch must allow one Markdown file and at least one image.")

    max_images_per_batch = max_files_per_batch - 1
    batches: list[_ChatGptBatch] = []
    current_segments: list[RenderedExportSegment] = []
    current_screenshots: dict[str, ExportedScreenshot] = {}

    for rendered_segment in rendered:
        screenshot = rendered_segment.screenshot
        introduces_new_image = (
            screenshot is not None and screenshot.source_screenshot_id not in current_screenshots
        )
        if introduces_new_image and len(current_screenshots) >= max_images_per_batch:
            batches.append(
                _ChatGptBatch(
                    rendered_segments=tuple(current_segments),
                    screenshots=tuple(current_screenshots.values()),
                )
            )
            current_segments = []
            current_screenshots = {}

        current_segments.append(rendered_segment)
        if screenshot is not None:
            current_screenshots.setdefault(screenshot.source_screenshot_id, screenshot)

    if current_segments or not batches:
        batches.append(
            _ChatGptBatch(
                rendered_segments=tuple(current_segments),
                screenshots=tuple(current_screenshots.values()),
            )
        )
    return batches


def _build_chatgpt_batch_input(
    request: ExportRequest,
    batch: _ChatGptBatch,
    *,
    batch_index: int,
    batch_count: int,
) -> str:
    """Render one self-contained evidence prompt with global step numbering."""
    lines = [
        f"# Narrastep analysis batch {batch_index} of {batch_count}",
        "",
        "IMPORTANT: Use only the evidence in this batch. Do not invent unsupported steps, "
        "actions, labels, prerequisites, or outcomes.",
        "",
        "Analyze only the steps listed below. Preserve their global step numbers so this result "
        "can be combined with the other batches in order.",
        "",
        f"Requested document mode: {request.output_mode}",
        "",
        *build_batch_task(request.output_mode, batch_index, batch_count),
    ]
    for rendered_segment in batch.rendered_segments:
        segment = rendered_segment.segment
        screenshot_name = (
            Path(rendered_segment.screenshot.relative_path).name
            if rendered_segment.screenshot
            else None
        )
        lines.extend(
            [
                f"## Evidence step {segment.sequence + 1}",
                f"Time: {segment.start_seconds:.3f}s–{segment.end_seconds:.3f}s",
                f"Transcript: {segment.reviewed_text}",
                f"Screenshot: {screenshot_name or 'none included'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _build_screenshot_groups(segments: list[ExportSegment]) -> list[_ScreenshotGroup]:
    """Validate selected assets and group repeated source screenshot references."""
    segments_by_screenshot: dict[str, list[ExportSegment]] = {}
    for segment in segments:
        if segment.screenshot_path is not None and not segment.screenshot_path.is_file():
            raise FileNotFoundError("A selected screenshot asset is unavailable.")
        if segment.screenshot_id is None:
            continue
        if segment.screenshot_path is None and (
            segment.screenshot_source_video_path is None
            or segment.screenshot_timestamp_seconds is None
        ):
            raise FileNotFoundError("A selected screenshot asset is unavailable.")
        segments_by_screenshot.setdefault(segment.screenshot_id, []).append(segment)

    groups: list[_ScreenshotGroup] = []
    for screenshot_id, grouped_segments in segments_by_screenshot.items():
        ordered_segments = tuple(sorted(grouped_segments, key=lambda segment: segment.sequence))
        representative = ordered_segments[0]
        groups.append(
            _ScreenshotGroup(
                source_screenshot_id=screenshot_id,
                source_path=representative.screenshot_path,
                thumbnail_path=representative.screenshot_thumbnail_path,
                source_video_path=representative.screenshot_source_video_path,
                timestamp_seconds=representative.screenshot_timestamp_seconds,
                perceptual_hash=representative.screenshot_perceptual_hash,
                quality_score=max(
                    segment.screenshot_quality_score or 0.0 for segment in ordered_segments
                ),
                association_score=max(
                    segment.screenshot_association_score or 0.0 for segment in ordered_segments
                ),
                user_selected=any(segment.screenshot_user_selected for segment in ordered_segments),
                is_mandatory=any(segment.screenshot_is_mandatory for segment in ordered_segments),
                selection_reason=representative.screenshot_selection_reason,
                width=representative.screenshot_width,
                height=representative.screenshot_height,
                segments=ordered_segments,
            )
        )
    return groups


def _automatic_group_priority(group: _ScreenshotGroup) -> tuple[int, float, float, int, str]:
    """Rank automatic evidence by reuse, segment relevance, visual quality, and time."""
    return (
        -len(group.segments),
        -group.association_score,
        -group.quality_score,
        group.first_sequence,
        group.source_screenshot_id,
    )


def _groups_are_visually_similar(
    left: _ScreenshotGroup, right: _ScreenshotGroup, similarity_threshold: float
) -> bool:
    """Identify globally repeated automatic screens without relying on transcript content."""
    if left.perceptual_hash is None or right.perceptual_hash is None:
        return False
    try:
        return (
            perceptual_hash_similarity(left.perceptual_hash, right.perceptual_hash)
            >= similarity_threshold
        )
    except ValueError:
        return False


def _build_manifest(
    request: ExportRequest, rendered: list[RenderedExportSegment]
) -> dict[str, Any]:
    """Describe project, source, active segments, and honest evidence associations."""
    manifest_segments = []
    screenshots = []
    associations = []
    recorded_screenshot_ids: set[str] = set()
    for rendered_segment in rendered:
        segment = rendered_segment.segment
        screenshot = rendered_segment.screenshot
        manifest_segments.append(
            {
                "id": segment.id,
                "sequence": segment.sequence,
                "startSeconds": segment.start_seconds,
                "endSeconds": segment.end_seconds,
                "rawText": segment.raw_text,
                "reviewedText": segment.reviewed_text,
                "speaker": segment.speaker,
                "confidence": segment.confidence,
                "primaryScreenshotId": screenshot.source_screenshot_id if screenshot else None,
                "primaryScreenshotPath": screenshot.relative_path if screenshot else None,
                "exportedScreenshotId": screenshot.id if screenshot else None,
            }
        )
        if screenshot and screenshot.id not in recorded_screenshot_ids:
            screenshots.append(
                {
                    "id": screenshot.id,
                    "sourceScreenshotId": screenshot.source_screenshot_id,
                    "segmentId": segment.id,
                    "timestampSeconds": screenshot.timestamp_seconds,
                    "path": screenshot.relative_path,
                }
            )
            recorded_screenshot_ids.add(screenshot.id)
        if screenshot:
            associations.append(
                {
                    "segmentId": segment.id,
                    "screenshotId": screenshot.id,
                    "sourceScreenshotId": screenshot.source_screenshot_id,
                    "path": screenshot.relative_path,
                    "isPrimary": True,
                }
            )
    manifest = {
        "schemaVersion": "0.2.0",
        "exportId": request.export_id,
        "createdAt": serialize_utc(request.created_at or datetime.now(UTC)),
        "narrastepVersion": "0.2.0",
        "project": request.project,
        "source": request.source,
        "segments": manifest_segments,
        "screenshots": screenshots,
        "associations": associations,
        "paths": {
            "manifest": "manifest.json",
            "rawTranscript": "transcript/raw-transcript.json",
            "reviewedTranscript": "transcript/reviewed-transcript.md",
            "subtitles": "transcript/subtitles.srt",
            "llmInput": "llm/llm-input.md",
            "generationPrompt": "llm/generation-prompt.md",
            "sourceMetadata": "metadata/source.json",
            "originalVideo": _original_video_export_path(request),
        },
        "includedOriginalVideo": bool(
            request.include_original_video and request.original_video_path is not None
        ),
    }
    manifest.update(request.extra_manifest)
    return manifest


def _copy_raw_transcript(source: Path, destination: Path) -> None:
    """Copy the immutable raw Whisper result without deriving it from reviewed text."""
    if not source.is_file():
        raise FileNotFoundError("The immutable raw transcript asset is unavailable.")
    shutil.copy2(source, destination)


def _original_video_export_path(request: ExportRequest) -> str | None:
    """Return the optional source path exactly as it appears in the immutable package."""
    if not request.include_original_video or request.original_video_path is None:
        return None
    return f"source/original-video{request.original_video_path.suffix.lower()}"


def _build_reviewed_markdown(request: ExportRequest, rendered: list[RenderedExportSegment]) -> str:
    """Render a readable reviewed procedure with optional screenshot references."""
    lines = [f"# {request.project.get('name', 'Narrastep export')}", ""]
    description = request.project.get("description")
    if description:
        lines.extend([str(description), ""])
    for rendered_segment in rendered:
        segment = rendered_segment.segment
        screenshot_path = (
            rendered_segment.screenshot.relative_path if rendered_segment.screenshot else None
        )
        lines.extend([f"## Step {segment.sequence + 1}", "", segment.reviewed_text, ""])
        if screenshot_path:
            lines.extend([f"![Step {segment.sequence + 1}](../{screenshot_path})", ""])
        else:
            lines.extend(["_No screenshot was included for this step._", ""])
    return "\n".join(lines).rstrip() + "\n"


def _build_srt(rendered: list[RenderedExportSegment]) -> str:
    """Render reviewed segment text as a standards-compatible SRT transcript."""
    entries = []
    for index, rendered_segment in enumerate(rendered, start=1):
        segment = rendered_segment.segment
        entries.append(
            f"{index}\n{_srt_time(segment.start_seconds)} --> {_srt_time(segment.end_seconds)}\n"
            f"{segment.reviewed_text}\n"
        )
    return "\n".join(entries)


def _srt_time(seconds: float) -> str:
    """Format non-negative seconds as an SRT timestamp with millisecond precision."""
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def _build_llm_input(request: ExportRequest, rendered: list[RenderedExportSegment]) -> str:
    """Render an evidence-only handoff that does not invoke or depend on an LLM."""
    lines = [
        "# Narrastep evidence package",
        "",
        "IMPORTANT: Use only this evidence. Do not invent unsupported steps, actions, or outcomes.",
        "",
        f"Requested document mode: {request.output_mode}",
        "",
    ]
    for rendered_segment in rendered:
        segment = rendered_segment.segment
        screenshot_path = (
            rendered_segment.screenshot.relative_path if rendered_segment.screenshot else None
        )
        lines.extend(
            [
                f"## Evidence step {segment.sequence + 1}",
                f"Time: {segment.start_seconds:.3f}s–{segment.end_seconds:.3f}s",
                f"Transcript: {segment.reviewed_text}",
                f"Screenshot: {f'../{screenshot_path}' if screenshot_path else 'none included'}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, value: Any) -> None:
    """Write deterministic human-readable UTF-8 JSON for inspection and versioning."""
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_zip(package_root: Path, destination: Path) -> None:
    """Write files in sorted order so equivalent evidence produces a stable layout."""
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(item for item in package_root.rglob("*") if item.is_file()):
            archive.write(path, path.relative_to(package_root).as_posix())
