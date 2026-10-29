"""CPU-compatible adaptive screenshot discovery, clustering, and coverage selection."""

from __future__ import annotations

import logging
import math
import shutil
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray

from app.services.diagnostics import MetricsCollector
from app.services.ocr import TesseractOcrEngine, token_jaccard
from app.services.pipeline import (
    FrameCandidate,
    PersistedFrameCandidate,
    PipelineConfig,
    PipelineServiceError,
    SampledFrame,
    TranscriptSegment,
    calculate_exposure_penalty,
    calculate_laplacian_sharpness,
    compute_perceptual_hash,
    perceptual_hash_similarity,
)
from app.services.screenshot_materialization import write_png_atomic, write_thumbnail_atomic

logger = logging.getLogger(__name__)

type Frame = NDArray[np.uint8]
type CancellationCheck = Callable[[], None]
type ProgressCallback = Callable[[str, int, int], None]

ERROR_TOKENS = frozenset(
    {
        "error",
        "failed",
        "failure",
        "invalid",
        "errore",
        "fallito",
        "invalido",
        "fehler",
        "fehlgeschlagen",
        "erreur",
        "echec",
        "fallo",
    }
)


@dataclass(frozen=True, slots=True)
class AdaptiveProfile:
    """Hold versioned mode-specific scan density and automatic image budgets."""

    scan_fps: float
    target_images_per_minute: float
    hard_cap_images_per_minute: float


# `task_briefing` keeps the Version 0.2 `technical_walkthrough` profile (ADR 0009).
PROFILES = {
    "tutorial": AdaptiveProfile(1.0, 1.2, 2.0),
    "task_briefing": AdaptiveProfile(1.0, 1.5, 2.2),
    "general": AdaptiveProfile(0.75, 0.8, 1.5),
    "meeting": AdaptiveProfile(0.5, 0.4, 1.0),
}
BUDGET_MULTIPLIERS = {"compact": 0.75, "balanced": 1.0, "detailed": 1.25}


@dataclass(frozen=True, slots=True)
class DocumentUnit:
    """Represent one temporary transcript coverage interval used only during processing."""

    index: int
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class InterestWindow:
    """Describe one visually or transcript-derived interval requiring fine extraction."""

    start_seconds: float
    peak_seconds: float
    end_seconds: float
    peak_score: float
    transient: bool = False
    fallback: bool = False
    text_sensitive: bool = False


@dataclass(slots=True)
class _AdaptiveCandidate:
    """Carry one decoded fine candidate and in-memory semantic comparison metadata."""

    frame_candidate: FrameCandidate
    visual_change_score: float
    is_mandatory: bool
    selection_reason: str
    tokens: frozenset[str] | None = None
    text_change_score: float | None = None
    cluster_id: str | None = None
    coverage_score: float | None = None
    role_rank_reason: str | None = None
    text_sensitive: bool = False
    decoded_frame_count: int = 0


@dataclass(slots=True)
class _Cluster:
    """Group globally equivalent candidates around a deterministic medoid."""

    members: list[_AdaptiveCandidate] = field(default_factory=list)
    representative: _AdaptiveCandidate | None = None


def extract_adaptive_frames_to_directory(
    video_path: Path,
    duration_seconds: float,
    segments: Sequence[TranscriptSegment],
    output_directory: Path,
    *,
    project_mode: str,
    detected_language: str | None,
    config: PipelineConfig,
    metrics: MetricsCollector,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[PersistedFrameCandidate]:
    """Run adaptive discovery and persist only budgeted review thumbnails."""
    profile = PROFILES.get(project_mode, PROFILES["general"])
    metrics.start_stage("low_res_scan")
    windows, decoded_scan_frames = scan_interest_windows(
        video_path,
        duration_seconds,
        profile.scan_fps,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )
    metrics.set("decodedFrameCount", decoded_scan_frames)
    metrics.set("changeWindowCount", len(windows))
    metrics.finish_stage("low_res_scan", logger)
    if cancellation_check is not None:
        cancellation_check()

    units = build_document_units(segments)
    fused_windows = fuse_interest_windows(windows, units, duration_seconds)
    candidate_times = candidate_times_for_windows(fused_windows, duration_seconds)
    metrics.set("candidateTimestampCount", len(candidate_times))

    metrics.start_stage("fine_extraction")
    candidates = extract_fine_candidates(
        video_path,
        candidate_times,
        fused_windows,
        cancellation_check=cancellation_check,
        progress_callback=progress_callback,
    )
    metrics.set("provisionalCandidateCount", len(candidates))
    metrics.set(
        "decodedFrameCount",
        decoded_scan_frames + sum(candidate.decoded_frame_count for candidate in candidates),
    )
    metrics.finish_stage("fine_extraction", logger)

    metrics.start_stage("global_clustering")
    if cancellation_check is not None:
        cancellation_check()
    if progress_callback is not None:
        progress_callback("global_clustering", 0, 1)
    ocr_engine = (
        TesseractOcrEngine(executable=config.tesseract_path)
        if config.ocr_mode == "selective"
        else None
    )
    ocr_language = "eng"
    if ocr_engine is not None:
        ocr_language, fallback = ocr_engine.resolve_language(detected_language)
        if fallback:
            metrics.increment("ocrLanguageFallbackCount")
    ocr_root = output_directory / ".ocr"
    clusters = global_cluster_candidates(
        candidates,
        ocr_engine=ocr_engine,
        ocr_language=ocr_language,
        ocr_root=ocr_root,
        metrics=metrics,
        cancellation_check=cancellation_check,
        direct_duplicate_threshold=config.adaptive_direct_duplicate_threshold,
        borderline_duplicate_threshold=config.adaptive_borderline_duplicate_threshold,
        borderline_ssim_threshold=config.adaptive_borderline_ssim_threshold,
        ocr_token_jaccard_threshold=config.adaptive_ocr_token_jaccard_threshold,
    )
    metrics.set("clusterRepresentativeCount", len(clusters))
    metrics.finish_stage("global_clustering", logger)
    if progress_callback is not None:
        progress_callback("global_clustering", 1, 1)

    metrics.start_stage("coverage_selection")
    if cancellation_check is not None:
        cancellation_check()
    if progress_callback is not None:
        progress_callback("coverage_selection", 0, 1)
    retained = select_coverage_candidates(
        clusters,
        units,
        duration_seconds,
        profile,
        config.image_budget_profile,
        max_alternatives=config.screenshot_alternatives,
    )
    metrics.set("selectedLogicalScreenshotCount", len(retained))
    metrics.finish_stage("coverage_selection", logger)
    if progress_callback is not None:
        progress_callback("coverage_selection", 1, 1)

    metrics.start_stage("thumbnail_persistence")
    if cancellation_check is not None:
        cancellation_check()
    output_directory.mkdir(parents=True, exist_ok=True)
    persisted: list[PersistedFrameCandidate] = []
    ordered_retained = sorted(retained, key=_candidate_timestamp)
    for index, candidate in enumerate(ordered_retained):
        if cancellation_check is not None:
            cancellation_check()
        persisted.append(_persist_thumbnail(candidate, output_directory, index))
        if progress_callback is not None:
            progress_callback("thumbnail_persistence", index + 1, len(ordered_retained))
    if progress_callback is not None and not ordered_retained:
        progress_callback("thumbnail_persistence", 1, 1)
    shutil.rmtree(ocr_root, ignore_errors=True)
    metrics.set("persistedThumbnailCount", len(persisted))
    metrics.set("persistedPngCount", 0)
    metrics.set(
        "diskBytesThumbnail",
        sum(item.image_path.stat().st_size for item in persisted if item.image_path.is_file()),
    )
    metrics.finish_stage("thumbnail_persistence", logger)
    return persisted


def scan_interest_windows(
    video_path: Path,
    duration_seconds: float,
    scan_fps: float,
    *,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> tuple[list[InterestWindow], int]:
    """Stream low-resolution frames and convert adaptive change scores into windows."""
    if duration_seconds <= 0 or scan_fps <= 0:
        return [], 0
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise PipelineServiceError(
            "FRAME_EXTRACTION_FAILED", "The source video could not be opened."
        )
    sample_count = max(1, math.floor(duration_seconds * scan_fps) + 1)
    rolling_scores: deque[float] = deque(maxlen=15)
    timestamp_buffer: deque[float] = deque(maxlen=max(2, math.ceil(scan_fps * 3) + 1))
    previous: Frame | None = None
    open_start: float | None = None
    event_started_at: float | None = None
    last_change_at: float | None = None
    peak_timestamp = 0.0
    peak_score = 0.0
    peak_text_sensitive = False
    stable_samples = 0
    windows: list[InterestWindow] = []
    decoded = 0
    try:
        for index in range(sample_count):
            if cancellation_check is not None:
                cancellation_check()
            timestamp = min(duration_seconds, index / scan_fps)
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1_000)
            success, frame = capture.read()
            if not success or frame is None or not frame.size:
                continue
            decoded += 1
            current = _resize_grayscale(cast(Frame, frame), 320)
            timestamp_buffer.append(timestamp)
            while timestamp_buffer and timestamp - timestamp_buffer[0] > 3.0:
                timestamp_buffer.popleft()
            if previous is not None:
                score, structural, localized = _visual_delta_signals(previous, current)
                text_sensitive = localized >= 0.06 and localized >= structural * 0.75
                threshold = _adaptive_threshold(rolling_scores)
                is_change = score >= threshold
                if is_change:
                    if open_start is None:
                        open_start = timestamp_buffer[0]
                        event_started_at = timestamp
                        peak_timestamp = timestamp
                        peak_score = score
                        peak_text_sensitive = text_sensitive
                    last_change_at = timestamp
                    if score > peak_score:
                        peak_timestamp = timestamp
                        peak_score = score
                    peak_text_sensitive = peak_text_sensitive or text_sensitive
                    stable_samples = 0
                elif open_start is not None:
                    stable_samples += 1
                    if stable_samples >= 2:
                        windows.append(
                            InterestWindow(
                                open_start,
                                peak_timestamp,
                                timestamp,
                                peak_score,
                                transient=_is_transient_event(
                                    event_started_at, last_change_at, peak_score
                                ),
                                text_sensitive=peak_text_sensitive,
                            )
                        )
                        open_start = None
                        event_started_at = None
                        last_change_at = None
                        stable_samples = 0
                        peak_text_sensitive = False
                rolling_scores.append(score)
            previous = current
            if progress_callback is not None:
                progress_callback("scanning_frames", index + 1, sample_count)
        if open_start is not None:
            windows.append(
                InterestWindow(
                    open_start,
                    peak_timestamp,
                    duration_seconds,
                    peak_score,
                    transient=_is_transient_event(event_started_at, last_change_at, peak_score),
                    text_sensitive=peak_text_sensitive,
                )
            )
    finally:
        capture.release()
    return _merge_interest_windows(windows), decoded


def visual_delta(previous: Frame, current: Frame) -> float:
    """Combine structural, histogram, edge, and localized change signals."""
    score, _, _ = _visual_delta_signals(previous, current)
    return score


def _visual_delta_signals(previous: Frame, current: Frame) -> tuple[float, float, float]:
    """Return the weighted score plus structural and localized text-sensitive channels."""
    if previous.shape != current.shape:
        current = cast(Frame, cv2.resize(current, (previous.shape[1], previous.shape[0])))
    difference = cast(Frame, cv2.absdiff(previous, current))
    masked_difference = _mask_micro_regions(difference)
    structural = 1.0 - structural_similarity(previous, current)
    previous_hist = cv2.calcHist([previous], [0], None, [64], [0, 256])
    current_hist = cv2.calcHist([current], [0], None, [64], [0, 256])
    cv2.normalize(previous_hist, previous_hist)
    cv2.normalize(current_hist, current_hist)
    histogram = float(cv2.compareHist(previous_hist, current_hist, cv2.HISTCMP_BHATTACHARYYA))
    previous_edges = cv2.Canny(previous, 80, 160)
    current_edges = cv2.Canny(current, 80, 160)
    edge = float(np.mean(cv2.absdiff(previous_edges, current_edges))) / 255.0
    localized = _localized_delta(masked_difference)
    score = _clamp(structural * 0.35 + histogram * 0.25 + edge * 0.25 + localized * 0.15)
    return score, structural, localized


def structural_similarity(left: Frame, right: Frame) -> float:
    """Calculate downscaled grayscale SSIM without adding a heavy model dependency."""
    left_float = left.astype(np.float32)
    right_float = right.astype(np.float32)
    c1 = 6.5025
    c2 = 58.5225
    mu_left = cv2.GaussianBlur(left_float, (11, 11), 1.5)
    mu_right = cv2.GaussianBlur(right_float, (11, 11), 1.5)
    mu_left_sq = mu_left * mu_left
    mu_right_sq = mu_right * mu_right
    mu_product = mu_left * mu_right
    sigma_left = cv2.GaussianBlur(left_float * left_float, (11, 11), 1.5) - mu_left_sq
    sigma_right = cv2.GaussianBlur(right_float * right_float, (11, 11), 1.5) - mu_right_sq
    sigma_product = cv2.GaussianBlur(left_float * right_float, (11, 11), 1.5) - mu_product
    numerator = (2 * mu_product + c1) * (2 * sigma_product + c2)
    denominator = (mu_left_sq + mu_right_sq + c1) * (sigma_left + sigma_right + c2)
    score = float(np.mean(numerator / np.maximum(denominator, 1e-6)))
    return _clamp(score)


def build_document_units(segments: Sequence[TranscriptSegment]) -> list[DocumentUnit]:
    """Build temporary coverage units from segments and one-second word pauses."""
    intervals: list[tuple[float, float]] = []
    for segment in segments:
        starts = [segment.start_seconds]
        for previous, current in zip(segment.words[:-1], segment.words[1:], strict=True):
            if current.start_seconds - previous.end_seconds >= 1.0:
                split = (previous.end_seconds + current.start_seconds) / 2
                starts.append(split)
        boundaries = starts + [segment.end_seconds]
        intervals.extend(
            (start, end)
            for start, end in zip(boundaries[:-1], boundaries[1:], strict=True)
            if end > start
        )
    merged: list[tuple[float, float]] = []
    for start, end in intervals:
        if merged:
            previous_start, previous_end = merged[-1]
            previous_duration = previous_end - previous_start
            current_duration = end - start
            if (
                (previous_duration < 2 or current_duration < 2)
                and start - previous_end <= 0.4
                and end - previous_start <= 10
            ):
                merged[-1] = (previous_start, end)
                continue
        merged.append((start, end))
    return [DocumentUnit(index, start, end) for index, (start, end) in enumerate(merged)]


def fuse_interest_windows(
    visual_windows: Sequence[InterestWindow],
    units: Sequence[DocumentUnit],
    duration_seconds: float,
) -> list[InterestWindow]:
    """Add transcript midpoint fallbacks only where visual events provide no coverage."""
    visual = _merge_interest_windows(visual_windows)
    fallbacks: list[InterestWindow] = []
    for unit in units:
        overlaps = any(
            window.start_seconds <= unit.end_seconds and window.end_seconds >= unit.start_seconds
            for window in visual
        )
        if not overlaps:
            midpoint = (unit.start_seconds + unit.end_seconds) / 2
            fallbacks.append(
                InterestWindow(
                    max(0.0, midpoint - 0.5),
                    midpoint,
                    min(duration_seconds, midpoint + 0.5),
                    0.0,
                    fallback=True,
                )
            )
    if not visual and not fallbacks and duration_seconds > 0:
        midpoint = duration_seconds / 2
        fallbacks.append(InterestWindow(0.0, midpoint, duration_seconds, 0.0, fallback=True))
    return sorted(
        [*visual, *fallbacks],
        key=lambda window: (window.start_seconds, window.peak_seconds, window.end_seconds),
    )


def candidate_times_for_windows(
    windows: Sequence[InterestWindow], duration_seconds: float
) -> list[float]:
    """Return collapsed pre-change, peak, and post-stable fine-extraction anchors."""
    values = {
        round(_clamp_time(value, duration_seconds), 3)
        for window in windows
        for value in (
            (window.peak_seconds,)
            if window.fallback
            else (window.start_seconds, window.peak_seconds, window.end_seconds)
        )
    }
    ordered: list[float] = []
    for value in sorted(values):
        if not ordered or value - ordered[-1] >= 0.1:
            ordered.append(value)
    return ordered


def extract_fine_candidates(
    video_path: Path,
    candidate_times: Sequence[float],
    windows: Sequence[InterestWindow],
    *,
    cancellation_check: CancellationCheck | None = None,
    progress_callback: ProgressCallback | None = None,
) -> list[_AdaptiveCandidate]:
    """Decode full-resolution burst neighbors and retain the best stable frame per anchor."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        capture.release()
        raise PipelineServiceError(
            "FRAME_EXTRACTION_FAILED", "The source video could not be opened."
        )
    candidates: list[_AdaptiveCandidate] = []
    try:
        for index, timestamp in enumerate(candidate_times):
            if cancellation_check is not None:
                cancellation_check()
            samples = _sample_neighbors(capture, timestamp)
            if samples:
                frame_candidate = _build_adaptive_frame_candidate(index, samples)
                window = min(windows, key=lambda item: abs(item.peak_seconds - timestamp))
                is_dialog_state = _looks_like_dialog(frame_candidate.frame)
                is_transient_state = (
                    window.transient
                    and window.peak_score >= 0.25
                    and abs(timestamp - window.peak_seconds) <= 0.25
                )
                mandatory = is_dialog_state or is_transient_state
                candidates.append(
                    _AdaptiveCandidate(
                        frame_candidate=frame_candidate,
                        visual_change_score=window.peak_score,
                        is_mandatory=mandatory,
                        selection_reason=(
                            "dialog_state"
                            if is_dialog_state
                            else "critical_transient"
                            if is_transient_state
                            else "quality"
                        ),
                        text_sensitive=window.text_sensitive,
                        decoded_frame_count=len(samples),
                    )
                )
            if progress_callback is not None:
                progress_callback("extracting_frames", index + 1, len(candidate_times))
    finally:
        capture.release()
    if candidates:
        final_candidate = max(candidates, key=_candidate_timestamp)
        final_candidate.is_mandatory = True
        final_candidate.selection_reason = "final_state"
    return candidates


def global_cluster_candidates(
    candidates: Sequence[_AdaptiveCandidate],
    *,
    ocr_engine: TesseractOcrEngine | None,
    ocr_language: str,
    ocr_root: Path,
    metrics: MetricsCollector,
    cancellation_check: CancellationCheck | None = None,
    direct_duplicate_threshold: float = 0.94,
    borderline_duplicate_threshold: float = 0.84,
    borderline_ssim_threshold: float = 0.96,
    ocr_token_jaccard_threshold: float = 0.85,
) -> list[_Cluster]:
    """Cluster non-consecutive screens globally with pHash, SSIM, and selective OCR."""
    clusters: list[_Cluster] = []
    for candidate in sorted(candidates, key=_candidate_timestamp):
        if cancellation_check is not None:
            cancellation_check()
        if (
            candidate.selection_reason == "critical_transient" or candidate.text_sensitive
        ) and ocr_engine is not None:
            tokens = _ensure_candidate_tokens(
                candidate, ocr_engine, ocr_language, ocr_root, metrics
            )
            if tokens & ERROR_TOKENS:
                candidate.is_mandatory = True
                candidate.selection_reason = "error_state"
        matched: _Cluster | None = None
        for cluster in clusters:
            representative = cluster.representative
            if representative is None or candidate.is_mandatory or representative.is_mandatory:
                continue
            similarity = perceptual_hash_similarity(
                candidate.frame_candidate.perceptual_hash,
                representative.frame_candidate.perceptual_hash,
            )
            if similarity >= direct_duplicate_threshold:
                if ocr_engine is not None and (
                    candidate.tokens is not None or representative.tokens is not None
                ):
                    left_tokens = _ensure_candidate_tokens(
                        candidate, ocr_engine, ocr_language, ocr_root, metrics
                    )
                    right_tokens = _ensure_candidate_tokens(
                        representative, ocr_engine, ocr_language, ocr_root, metrics
                    )
                    if candidate.is_mandatory or representative.is_mandatory:
                        continue
                    overlap = token_jaccard(left_tokens, right_tokens)
                    candidate.text_change_score = 1.0 - overlap
                    if overlap < ocr_token_jaccard_threshold:
                        continue
                matched = cluster
                break
            if borderline_duplicate_threshold <= similarity < direct_duplicate_threshold:
                left_gray = _resize_grayscale(candidate.frame_candidate.frame, 320)
                right_gray = _resize_grayscale(representative.frame_candidate.frame, 320)
                if structural_similarity(left_gray, right_gray) < borderline_ssim_threshold:
                    continue
                if ocr_engine is not None:
                    left_tokens = _ensure_candidate_tokens(
                        candidate, ocr_engine, ocr_language, ocr_root, metrics
                    )
                    right_tokens = _ensure_candidate_tokens(
                        representative, ocr_engine, ocr_language, ocr_root, metrics
                    )
                    if candidate.is_mandatory or representative.is_mandatory:
                        continue
                    overlap = token_jaccard(left_tokens, right_tokens)
                    candidate.text_change_score = 1.0 - overlap
                    if overlap < ocr_token_jaccard_threshold:
                        continue
                matched = cluster
                break
        if matched is None:
            matched = _Cluster([candidate], candidate)
            clusters.append(matched)
        else:
            matched.members.append(candidate)
            matched.representative = _cluster_medoid(matched.members)

    for index, cluster in enumerate(clusters, start=1):
        representative = cluster.representative
        if representative is None:
            continue
        representative.cluster_id = f"cluster-{index:04d}"
        if representative.tokens and representative.tokens & ERROR_TOKENS:
            representative.is_mandatory = True
            representative.selection_reason = "error_state"
        if representative.tokens:
            similarities = [
                token_jaccard(representative.tokens, member.tokens or frozenset())
                for member in cluster.members
                if member is not representative and member.tokens is not None
            ]
            if similarities:
                representative.text_change_score = 1.0 - max(similarities)
    return clusters


def select_coverage_candidates(
    clusters: Sequence[_Cluster],
    units: Sequence[DocumentUnit],
    duration_seconds: float,
    profile: AdaptiveProfile,
    budget_profile: str,
    *,
    max_alternatives: int,
) -> list[_AdaptiveCandidate]:
    """Select mandatory and high-marginal-utility representatives within the mode budget."""
    representatives = [cluster.representative for cluster in clusters if cluster.representative]
    if not representatives:
        return []
    minutes = max(duration_seconds / 60.0, 1 / 60)
    multiplier = BUDGET_MULTIPLIERS.get(budget_profile, 1.0)
    target = max(1, math.ceil(minutes * profile.target_images_per_minute * multiplier))
    hard_cap = max(target, math.ceil(minutes * profile.hard_cap_images_per_minute))
    selected = [candidate for candidate in representatives if candidate.is_mandatory]
    uncovered = set(range(len(units)))
    for candidate in selected:
        uncovered -= _covered_units(candidate, units)

    non_mandatory_count = 0
    remaining = [candidate for candidate in representatives if candidate not in selected]
    while remaining and non_mandatory_count < hard_cap:
        ranked = sorted(
            (
                (_candidate_utility(candidate, selected, units, uncovered), candidate)
                for candidate in remaining
            ),
            key=lambda item: (-item[0], _candidate_timestamp(item[1])),
        )
        utility, candidate = ranked[0]
        coverage = _covered_units(candidate, units)
        if non_mandatory_count >= target and not (coverage & uncovered or utility >= 0.75):
            break
        candidate.selection_reason = "coverage" if coverage & uncovered else "visual_novelty"
        candidate.coverage_score = len(coverage & uncovered) / max(1, len(units))
        candidate.role_rank_reason = candidate.selection_reason
        selected.append(candidate)
        remaining.remove(candidate)
        uncovered -= coverage
        non_mandatory_count += 1

    retained = list(selected)
    retained_automatic_count = sum(not candidate.is_mandatory for candidate in retained)
    for unit in units:
        ranked_for_unit = sorted(
            representatives,
            key=lambda candidate: (
                -_unit_relevance(candidate, unit),
                -candidate.frame_candidate.quality_score,
                _candidate_timestamp(candidate),
            ),
        )
        added_alternatives = 0
        for candidate in ranked_for_unit:
            if candidate in retained:
                continue
            if added_alternatives >= max_alternatives or retained_automatic_count >= hard_cap:
                break
            relevance = _unit_relevance(candidate, unit)
            if relevance <= 0:
                continue
            candidate.selection_reason = "alternative"
            candidate.role_rank_reason = "temporal_alternative"
            candidate.coverage_score = relevance
            retained.append(candidate)
            retained_automatic_count += 1
            added_alternatives += 1
    return retained


def _persist_thumbnail(
    candidate: _AdaptiveCandidate, output_root: Path, index: int
) -> PersistedFrameCandidate:
    """Persist one selected representative as a lightweight WebP review derivative."""
    destination = output_root / f"candidate-{index + 1:06d}.webp"
    write_thumbnail_atomic(candidate.frame_candidate.frame, destination)
    height, width = candidate.frame_candidate.frame.shape[:2]
    return PersistedFrameCandidate(
        timestamp_seconds=candidate.frame_candidate.timestamp_seconds,
        image_path=destination,
        stability=candidate.frame_candidate.stability,
        sharpness=candidate.frame_candidate.sharpness,
        exposure_penalty=candidate.frame_candidate.exposure_penalty,
        quality_score=candidate.frame_candidate.quality_score,
        perceptual_hash=candidate.frame_candidate.perceptual_hash,
        source_index=candidate.frame_candidate.source_index,
        width=width,
        height=height,
        materialization_state="thumbnail_only",
        selection_reason=candidate.selection_reason,
        visual_change_score=candidate.visual_change_score,
        text_change_score=candidate.text_change_score,
        cluster_id=candidate.cluster_id,
        is_mandatory=candidate.is_mandatory,
        coverage_score=candidate.coverage_score,
        role_rank_reason=candidate.role_rank_reason,
    )


def _ensure_candidate_tokens(
    candidate: _AdaptiveCandidate,
    engine: TesseractOcrEngine,
    language: str,
    ocr_root: Path,
    metrics: MetricsCollector,
) -> frozenset[str]:
    """Run OCR at most once for a provisional candidate and keep tokens only in memory."""
    if candidate.tokens is not None:
        return candidate.tokens
    ocr_root.mkdir(parents=True, exist_ok=True)
    image_path = ocr_root / f"candidate-{candidate.frame_candidate.source_index:06d}.png"
    write_png_atomic(candidate.frame_candidate.frame, image_path)
    candidate.tokens = engine.recognize_tokens(image_path, language)
    if candidate.tokens & ERROR_TOKENS:
        candidate.is_mandatory = True
        candidate.selection_reason = "error_state"
    metrics.increment("ocrTriggeredCount")
    return candidate.tokens


def _cluster_medoid(members: Sequence[_AdaptiveCandidate]) -> _AdaptiveCandidate:
    """Choose the most central perceptual hash with quality and timestamp tie-breaking."""
    return min(
        members,
        key=lambda candidate: (
            -sum(
                perceptual_hash_similarity(
                    candidate.frame_candidate.perceptual_hash,
                    other.frame_candidate.perceptual_hash,
                )
                for other in members
            ),
            -candidate.frame_candidate.quality_score,
            candidate.frame_candidate.timestamp_seconds,
        ),
    )


def _candidate_utility(
    candidate: _AdaptiveCandidate,
    selected: Sequence[_AdaptiveCandidate],
    units: Sequence[DocumentUnit],
    uncovered: set[int],
) -> float:
    """Calculate explainable marginal coverage, novelty, relevance, and quality utility."""
    coverage = len(_covered_units(candidate, units) & uncovered) / max(1, len(units))
    visual_novelty = 1.0
    if selected:
        visual_novelty = 1.0 - max(
            perceptual_hash_similarity(
                candidate.frame_candidate.perceptual_hash,
                item.frame_candidate.perceptual_hash,
            )
            for item in selected
        )
    text_novelty = candidate.text_change_score if candidate.text_change_score is not None else 0.5
    relevance = max((_unit_relevance(candidate, unit) for unit in units), default=0.0)
    return _clamp(
        coverage * 0.30
        + visual_novelty * 0.20
        + text_novelty * 0.20
        + relevance * 0.15
        + candidate.frame_candidate.quality_score * 0.15
    )


def _covered_units(candidate: _AdaptiveCandidate, units: Sequence[DocumentUnit]) -> set[int]:
    """Return document units directly or closely covered by a representative timestamp."""
    direct = {
        unit.index
        for unit in units
        if unit.start_seconds <= _candidate_timestamp(candidate) <= unit.end_seconds
    }
    if direct or not units:
        return direct
    nearest = min(
        units,
        key=lambda unit: _distance_to_unit(_candidate_timestamp(candidate), unit),
    )
    return (
        {nearest.index}
        if _distance_to_unit(_candidate_timestamp(candidate), nearest) <= 1.0
        else set()
    )


def _unit_relevance(candidate: _AdaptiveCandidate, unit: DocumentUnit) -> float:
    """Score temporal relevance to one document unit on the inclusive zero-to-one range."""
    midpoint = (unit.start_seconds + unit.end_seconds) / 2
    half_duration = max((unit.end_seconds - unit.start_seconds) / 2, 0.5)
    return _clamp(1.0 - abs(_candidate_timestamp(candidate) - midpoint) / half_duration)


def _distance_to_unit(timestamp: float, unit: DocumentUnit) -> float:
    """Measure the shortest distance from a timestamp to a closed document interval."""
    if unit.start_seconds <= timestamp <= unit.end_seconds:
        return 0.0
    return min(abs(timestamp - unit.start_seconds), abs(timestamp - unit.end_seconds))


def _sample_neighbors(capture: cv2.VideoCapture, timestamp: float) -> list[SampledFrame]:
    """Decode the unique minus, exact, and plus 0.2-second fine samples."""
    samples: list[SampledFrame] = []
    for sample_time in sorted({max(0.0, timestamp - 0.2), timestamp, timestamp + 0.2}):
        capture.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1_000)
        success, frame = capture.read()
        if success and frame is not None and frame.size:
            samples.append(SampledFrame(sample_time, cast(Frame, frame)))
    return samples


def _build_adaptive_frame_candidate(index: int, samples: Sequence[SampledFrame]) -> FrameCandidate:
    """Choose the clearest neighbor and apply the adaptive explainable quality formula."""
    selected = max(samples, key=lambda sample: _initial_frame_quality(sample.frame))
    sharpness = calculate_laplacian_sharpness(selected.frame)
    normalized_sharpness = sharpness / (sharpness + 100.0)
    exposure_penalty = calculate_exposure_penalty(selected.frame)
    selected_gray = _resize_grayscale(selected.frame, 640)
    stability = float(
        np.mean(
            [
                structural_similarity(selected_gray, _resize_grayscale(sample.frame, 640))
                for sample in samples
            ]
        )
    )
    crop_safety = _border_crop_safety(selected.frame)
    quality = _clamp(
        normalized_sharpness * 0.35
        + (1.0 - exposure_penalty) * 0.20
        + stability * 0.30
        + crop_safety * 0.15
    )
    return FrameCandidate(
        timestamp_seconds=selected.timestamp_seconds,
        frame=selected.frame,
        stability=stability,
        sharpness=sharpness,
        exposure_penalty=exposure_penalty,
        quality_score=quality,
        perceptual_hash=compute_perceptual_hash(selected.frame),
        source_index=index,
    )


def _initial_frame_quality(frame: Frame) -> float:
    """Rank burst neighbors before cross-neighbor stability is available."""
    sharpness = calculate_laplacian_sharpness(frame)
    return sharpness / (sharpness + 100.0) * 0.6 + (1 - calculate_exposure_penalty(frame)) * 0.4


def _border_crop_safety(frame: Frame) -> float:
    """Penalize heavily clipped outer borders that commonly indicate bad transitions or crops."""
    gray = cast(Frame, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)) if frame.ndim == 3 else frame
    height, width = gray.shape[:2]
    border_height = max(1, round(height * 0.03))
    border_width = max(1, round(width * 0.03))
    border = np.concatenate(
        (
            gray[:border_height, :].ravel(),
            gray[-border_height:, :].ravel(),
            gray[:, :border_width].ravel(),
            gray[:, -border_width:].ravel(),
        )
    )
    clipped = float(np.mean((border <= 3) | (border >= 252)))
    return _clamp(1.0 - clipped)


def _looks_like_dialog(frame: Frame) -> bool:
    """Recognize a bounded centered rectangular overlay as mandatory dialog evidence."""
    gray = cast(Frame, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)) if frame.ndim == 3 else frame
    height, width = gray.shape[:2]
    if height < 32 or width < 32:
        return False
    edges = cv2.Canny(gray, 60, 150)
    closed = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)),
    )
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    frame_area = float(width * height)
    for contour in sorted(contours, key=cv2.contourArea, reverse=True):
        x, y, box_width, box_height = cv2.boundingRect(contour)
        box_area = float(box_width * box_height)
        area_ratio = box_area / frame_area
        if area_ratio < 0.08 or area_ratio > 0.72:
            continue
        if x <= width * 0.02 or y <= height * 0.02:
            continue
        if x + box_width >= width * 0.98 or y + box_height >= height * 0.98:
            continue
        center_x = x + box_width / 2
        center_y = y + box_height / 2
        if abs(center_x - width / 2) > width * 0.18:
            continue
        if abs(center_y - height / 2) > height * 0.18:
            continue
        perimeter = cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if not 4 <= len(polygon) <= 8:
            continue
        contour_fill = cv2.contourArea(contour) / max(box_area, 1.0)
        if contour_fill < 0.55:
            continue
        inset_x = max(1, round(box_width * 0.08))
        inset_y = max(1, round(box_height * 0.08))
        inside = gray[
            y + inset_y : y + box_height - inset_y,
            x + inset_x : x + box_width - inset_x,
        ]
        outside_mask = np.ones(gray.shape, dtype=bool)
        outside_mask[y : y + box_height, x : x + box_width] = False
        outside = gray[outside_mask]
        if (
            inside.size
            and outside.size
            and abs(float(np.mean(inside)) - float(np.mean(outside))) >= 10
        ):
            return True
    return False


def _resize_grayscale(frame: Frame, long_edge: int) -> Frame:
    """Convert and downscale one frame while preserving its aspect ratio."""
    gray = cast(Frame, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)) if frame.ndim == 3 else frame
    height, width = gray.shape[:2]
    scale = min(1.0, long_edge / max(width, height))
    if scale == 1.0:
        return gray
    return cast(
        Frame,
        cv2.resize(
            gray,
            (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        ),
    )


def _mask_micro_regions(difference: Frame) -> Frame:
    """Remove tiny short-lived cursor-like components from the masked change channel."""
    binary = np.where(difference >= 18, 255, 0).astype(np.uint8)
    component_count, raw_labels, raw_statistics, _ = cv2.connectedComponentsWithStats(
        binary, connectivity=8
    )
    labels = cast(Any, raw_labels)
    statistics = cast(Any, raw_statistics)
    maximum_area = max(1, round(difference.size * 0.0015))
    masked = difference.copy()
    for label in range(1, component_count):
        if statistics[label, cv2.CC_STAT_AREA] <= maximum_area:
            masked[labels == label] = 0
    return masked


def _localized_delta(difference: Frame) -> float:
    """Return the strongest normalized mean change across a four-by-four region grid."""
    height, width = difference.shape[:2]
    values = []
    for row in range(4):
        for column in range(4):
            top = row * height // 4
            bottom = (row + 1) * height // 4
            left = column * width // 4
            right = (column + 1) * width // 4
            region = difference[top:bottom, left:right]
            if region.size:
                values.append(float(np.mean(region)) / 255.0)
    return max(values, default=0.0)


def _adaptive_threshold(scores: Sequence[float]) -> float:
    """Calculate the clamped rolling median-plus-three-MAD event threshold."""
    if len(scores) < 5:
        return 0.12
    values = np.asarray(scores, dtype=np.float32)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    return _clamp(median + 3 * mad, 0.12, 0.45)


def _is_transient_event(
    event_started_at: float | None,
    last_change_at: float | None,
    peak_score: float,
) -> bool:
    """Classify the visual event itself without counting its pre-change or stable buffers."""
    if event_started_at is None or last_change_at is None:
        return False
    return last_change_at - event_started_at <= 3.0 and peak_score >= 0.25


def _merge_interest_windows(windows: Sequence[InterestWindow]) -> list[InterestWindow]:
    """Merge overlapping or one-second-adjacent windows without losing the strongest peak."""
    merged: list[InterestWindow] = []
    for window in sorted(windows, key=lambda item: (item.start_seconds, item.end_seconds)):
        if not merged or window.start_seconds - merged[-1].end_seconds > 1.0:
            merged.append(window)
            continue
        previous = merged[-1]
        peak = window if window.peak_score > previous.peak_score else previous
        merged[-1] = InterestWindow(
            previous.start_seconds,
            peak.peak_seconds,
            max(previous.end_seconds, window.end_seconds),
            peak.peak_score,
            previous.transient or window.transient,
            previous.fallback and window.fallback,
            previous.text_sensitive or window.text_sensitive,
        )
    return merged


def _candidate_timestamp(candidate: _AdaptiveCandidate) -> float:
    """Return the selected source timestamp used for deterministic ordering."""
    return candidate.frame_candidate.timestamp_seconds


def _clamp_time(value: float, duration_seconds: float) -> float:
    """Clamp a candidate timestamp inside the validated source duration."""
    return max(0.0, min(duration_seconds, value))


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Clamp one normalized score to a documented inclusive interval."""
    return max(minimum, min(maximum, value))
