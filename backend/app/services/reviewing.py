"""Transactional reviewed-text, split, merge, and screenshot-selection rules."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import (
    Project,
    ProjectState,
    Screenshot,
    SegmentLineage,
    SegmentScreenshotAssociation,
    TranscriptSegment,
)
from app.schemas import SegmentMergeRequest, SegmentSplitRequest


def segment_response_data(segment: TranscriptSegment) -> dict[str, Any]:
    """Build the frontend segment contract while retaining immutable word evidence."""
    words = [
        {
            "text": str(word.get("text", word.get("word", ""))),
            "startSeconds": float(word.get("startSeconds", word.get("start", 0.0))),
            "endSeconds": float(word.get("endSeconds", word.get("end", 0.0))),
            "probability": word.get("probability"),
        }
        for word in segment.raw_words
    ]
    return {
        "id": segment.id,
        "projectId": segment.project_id,
        "segmentIndex": segment.sequence,
        "startSeconds": segment.start_seconds,
        "endSeconds": segment.end_seconds,
        "rawText": segment.raw_text,
        "reviewedText": segment.reviewed_text,
        "words": words,
        "speakerLabel": segment.speaker,
        "confidence": segment.confidence,
        "createdAt": segment.created_at,
        "updatedAt": segment.updated_at,
    }


def mark_project_review_changed(project: Project) -> None:
    """Return completed work to review while preserving immutable export history."""
    if project.state == ProjectState.COMPLETED:
        project.state = ProjectState.REVIEW


def split_segment(
    session: Session, segment: TranscriptSegment, request: SegmentSplitRequest
) -> tuple[TranscriptSegment, TranscriptSegment]:
    """Archive one segment and create two lineage-aware children atomically."""
    if not segment.active:
        raise ApiError("INVALID_SEGMENT_OPERATION", "Only an active segment can be split.", 409)
    _reject_busy_review_project(segment.project)
    if request.left_reviewed_text + request.right_reviewed_text != segment.reviewed_text:
        raise ApiError(
            "INVALID_SEGMENT_OPERATION",
            "The split text must reproduce the current reviewed text exactly.",
            422,
        )
    if not request.left_reviewed_text.strip() or not request.right_reviewed_text.strip():
        raise ApiError(
            "INVALID_SEGMENT_OPERATION",
            "Both split portions must contain meaningful reviewed text.",
            422,
        )
    if not segment.start_seconds < request.split_seconds < segment.end_seconds:
        raise ApiError(
            "INVALID_SEGMENT_OPERATION",
            "The split time must be inside the segment interval.",
            422,
        )

    source_associations = list(segment.screenshot_associations)
    left_words, right_words = _partition_words(segment.raw_words, request.split_seconds)
    left_raw, right_raw = _partition_raw_text(
        segment, left_words, right_words, request.split_seconds
    )
    segment.active = False

    left = TranscriptSegment(
        project_id=segment.project_id,
        sequence=segment.sequence,
        start_seconds=segment.start_seconds,
        end_seconds=request.split_seconds,
        raw_text=left_raw,
        reviewed_text=request.left_reviewed_text,
        raw_words=left_words,
        speaker=segment.speaker,
        confidence=segment.confidence,
    )
    right = TranscriptSegment(
        project_id=segment.project_id,
        sequence=segment.sequence + 1,
        start_seconds=request.split_seconds,
        end_seconds=segment.end_seconds,
        raw_text=right_raw,
        reviewed_text=request.right_reviewed_text,
        raw_words=right_words,
        speaker=segment.speaker,
        confidence=segment.confidence,
    )
    session.add_all([left, right])
    session.flush()
    session.add_all(
        [
            SegmentLineage(
                child_segment_id=left.id, source_segment_id=segment.id, operation="split"
            ),
            SegmentLineage(
                child_segment_id=right.id, source_segment_id=segment.id, operation="split"
            ),
        ]
    )
    _copy_ranked_candidates(session, left, source_associations)
    _copy_ranked_candidates(session, right, source_associations)
    _reindex_active_segments(session, segment.project_id)
    mark_project_review_changed(segment.project)
    session.commit()
    session.refresh(left)
    session.refresh(right)
    return left, right


def merge_segments(session: Session, request: SegmentMergeRequest) -> TranscriptSegment:
    """Archive contiguous active segments and create one evidence-preserving child."""
    segments = list(
        session.scalars(
            select(TranscriptSegment).where(TranscriptSegment.id.in_(request.segment_ids))
        )
    )
    by_id = {segment.id: segment for segment in segments}
    if len(by_id) != len(request.segment_ids):
        raise ApiError("INVALID_SEGMENT_OPERATION", "One or more segments were not found.", 404)
    ordered = [by_id[segment_id] for segment_id in request.segment_ids]
    project_id = ordered[0].project_id
    if any(not segment.active or segment.project_id != project_id for segment in ordered):
        raise ApiError(
            "INVALID_SEGMENT_OPERATION",
            "Merge segments must be active and belong to the same project.",
            409,
        )
    _reject_busy_review_project(ordered[0].project)
    sequences = [segment.sequence for segment in ordered]
    expected = list(range(sequences[0], sequences[0] + len(sequences)))
    if sequences != expected:
        raise ApiError(
            "INVALID_SEGMENT_OPERATION",
            "Merge segment identifiers must be ordered and contiguous.",
            422,
        )

    associations = _unique_associations(ordered)
    duration_total = sum(max(0, item.end_seconds - item.start_seconds) for item in ordered)
    confidence = _duration_weighted_confidence(ordered, duration_total)
    speakers = {item.speaker for item in ordered}
    merged = TranscriptSegment(
        project_id=project_id,
        sequence=ordered[0].sequence,
        start_seconds=ordered[0].start_seconds,
        end_seconds=ordered[-1].end_seconds,
        raw_text=" ".join(item.raw_text.strip() for item in ordered if item.raw_text.strip()),
        reviewed_text="\n".join(item.reviewed_text for item in ordered),
        raw_words=[word for item in ordered for word in item.raw_words],
        speaker=speakers.pop() if len(speakers) == 1 else None,
        confidence=confidence,
    )
    for segment in ordered:
        segment.active = False
    session.add(merged)
    session.flush()
    session.add_all(
        [
            SegmentLineage(child_segment_id=merged.id, source_segment_id=item.id, operation="merge")
            for item in ordered
        ]
    )
    _copy_ranked_candidates(session, merged, associations)
    _reindex_active_segments(session, project_id)
    mark_project_review_changed(ordered[0].project)
    session.commit()
    session.refresh(merged)
    return merged


def select_primary_screenshot(
    session: Session, segment: TranscriptSegment, screenshot_id: str | None
) -> TranscriptSegment:
    """Apply one explicit primary selection without accepting cross-project evidence."""
    if not segment.active:
        raise ApiError("INVALID_SEGMENT_OPERATION", "Only an active segment can be edited.", 409)
    _reject_busy_review_project(segment.project)
    associations = list(segment.screenshot_associations)
    selected = next(
        (item for item in associations if item.screenshot_id == screenshot_id),
        None,
    )
    if screenshot_id is not None and selected is None:
        screenshot = session.get(Screenshot, screenshot_id)
        if screenshot is None or screenshot.project_id != segment.project_id:
            raise ApiError(
                "INVALID_SCREENSHOT_ASSOCIATION",
                "The screenshot is not available for this project.",
                422,
            )
        selected = SegmentScreenshotAssociation(
            segment_id=segment.id,
            screenshot_id=screenshot.id,
            rank=len(associations),
            score=_candidate_score(segment, screenshot),
        )
        session.add(selected)
        associations.append(selected)
    # Clear first so SQLite's partial unique index is never violated by update ordering.
    for association in associations:
        association.is_primary = False
        association.user_selected = False
    session.flush()
    if selected is not None:
        selected.is_primary = True
        selected.user_selected = True
    mark_project_review_changed(segment.project)
    session.commit()
    session.refresh(segment)
    return segment


def _partition_words(
    words: list[dict[str, Any]], split_seconds: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Partition immutable words by midpoint so boundary-spanning words remain whole."""
    left: list[dict[str, Any]] = []
    right: list[dict[str, Any]] = []
    for word in words:
        start = float(word.get("startSeconds", word.get("start", split_seconds)))
        end = float(word.get("endSeconds", word.get("end", start)))
        (left if (start + end) / 2 <= split_seconds else right).append(word)
    return left, right


def _reject_busy_review_project(project: Project) -> None:
    """Prevent review mutations while processing or immutable export capture is active."""
    if project.state in {
        ProjectState.QUEUED,
        ProjectState.PROCESSING,
        ProjectState.EXPORTING,
    }:
        raise ApiError("PROJECT_BUSY", "The project has active background work.", 409)


def _partition_raw_text(
    segment: TranscriptSegment,
    left_words: list[dict[str, Any]],
    right_words: list[dict[str, Any]],
    split_seconds: float,
) -> tuple[str, str]:
    """Derive child raw text from words, with a proportional fallback for legacy rows."""
    if segment.raw_words:
        return (
            "".join(str(word.get("text", word.get("word", ""))) for word in left_words).strip(),
            "".join(str(word.get("text", word.get("word", ""))) for word in right_words).strip(),
        )
    duration = segment.end_seconds - segment.start_seconds
    ratio = (split_seconds - segment.start_seconds) / duration if duration > 0 else 0.5
    boundary = round(len(segment.raw_text) * ratio)
    return segment.raw_text[:boundary].rstrip(), segment.raw_text[boundary:].lstrip()


def _duration_weighted_confidence(
    segments: list[TranscriptSegment], duration_total: float
) -> float | None:
    """Combine available confidence values in proportion to their segment duration."""
    weighted = [
        (segment.confidence, max(0, segment.end_seconds - segment.start_seconds))
        for segment in segments
        if segment.confidence is not None
    ]
    weighted_duration = sum(duration for _, duration in weighted)
    if not weighted or weighted_duration <= 0 or duration_total <= 0:
        return None
    return (
        sum(float(confidence) * duration for confidence, duration in weighted) / weighted_duration
    )


def _unique_associations(
    segments: Iterable[TranscriptSegment],
) -> list[SegmentScreenshotAssociation]:
    """Collect each screenshot once while preferring an explicit user selection."""
    selected: dict[str, SegmentScreenshotAssociation] = {}
    for segment in segments:
        for association in segment.screenshot_associations:
            current = selected.get(association.screenshot_id)
            if current is None or (association.user_selected and not current.user_selected):
                selected[association.screenshot_id] = association
    return list(selected.values())


def _copy_ranked_candidates(
    session: Session,
    segment: TranscriptSegment,
    source_associations: list[SegmentScreenshotAssociation],
) -> None:
    """Re-rank existing evidence and preserve applicable explicit primary choices."""
    if not source_associations:
        return
    unique = {item.screenshot_id: item for item in source_associations}
    score_ranked = sorted(
        unique.values(),
        key=lambda item: _candidate_score(segment, item.screenshot),
        reverse=True,
    )
    midpoint = (segment.start_seconds + segment.end_seconds) / 2
    user_candidates = [
        item
        for item in score_ranked
        if item.user_selected
        and segment.start_seconds <= item.screenshot.timestamp_seconds <= segment.end_seconds
    ]
    primary_source = min(
        user_candidates,
        key=lambda item: abs(item.screenshot.timestamp_seconds - midpoint),
        default=score_ranked[0],
    )
    # Explicit evidence stays within the bounded five candidates even when its score is lower.
    ranked = [primary_source] + [
        item for item in score_ranked if item.screenshot_id != primary_source.screenshot_id
    ][:4]
    for rank, source in enumerate(ranked):
        session.add(
            SegmentScreenshotAssociation(
                segment_id=segment.id,
                screenshot_id=source.screenshot_id,
                is_primary=source is primary_source,
                rank=rank,
                score=_candidate_score(segment, source.screenshot),
                user_selected=source is primary_source and source.user_selected,
            )
        )


def _candidate_score(segment: TranscriptSegment, screenshot: Screenshot) -> float:
    """Rank visual evidence using temporal proximity and normalized image quality."""
    midpoint = (segment.start_seconds + segment.end_seconds) / 2
    half_duration = max((segment.end_seconds - segment.start_seconds) / 2, 0.001)
    temporal = max(0.0, 1.0 - abs(screenshot.timestamp_seconds - midpoint) / half_duration)
    quality = max(0.0, min(1.0, screenshot.quality_score))
    return 0.7 * temporal + 0.3 * quality


def _reindex_active_segments(session: Session, project_id: str) -> None:
    """Assign deterministic contiguous indices after structural review operations."""
    session.flush()
    active = list(
        session.scalars(
            select(TranscriptSegment)
            .where(
                TranscriptSegment.project_id == project_id,
                TranscriptSegment.active.is_(True),
            )
            .order_by(TranscriptSegment.start_seconds, TranscriptSegment.end_seconds)
        )
    )
    for sequence, segment in enumerate(active):
        segment.sequence = sequence
