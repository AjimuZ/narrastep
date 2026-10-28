"""Local transcription and screenshot analysis utilities for Narrastep."""

from __future__ import annotations

import json
import math
import os
import wave
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol, cast

import cv2
import numpy as np
from numpy.typing import NDArray

type Frame = NDArray[np.uint8]


class PipelineConfig(Protocol):
    """Describe processing settings consumed by the standalone pipeline helpers."""

    @property
    def whisper_model_path(self) -> str | Path:
        """Return the explicit local Whisper model directory."""

        ...

    @property
    def whisper_device(self) -> str:
        """Return the configured Whisper execution device."""

        ...

    @property
    def whisper_compute_type(self) -> str:
        """Return the configured Whisper numeric compute type."""

        ...

    @property
    def frame_interval_seconds(self) -> float:
        """Return the regular screenshot sampling interval."""

        ...

    @property
    def frame_neighbor_seconds(self) -> float:
        """Return the offset used to sample neighboring stability frames."""

        ...

    @property
    def duplicate_similarity_threshold(self) -> float:
        """Return the perceptual similarity threshold for adjacent frames."""

        ...

    @property
    def screenshot_alternatives(self) -> int:
        """Return the maximum alternatives retained for each segment."""

        ...

    @property
    def screenshot_selection_strategy(self) -> str:
        """Return the versioned screenshot selection strategy."""

        ...

    @property
    def ocr_mode(self) -> str:
        """Return whether selective OCR may assist screenshot comparison."""

        ...

    @property
    def image_budget_profile(self) -> str:
        """Return the automatic evidence-density profile."""

        ...

    @property
    def tesseract_path(self) -> str:
        """Return the configured local Tesseract executable."""

        ...

    @property
    def adaptive_direct_duplicate_threshold(self) -> float:
        """Return the direct adaptive pHash duplicate boundary."""

        ...

    @property
    def adaptive_borderline_duplicate_threshold(self) -> float:
        """Return the adaptive pHash boundary that triggers secondary comparison."""

        ...

    @property
    def adaptive_borderline_ssim_threshold(self) -> float:
        """Return the structural threshold used for borderline duplicate pairs."""

        ...

    @property
    def adaptive_ocr_token_jaccard_threshold(self) -> float:
        """Return the token overlap required after selective OCR comparison."""

        ...

    @property
    def mock_processing(self) -> bool:
        """Return whether deterministic test-only processing is enabled."""

        ...

    @property
    def environment(self) -> str:
        """Return the validated runtime environment controlling test-only behavior."""

        ...


class PipelineServiceError(RuntimeError):
    """Represent a stable processing failure suitable for API error mapping."""

    def __init__(self, code: str, message: str) -> None:
        """Create a pipeline error without exposing transcript or model diagnostics."""

        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class TranscriptWord:
    """Represent one immutable Whisper word and its source timing."""

    text: str
    start_seconds: float
    end_seconds: float
    probability: float | None

    def to_dict(self) -> dict[str, str | float | None]:
        """Serialize a word for the immutable raw transcript artifact."""

        return {
            "text": self.text,
            "startSeconds": self.start_seconds,
            "endSeconds": self.end_seconds,
            "probability": self.probability,
        }


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    """Represent one immutable source segment returned by transcription."""

    source_index: int
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None
    words: tuple[TranscriptWord, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Serialize a source segment while retaining all available word timings."""

        return {
            "sourceIndex": self.source_index,
            "startSeconds": self.start_seconds,
            "endSeconds": self.end_seconds,
            "text": self.text,
            "confidence": self.confidence,
            "words": [word.to_dict() for word in self.words],
        }


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    """Contain the complete immutable result of one local transcription run."""

    language: str
    language_probability: float | None
    duration_seconds: float
    segments: tuple[TranscriptSegment, ...]
    model_path: str
    mock: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize the transcription result for raw-transcript.json."""

        return {
            "language": self.language,
            "languageProbability": self.language_probability,
            "durationSeconds": self.duration_seconds,
            "modelPath": self.model_path,
            "mock": self.mock,
            "segments": [segment.to_dict() for segment in self.segments],
        }


@dataclass(frozen=True, slots=True)
class SampledFrame:
    """Pair a decoded video frame with its actual requested timestamp."""

    timestamp_seconds: float
    frame: Frame = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class FrameCandidate:
    """Contain one selected screenshot candidate and its explainable metrics."""

    timestamp_seconds: float
    frame: Frame = field(repr=False, compare=False)
    stability: float
    sharpness: float
    exposure_penalty: float
    quality_score: float
    perceptual_hash: str
    source_index: int

    def to_metadata_dict(self) -> dict[str, str | float | int]:
        """Serialize candidate metrics without embedding the decoded image bytes."""

        return {
            "timestampSeconds": self.timestamp_seconds,
            "stability": self.stability,
            "sharpness": self.sharpness,
            "exposurePenalty": self.exposure_penalty,
            "qualityScore": self.quality_score,
            "perceptualHash": self.perceptual_hash,
            "sourceIndex": self.source_index,
        }


@dataclass(frozen=True, slots=True)
class PersistedFrameCandidate:
    """Carry screenshot metrics and a staged PNG without retaining decoded pixels."""

    timestamp_seconds: float
    image_path: Path
    stability: float
    sharpness: float
    exposure_penalty: float
    quality_score: float
    perceptual_hash: str
    source_index: int
    width: int
    height: int
    materialization_state: str = "png_ready"
    selection_reason: str = "legacy"
    visual_change_score: float | None = None
    text_change_score: float | None = None
    cluster_id: str | None = None
    is_mandatory: bool = False
    coverage_score: float | None = None
    role_rank_reason: str | None = None


type RankableFrameCandidate = FrameCandidate | PersistedFrameCandidate


@dataclass(frozen=True, slots=True)
class RankedFrameCandidate:
    """Pair a screenshot candidate with its segment-specific ranking score."""

    candidate: RankableFrameCandidate
    score: float
    temporal_proximity: float


@dataclass(frozen=True, slots=True)
class SegmentFrameSelection:
    """Contain one primary screenshot and the bounded alternatives for a segment."""

    segment_id: str
    primary: RankedFrameCandidate | None
    alternatives: tuple[RankedFrameCandidate, ...]


def transcribe_audio(
    audio_path: str | Path,
    *,
    config: PipelineConfig | None = None,
    model_path: str | Path | None = None,
    source_language: str = "auto",
    device: str | None = None,
    compute_type: str | None = None,
    beam_size: int = 5,
    duration_seconds: float | None = None,
    model_factory: Callable[..., Any] | None = None,
    cancellation_check: Callable[[], None] | None = None,
    progress_callback: Callable[[float], None] | None = None,
) -> TranscriptionResult:
    """Transcribe PCM audio with an explicitly local Whisper model or test mock."""

    audio = Path(audio_path)
    if not audio.is_file():
        raise PipelineServiceError(
            "TRANSCRIPTION_INPUT_MISSING", "The prepared audio is unavailable."
        )
    if beam_size <= 0:
        raise ValueError("The Whisper beam size must be greater than zero.")

    use_mock = bool(_setting(config, "mock_processing", False))
    if use_mock:
        if str(_setting(config, "environment", "development")) != "test":
            raise PipelineServiceError(
                "MOCK_PROCESSING_FORBIDDEN", "Mock transcription is restricted to automated tests."
            )
        if cancellation_check is not None:
            cancellation_check()
        result = _build_mock_transcription(audio, source_language, duration_seconds)
        if progress_callback is not None:
            progress_callback(1.0)
        return result

    configured_model_path = model_path or _setting(config, "whisper_model_path", None)
    if configured_model_path is None:
        raise PipelineServiceError(
            "TRANSCRIPTION_MODEL_FAILED", "The local transcription model is unavailable."
        )
    local_model = Path(configured_model_path)
    if model_factory is None and not local_model.exists():
        raise PipelineServiceError(
            "TRANSCRIPTION_MODEL_FAILED", "The local transcription model is unavailable."
        )

    selected_device = device or str(_setting(config, "whisper_device", "cpu"))
    selected_compute_type = compute_type or str(_setting(config, "whisper_compute_type", "int8"))
    factory = model_factory or _load_whisper_model_factory()

    try:
        model = factory(
            str(local_model),
            device=selected_device,
            compute_type=selected_compute_type,
            local_files_only=True,
        )
        raw_segments, info = model.transcribe(
            str(audio),
            language=None if source_language == "auto" else source_language,
            beam_size=beam_size,
            word_timestamps=True,
            vad_filter=True,
        )
        expected_duration = (
            _positive_float(getattr(info, "duration", None))
            or _positive_float(duration_seconds)
            or _wav_duration(audio)
        )
        converted_segments: list[TranscriptSegment] = []
        for index, raw_segment in enumerate(raw_segments):
            if cancellation_check is not None:
                cancellation_check()
            converted = _convert_whisper_segment(index, raw_segment)
            converted_segments.append(converted)
            if progress_callback is not None:
                progress_callback(_clamp(converted.end_seconds / expected_duration))
        segments = tuple(converted_segments)
    except PipelineServiceError:
        raise
    except Exception as exc:
        raise PipelineServiceError(
            "TRANSCRIPTION_MODEL_FAILED", "The audio could not be transcribed locally."
        ) from exc

    detected_language = str(getattr(info, "language", None) or source_language)
    if detected_language == "auto":
        detected_language = "und"
    detected_probability = _bounded_probability(getattr(info, "language_probability", None))
    detected_duration = _positive_float(getattr(info, "duration", None))
    result_duration = detected_duration or _segments_duration(segments) or _wav_duration(audio)
    return TranscriptionResult(
        language=detected_language,
        language_probability=detected_probability,
        duration_seconds=result_duration,
        segments=segments,
        model_path=local_model.name,
        mock=False,
    )


def write_raw_transcript(result: TranscriptionResult, output_path: str | Path) -> Path:
    """Persist an immutable transcript JSON artifact through an atomic replacement."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.partial")
    try:
        partial.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def generate_frame_times(
    duration_seconds: float,
    segments: Iterable[object] = (),
    *,
    config: PipelineConfig | None = None,
    interval_seconds: float | None = None,
) -> list[float]:
    """Generate interval samples plus 10, 50, and 90 percent segment anchors."""

    duration = _positive_float(duration_seconds)
    interval = (
        interval_seconds
        if interval_seconds is not None
        else float(_setting(config, "frame_interval_seconds", 3.0))
    )
    if duration is None:
        raise ValueError("Video duration must be greater than zero.")
    if interval <= 0 or not math.isfinite(interval):
        raise ValueError("Frame interval must be greater than zero.")

    last_decodable_time = max(0.0, duration - 0.001)
    times = {0.0}
    interval_index = 1
    while interval_index * interval < duration:
        times.add(min(interval_index * interval, last_decodable_time))
        interval_index += 1

    for segment in segments:
        start, end = _segment_bounds(segment)
        if start < 0 or end < start:
            raise ValueError("Segment timing must be ordered and non-negative.")
        bounded_start = min(start, last_decodable_time)
        bounded_end = min(end, last_decodable_time)
        segment_duration = max(0.0, bounded_end - bounded_start)
        for position in (0.1, 0.5, 0.9):
            times.add(bounded_start + segment_duration * position)

    return sorted({round(max(0.0, min(time, last_decodable_time)), 3) for time in times})


def sample_neighboring_frames(
    video_path: str | Path,
    timestamp_seconds: float,
    *,
    config: PipelineConfig | None = None,
    neighbor_offset_seconds: float | None = None,
) -> tuple[SampledFrame, ...]:
    """Decode a target frame and its immediate neighbors for quality analysis."""

    video = Path(video_path)
    neighbor_offset = (
        neighbor_offset_seconds
        if neighbor_offset_seconds is not None
        else float(_setting(config, "frame_neighbor_seconds", 0.2))
    )
    if not video.is_file():
        raise PipelineServiceError("MEDIA_FILE_MISSING", "The source video is unavailable.")
    if (
        timestamp_seconds < 0
        or neighbor_offset < 0
        or not math.isfinite(timestamp_seconds)
        or not math.isfinite(neighbor_offset)
    ):
        raise ValueError("Frame timestamps and neighbor offsets cannot be negative.")

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        capture.release()
        raise PipelineServiceError(
            "FRAME_EXTRACTION_FAILED", "Screenshots could not be extracted from the video."
        )
    try:
        return tuple(_sample_frames_with_capture(capture, timestamp_seconds, neighbor_offset))
    finally:
        capture.release()


def extract_frame_candidates(
    video_path: str | Path,
    timestamps: Iterable[float],
    *,
    config: PipelineConfig | None = None,
    neighbor_offset_seconds: float | None = None,
) -> list[FrameCandidate]:
    """Extract one best-quality candidate for every decodable requested time."""

    video = Path(video_path)
    neighbor_offset = (
        neighbor_offset_seconds
        if neighbor_offset_seconds is not None
        else float(_setting(config, "frame_neighbor_seconds", 0.2))
    )
    if not video.is_file():
        raise PipelineServiceError("MEDIA_FILE_MISSING", "The source video is unavailable.")
    if neighbor_offset < 0 or not math.isfinite(neighbor_offset):
        raise ValueError("The neighboring frame offset cannot be negative.")
    requested_times = sorted({float(timestamp) for timestamp in timestamps})
    if any(timestamp < 0 or not math.isfinite(timestamp) for timestamp in requested_times):
        raise ValueError("Frame timestamps must be finite and non-negative.")

    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        capture.release()
        raise PipelineServiceError(
            "FRAME_EXTRACTION_FAILED", "Screenshots could not be extracted from the video."
        )

    candidates: list[FrameCandidate] = []
    try:
        for source_index, timestamp in enumerate(requested_times):
            samples = _sample_frames_with_capture(capture, timestamp, neighbor_offset)
            if samples:
                candidates.append(_build_frame_candidate(source_index, samples))
    finally:
        capture.release()
    return candidates


def extract_deduplicated_frames_to_directory(
    video_path: str | Path,
    timestamps: Iterable[float],
    output_directory: str | Path,
    *,
    config: PipelineConfig | None = None,
    neighbor_offset_seconds: float | None = None,
    cancellation_check: Callable[[], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    decoded_frame_callback: Callable[[int], None] | None = None,
    provisional_candidate_callback: Callable[[], None] | None = None,
) -> list[PersistedFrameCandidate]:
    """Decode, consecutively deduplicate, and stage frames with constant image memory."""
    video = Path(video_path)
    output_root = Path(output_directory)
    neighbor_offset = (
        neighbor_offset_seconds
        if neighbor_offset_seconds is not None
        else float(_setting(config, "frame_neighbor_seconds", 0.2))
    )
    similarity_threshold = float(_setting(config, "duplicate_similarity_threshold", 0.92))
    if not video.is_file():
        raise PipelineServiceError("MEDIA_FILE_MISSING", "The source video is unavailable.")
    if neighbor_offset < 0 or not math.isfinite(neighbor_offset):
        raise ValueError("The neighboring frame offset cannot be negative.")
    if not 0 <= similarity_threshold <= 1:
        raise ValueError("Duplicate similarity threshold must be between zero and one.")
    requested_times = sorted({float(timestamp) for timestamp in timestamps})
    if any(timestamp < 0 or not math.isfinite(timestamp) for timestamp in requested_times):
        raise ValueError("Frame timestamps must be finite and non-negative.")

    output_root.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        capture.release()
        raise PipelineServiceError(
            "FRAME_EXTRACTION_FAILED", "Screenshots could not be extracted from the video."
        )

    retained: list[PersistedFrameCandidate] = []
    cluster_best: FrameCandidate | None = None
    previous_hash: str | None = None
    try:
        for source_index, timestamp in enumerate(requested_times):
            if cancellation_check is not None:
                cancellation_check()
            samples = _sample_frames_with_capture(capture, timestamp, neighbor_offset)
            if decoded_frame_callback is not None:
                decoded_frame_callback(len(samples))
            if samples:
                if provisional_candidate_callback is not None:
                    provisional_candidate_callback()
                candidate = _build_frame_candidate(source_index, samples)
                if (
                    previous_hash is None
                    or perceptual_hash_similarity(previous_hash, candidate.perceptual_hash)
                    < similarity_threshold
                ):
                    if cluster_best is not None:
                        retained.append(
                            _persist_frame_candidate(cluster_best, output_root, len(retained))
                        )
                    cluster_best = candidate
                elif cluster_best is None or candidate.quality_score > cluster_best.quality_score:
                    cluster_best = candidate
                previous_hash = candidate.perceptual_hash
            if progress_callback is not None:
                progress_callback(source_index + 1, len(requested_times))
        if cluster_best is not None:
            retained.append(_persist_frame_candidate(cluster_best, output_root, len(retained)))
    finally:
        capture.release()
    return retained


def calculate_histogram_stability(frames: Sequence[Frame]) -> float:
    """Measure visual stability from correlations between consecutive histograms."""

    if not frames:
        return 0.0
    if len(frames) == 1:
        return 1.0
    histograms = [_normalized_grayscale_histogram(frame) for frame in frames]
    correlations = [
        cv2.compareHist(previous, current, cv2.HISTCMP_CORREL)
        for previous, current in zip(histograms[:-1], histograms[1:], strict=True)
    ]
    normalized = [(float(correlation) + 1.0) / 2.0 for correlation in correlations]
    return _clamp(float(np.mean(normalized)))


def calculate_laplacian_sharpness(frame: Frame) -> float:
    """Measure edge definition using the variance of a grayscale Laplacian."""

    gray = _grayscale(frame)
    return max(0.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()))


def calculate_exposure_penalty(frame: Frame) -> float:
    """Penalize clipped dark, clipped bright, and severely imbalanced frames."""

    gray = _grayscale(frame)
    black_ratio = float(np.mean(gray <= 10))
    white_ratio = float(np.mean(gray >= 245))
    clipped_penalty = min(1.0, (black_ratio + white_ratio) * 1.5)
    brightness_distance = abs(float(np.mean(gray)) - 127.5)
    imbalance_penalty = max(0.0, (brightness_distance - 70.0) / 57.5)
    return _clamp(clipped_penalty * 0.75 + imbalance_penalty * 0.25)


def compute_perceptual_hash(frame: Frame) -> str:
    """Compute a deterministic 64-bit DCT perceptual hash as hexadecimal text."""

    gray = _grayscale(frame)
    resized = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    low_frequencies = cv2.dct(resized)[:8, :8]
    comparison_values = low_frequencies.flatten()[1:]
    median = float(np.median(comparison_values)) if comparison_values.size else 0.0
    bits = (low_frequencies.flatten() >= median).astype(np.uint8)
    hash_value = 0
    for bit in bits:
        hash_value = (hash_value << 1) | int(bit)
    return f"{hash_value:016x}"


def perceptual_hash_similarity(left_hash: str | int, right_hash: str | int) -> float:
    """Return normalized similarity between two 64-bit perceptual hashes."""

    left = _parse_perceptual_hash(left_hash)
    right = _parse_perceptual_hash(right_hash)
    hamming_distance = (left ^ right).bit_count()
    return 1.0 - hamming_distance / 64.0


def deduplicate_consecutive_candidates(
    candidates: Sequence[FrameCandidate],
    *,
    config: PipelineConfig | None = None,
    similarity_threshold: float | None = None,
) -> list[FrameCandidate]:
    """Collapse only consecutive similar screens and retain each cluster's best frame."""

    threshold = (
        float(_setting(config, "duplicate_similarity_threshold", 0.92))
        if similarity_threshold is None
        else similarity_threshold
    )
    if not 0 <= threshold <= 1:
        raise ValueError("Duplicate similarity threshold must be between zero and one.")
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda candidate: candidate.timestamp_seconds)
    retained: list[FrameCandidate] = []
    cluster = [ordered[0]]
    previous = ordered[0]
    for candidate in ordered[1:]:
        similarity = perceptual_hash_similarity(previous.perceptual_hash, candidate.perceptual_hash)
        if similarity >= threshold:
            cluster.append(candidate)
        else:
            retained.append(_best_cluster_candidate(cluster))
            cluster = [candidate]
        previous = candidate
    retained.append(_best_cluster_candidate(cluster))
    return retained


def rank_candidates_for_segment(
    segment: object,
    candidates: Sequence[RankableFrameCandidate],
    *,
    config: PipelineConfig | None = None,
    max_alternatives: int | None = None,
) -> SegmentFrameSelection:
    """Rank candidates with 70 percent temporal proximity and 30 percent quality."""

    alternative_limit = (
        int(_setting(config, "screenshot_alternatives", 4))
        if max_alternatives is None
        else max_alternatives
    )
    if alternative_limit < 0:
        raise ValueError("The screenshot alternative limit cannot be negative.")
    segment_id = _segment_identifier(segment)
    start, end = _segment_bounds(segment)
    if start < 0 or end < start:
        raise ValueError("Segment timing must be ordered and non-negative.")
    if not candidates:
        return SegmentFrameSelection(segment_id, None, ())

    midpoint = start + (end - start) / 2.0
    half_duration = max((end - start) / 2.0, 0.001)
    in_segment = [
        candidate for candidate in candidates if start <= candidate.timestamp_seconds <= end
    ]
    candidate_pool = in_segment or list(candidates)
    ranked = [_rank_candidate(candidate, midpoint, half_duration) for candidate in candidate_pool]
    ranked.sort(
        key=lambda item: (
            -item.score,
            abs(item.candidate.timestamp_seconds - midpoint),
            item.candidate.timestamp_seconds,
        )
    )
    return SegmentFrameSelection(
        segment_id=segment_id,
        primary=ranked[0],
        alternatives=tuple(ranked[1 : alternative_limit + 1]),
    )


def rank_candidates_for_segments(
    segments: Iterable[object],
    candidates: Sequence[RankableFrameCandidate],
    *,
    config: PipelineConfig | None = None,
    max_alternatives: int | None = None,
) -> list[SegmentFrameSelection]:
    """Produce independent screenshot selections while allowing evidence reuse."""

    return [
        rank_candidates_for_segment(
            segment,
            candidates,
            config=config,
            max_alternatives=max_alternatives,
        )
        for segment in segments
    ]


def write_frame_png(candidate: FrameCandidate, output_path: str | Path) -> Path:
    """Write a selected frame as an atomic PNG derivative."""

    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    encoded, payload = cv2.imencode(".png", candidate.frame, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    if not encoded:
        raise PipelineServiceError("FRAME_EXTRACTION_FAILED", "A screenshot could not be encoded.")
    partial = destination.with_name(f".{destination.name}.partial")
    try:
        partial.write_bytes(payload.tobytes())
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def _persist_frame_candidate(
    candidate: FrameCandidate, output_root: Path, retained_index: int
) -> PersistedFrameCandidate:
    """Persist one retained cluster representative and release its decoded frame afterward."""
    destination = output_root / f"candidate-{retained_index + 1:06d}.png"
    write_frame_png(candidate, destination)
    height, width = candidate.frame.shape[:2]
    return PersistedFrameCandidate(
        timestamp_seconds=candidate.timestamp_seconds,
        image_path=destination,
        stability=candidate.stability,
        sharpness=candidate.sharpness,
        exposure_penalty=candidate.exposure_penalty,
        quality_score=candidate.quality_score,
        perceptual_hash=candidate.perceptual_hash,
        source_index=candidate.source_index,
        width=width,
        height=height,
    )


def _load_whisper_model_factory() -> Callable[..., Any]:
    """Load faster-whisper lazily so non-transcription commands stay lightweight."""

    try:
        whisper_module = import_module("faster_whisper")
        whisper_model = whisper_module.WhisperModel
    except (ImportError, AttributeError) as exc:
        raise PipelineServiceError(
            "TRANSCRIPTION_MODEL_FAILED", "The local transcription model is unavailable."
        ) from exc
    return cast(Callable[..., Any], whisper_model)


def _convert_whisper_segment(index: int, source: object) -> TranscriptSegment:
    """Convert one faster-whisper segment into the immutable internal contract."""

    start = max(0.0, float(getattr(source, "start", 0.0)))
    end = max(start, float(getattr(source, "end", start)))
    raw_words = getattr(source, "words", None) or ()
    words = tuple(_convert_whisper_word(word, start, end) for word in raw_words)
    return TranscriptSegment(
        source_index=index,
        start_seconds=start,
        end_seconds=end,
        text=str(getattr(source, "text", "")),
        confidence=_confidence_from_log_probability(getattr(source, "avg_logprob", None)),
        words=words,
    )


def _convert_whisper_word(
    source: object, segment_start: float, segment_end: float
) -> TranscriptWord:
    """Normalize optional Whisper word timing within its owning segment."""

    start = _finite_float(getattr(source, "start", segment_start), segment_start)
    end = _finite_float(getattr(source, "end", start), start)
    bounded_start = min(max(start, segment_start), segment_end)
    bounded_end = min(max(end, bounded_start), segment_end)
    return TranscriptWord(
        text=str(getattr(source, "word", "")),
        start_seconds=bounded_start,
        end_seconds=bounded_end,
        probability=_bounded_probability(getattr(source, "probability", None)),
    )


def _confidence_from_log_probability(value: Any) -> float | None:
    """Convert Whisper average log probability into a documented 0-to-1 heuristic."""

    parsed = _optional_finite_float(value)
    if parsed is None:
        return None
    return _clamp(math.exp(min(0.0, parsed)))


def _build_mock_transcription(
    audio_path: Path, source_language: str, duration_seconds: float | None
) -> TranscriptionResult:
    """Create stable timestamped text only for explicitly enabled automated tests."""

    duration = _positive_float(duration_seconds) or _wav_duration(audio_path)
    duration = max(duration, 0.001)
    segment_length = 5.0
    segment_count = max(1, math.ceil(duration / segment_length))
    segments: list[TranscriptSegment] = []
    for index in range(segment_count):
        start = index * segment_length
        end = min(duration, (index + 1) * segment_length)
        text = f"Deterministic mock transcript segment {index + 1}."
        words = _build_mock_words(text, start, end)
        segments.append(TranscriptSegment(index, start, end, text, 0.99, tuple(words)))
    return TranscriptionResult(
        language="en" if source_language == "auto" else source_language,
        language_probability=1.0,
        duration_seconds=duration,
        segments=tuple(segments),
        model_path="mock",
        mock=True,
    )


def _build_mock_words(text: str, start: float, end: float) -> list[TranscriptWord]:
    """Distribute deterministic mock words evenly across a source segment."""

    tokens = text.split(" ")
    word_duration = (end - start) / max(1, len(tokens))
    return [
        TranscriptWord(
            text=("" if index == 0 else " ") + token,
            start_seconds=start + index * word_duration,
            end_seconds=start + (index + 1) * word_duration,
            probability=0.99,
        )
        for index, token in enumerate(tokens)
    ]


def _wav_duration(audio_path: Path) -> float:
    """Read PCM WAV duration and report an invalid prepared-audio failure cleanly."""

    try:
        with wave.open(str(audio_path), "rb") as source:
            frame_rate = source.getframerate()
            if frame_rate <= 0:
                raise ValueError("Invalid WAV frame rate.")
            return source.getnframes() / frame_rate
    except (wave.Error, OSError, ValueError) as exc:
        raise PipelineServiceError(
            "TRANSCRIPTION_INPUT_INVALID", "The prepared audio is invalid."
        ) from exc


def _segments_duration(segments: Sequence[TranscriptSegment]) -> float | None:
    """Resolve transcription duration from the latest returned segment."""

    if not segments:
        return None
    return max(segment.end_seconds for segment in segments)


def _sample_frames_with_capture(
    capture: Any, timestamp_seconds: float, neighbor_offset_seconds: float
) -> list[SampledFrame]:
    """Decode unique neighboring timestamps through an already-open capture."""

    sample_times = sorted(
        {
            max(0.0, timestamp_seconds - neighbor_offset_seconds),
            timestamp_seconds,
            timestamp_seconds + neighbor_offset_seconds,
        }
    )
    samples: list[SampledFrame] = []
    for sample_time in sample_times:
        capture.set(cv2.CAP_PROP_POS_MSEC, sample_time * 1_000.0)
        success, frame = capture.read()
        if success and frame is not None and frame.size:
            samples.append(SampledFrame(sample_time, frame))
    return samples


def _build_frame_candidate(source_index: int, samples: Sequence[SampledFrame]) -> FrameCandidate:
    """Select the clearest neighbor and calculate its complete quality profile."""

    stability = calculate_histogram_stability([sample.frame for sample in samples])
    selected = max(samples, key=lambda sample: _individual_frame_quality(sample.frame))
    sharpness = calculate_laplacian_sharpness(selected.frame)
    normalized_sharpness = sharpness / (sharpness + 100.0)
    exposure_penalty = calculate_exposure_penalty(selected.frame)
    quality_score = _clamp(
        stability * 0.35 + normalized_sharpness * 0.40 + (1.0 - exposure_penalty) * 0.25
    )
    return FrameCandidate(
        timestamp_seconds=selected.timestamp_seconds,
        frame=selected.frame,
        stability=stability,
        sharpness=sharpness,
        exposure_penalty=exposure_penalty,
        quality_score=quality_score,
        perceptual_hash=compute_perceptual_hash(selected.frame),
        source_index=source_index,
    )


def _individual_frame_quality(frame: Frame) -> float:
    """Score a neighboring frame before cross-frame stability is available."""

    sharpness = calculate_laplacian_sharpness(frame)
    normalized_sharpness = sharpness / (sharpness + 100.0)
    return normalized_sharpness * 0.6 + (1.0 - calculate_exposure_penalty(frame)) * 0.4


def _normalized_grayscale_histogram(frame: Frame) -> NDArray[np.float32]:
    """Build a normalized grayscale histogram for stable correlation scoring."""

    histogram = cv2.calcHist([_grayscale(frame)], [0], None, [64], [0, 256])
    normalized: NDArray[np.float32] = np.empty_like(histogram, dtype=np.float32)
    cv2.normalize(histogram, normalized)
    return normalized


def _grayscale(frame: Frame) -> Frame:
    """Validate and convert a decoded frame to grayscale for metric calculation."""

    if not isinstance(frame, np.ndarray) or frame.size == 0:
        raise ValueError("Frame data cannot be empty.")
    if frame.ndim == 2:
        return frame
    if frame.ndim == 3 and frame.shape[2] == 3:
        return cast(Frame, cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))
    if frame.ndim == 3 and frame.shape[2] == 4:
        return cast(Frame, cv2.cvtColor(frame, cv2.COLOR_BGRA2GRAY))
    raise ValueError("Frame data must be grayscale, BGR, or BGRA.")


def _parse_perceptual_hash(value: str | int) -> int:
    """Parse and validate a hexadecimal or integer 64-bit perceptual hash."""

    try:
        parsed = int(value, 16) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Perceptual hashes must be 64-bit hexadecimal values.") from exc
    if parsed < 0 or parsed >= 2**64:
        raise ValueError("Perceptual hashes must fit within 64 bits.")
    return parsed


def _best_cluster_candidate(cluster: Sequence[FrameCandidate]) -> FrameCandidate:
    """Choose the highest-quality representative with deterministic tie-breaking."""

    return min(
        cluster,
        key=lambda candidate: (-candidate.quality_score, candidate.timestamp_seconds),
    )


def _rank_candidate(
    candidate: RankableFrameCandidate, midpoint: float, half_duration: float
) -> RankedFrameCandidate:
    """Calculate the explicit temporal and visual score for one association."""

    distance = abs(candidate.timestamp_seconds - midpoint)
    proximity = _clamp(1.0 - distance / half_duration)
    score = proximity * 0.7 + _clamp(candidate.quality_score) * 0.3
    return RankedFrameCandidate(candidate, score, proximity)


def _segment_bounds(segment: object) -> tuple[float, float]:
    """Read segment boundaries from domain objects, mappings, or API-shaped data."""

    start = _object_value(segment, ("start_seconds", "startSeconds", "start"))
    end = _object_value(segment, ("end_seconds", "endSeconds", "end"))
    if start is None or end is None:
        raise ValueError("Segment timing is required for screenshot processing.")
    return float(start), float(end)


def _segment_identifier(segment: object) -> str:
    """Read the stable segment identifier used by screenshot associations."""

    value = _object_value(segment, ("id", "segment_id", "segmentId"))
    if value is None:
        raise ValueError("A segment identifier is required for screenshot ranking.")
    return str(value)


def _object_value(source: object, names: Sequence[str]) -> Any:
    """Resolve a named value without coupling pipeline code to ORM model classes."""

    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source[name]
        if hasattr(source, name):
            return getattr(source, name)
    return None


def _setting(config: object | None, name: str, default: Any) -> Any:
    """Read an optional processing setting while preserving standalone defaults."""

    return getattr(config, name, default) if config is not None else default


def _bounded_probability(value: Any) -> float | None:
    """Normalize an optional finite probability into the inclusive 0-to-1 range."""

    parsed = _optional_finite_float(value)
    return None if parsed is None else _clamp(parsed)


def _optional_finite_float(value: Any) -> float | None:
    """Convert an optional numeric value only when it is finite."""

    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _positive_float(value: Any) -> float | None:
    """Convert a numeric value only when it is finite and greater than zero."""

    parsed = _optional_finite_float(value)
    return parsed if parsed is not None and parsed > 0 else None


def _finite_float(value: Any, default: float) -> float:
    """Convert a numeric value when finite and otherwise retain a safe default."""

    parsed = _optional_finite_float(value)
    return default if parsed is None else parsed


def _clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    """Clamp a score to the documented inclusive range."""

    return max(minimum, min(maximum, value))
