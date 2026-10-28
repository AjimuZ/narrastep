"""Compare legacy and adaptive screenshot strategies on an annotated local corpus."""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.services.adaptive_selection import extract_adaptive_frames_to_directory
from app.services.diagnostics import MetricsCollector
from app.services.exporting import ExportRequest, ExportSegment, estimate_export_package
from app.services.pipeline import (
    PersistedFrameCandidate,
    TranscriptSegment,
    TranscriptWord,
    extract_deduplicated_frames_to_directory,
    generate_frame_times,
    perceptual_hash_similarity,
    rank_candidates_for_segments,
)

PROMOTION_GATES = {
    "criticalStateRecall": 0.98,
    "maximumMissedCriticalStateRate": 0.02,
    "minimumRedundancyReduction": 0.30,
    "minimumPngReduction": 0.40,
    "minimumBatchCopyReduction": 0.25,
    "maximumCpuTimeIncrease": 0.15,
}


@dataclass(frozen=True, slots=True)
class _StrategyResult:
    """Hold aggregate inputs from one case and one screenshot strategy."""

    candidates: list[PersistedFrameCandidate]
    elapsed_seconds: float
    peak_resident_set_mb: float
    batch_copy_count: int


def _parse_segments(raw_segments: list[dict[str, Any]]) -> list[TranscriptSegment]:
    """Convert annotated local JSON segments into the immutable pipeline contract."""
    segments: list[TranscriptSegment] = []
    for index, raw_segment in enumerate(raw_segments):
        words = tuple(
            TranscriptWord(
                text=str(raw_word.get("text", "")),
                start_seconds=float(raw_word["startSeconds"]),
                end_seconds=float(raw_word["endSeconds"]),
                probability=None,
            )
            for raw_word in raw_segment.get("words", [])
        )
        segments.append(
            TranscriptSegment(
                source_index=index,
                start_seconds=float(raw_segment["startSeconds"]),
                end_seconds=float(raw_segment["endSeconds"]),
                text=str(raw_segment.get("text", "")),
                confidence=None,
                words=words,
            )
        )
    return segments


def _run_strategy(
    strategy: str,
    case: dict[str, Any],
    segments: list[TranscriptSegment],
    output_root: Path,
    base_settings: Settings,
) -> _StrategyResult:
    """Run one real local selector and calculate its matching export batch shape."""
    video_path = Path(str(case["videoPath"])).expanduser().resolve()
    duration_seconds = float(case["durationSeconds"])
    config = base_settings.model_copy(update={"screenshot_selection_strategy": strategy})
    metrics = MetricsCollector(
        project_id="benchmark",
        operation_id=f"{case.get('id', 'case')}-{strategy}",
        operation="benchmark",
        strategy=strategy,
    )
    started_at = time.perf_counter()
    if strategy == "adaptive_v1":
        candidates = extract_adaptive_frames_to_directory(
            video_path,
            duration_seconds,
            segments,
            output_root / strategy,
            project_mode=str(case.get("mode", "general")),
            detected_language=str(case.get("detectedLanguage", "en")),
            config=config,
            metrics=metrics,
        )
    else:
        frame_times = generate_frame_times(duration_seconds, segments, config=config)
        candidates = extract_deduplicated_frames_to_directory(
            video_path,
            frame_times,
            output_root / strategy,
            config=config,
            neighbor_offset_seconds=config.frame_neighbor_seconds,
        )
    elapsed_seconds = time.perf_counter() - started_at
    return _StrategyResult(
        candidates=candidates,
        elapsed_seconds=elapsed_seconds,
        peak_resident_set_mb=float(metrics.snapshot().get("peakResidentSetMb", 0.0)),
        batch_copy_count=_estimate_batch_copies(
            strategy,
            case,
            segments,
            candidates,
            video_path,
            config,
            output_root,
        ),
    )


def _estimate_batch_copies(
    strategy: str,
    case: dict[str, Any],
    segments: list[TranscriptSegment],
    candidates: list[PersistedFrameCandidate],
    video_path: Path,
    config: Settings,
    output_root: Path,
) -> int:
    """Use the production pure selector to estimate repeated attachment-batch images."""
    selections = rank_candidates_for_segments(segments, candidates, config=config)
    export_segments: list[ExportSegment] = []
    for sequence, (segment, selection) in enumerate(zip(segments, selections, strict=True)):
        candidate = selection.primary.candidate if selection.primary is not None else None
        thumbnail_only = (
            candidate is not None and candidate.materialization_state == "thumbnail_only"
        )
        export_segments.append(
            ExportSegment(
                id=f"segment-{sequence}",
                sequence=sequence,
                start_seconds=segment.start_seconds,
                end_seconds=segment.end_seconds,
                raw_text=segment.text,
                reviewed_text=segment.text,
                speaker=None,
                confidence=segment.confidence,
                screenshot_id=f"candidate-{candidate.source_index}" if candidate else None,
                screenshot_path=(
                    candidate.image_path if candidate is not None and not thumbnail_only else None
                ),
                screenshot_thumbnail_path=(
                    candidate.image_path if candidate is not None and thumbnail_only else None
                ),
                screenshot_source_video_path=video_path if candidate else None,
                screenshot_timestamp_seconds=candidate.timestamp_seconds if candidate else None,
                screenshot_perceptual_hash=candidate.perceptual_hash if candidate else None,
                screenshot_quality_score=candidate.quality_score if candidate else None,
                screenshot_association_score=(
                    selection.primary.score if selection.primary else None
                ),
                screenshot_is_mandatory=candidate.is_mandatory if candidate else False,
                screenshot_selection_reason=candidate.selection_reason if candidate else None,
                screenshot_width=candidate.width if candidate else None,
                screenshot_height=candidate.height if candidate else None,
            )
        )
    estimate = estimate_export_package(
        ExportRequest(
            export_id="benchmark",
            project={"id": "benchmark"},
            source={"durationSeconds": float(case["durationSeconds"])},
            segments=export_segments,
            raw_transcript_path=output_root / "missing-raw-transcript.json",
            destination_path=output_root / "unused.zip",
            output_mode=str(case.get("mode", "general")),
            original_video_path=video_path,
            chatgpt_max_files_per_batch=config.chatgpt_max_files_per_batch,
            duplicate_similarity_threshold=config.duplicate_similarity_threshold,
            selection_strategy=strategy,
            image_budget_profile=config.image_budget_profile,
            duration_seconds=float(case["durationSeconds"]),
        )
    )
    return estimate.batch_image_copy_count


def _critical_hits(
    candidates: list[PersistedFrameCandidate], critical_timestamps: list[float], tolerance: float
) -> int:
    """Count annotated critical states with a retained candidate inside the accepted tolerance."""
    return sum(
        any(abs(candidate.timestamp_seconds - timestamp) <= tolerance for candidate in candidates)
        for timestamp in critical_timestamps
    )


def _redundancy_ratio(candidates: list[PersistedFrameCandidate]) -> float:
    """Measure candidates that repeat any earlier globally similar screen."""
    duplicate_count = 0
    for index, candidate in enumerate(candidates):
        if any(
            perceptual_hash_similarity(candidate.perceptual_hash, earlier.perceptual_hash) >= 0.94
            for earlier in candidates[:index]
        ):
            duplicate_count += 1
    return duplicate_count / len(candidates) if candidates else 0.0


def _safe_reduction(baseline: float, improved: float) -> float:
    """Calculate proportional reduction while treating a zero baseline as already optimal."""
    if baseline <= 0:
        return 0.0 if improved > 0 else 1.0
    return (baseline - improved) / baseline


def run_benchmark(dataset: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Run every annotated case locally and evaluate all automatic promotion gates."""
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("The benchmark dataset must contain at least one case.")
    totals = {
        "critical": 0,
        "legacyHits": 0,
        "adaptiveHits": 0,
        "legacyRedundancy": 0.0,
        "adaptiveRedundancy": 0.0,
        "legacyPngs": 0,
        "adaptivePngs": 0,
        "legacyBatchCopies": 0,
        "adaptiveBatchCopies": 0,
        "legacySeconds": 0.0,
        "adaptiveSeconds": 0.0,
        "adaptivePeakMb": 0.0,
    }
    with tempfile.TemporaryDirectory(prefix="narrastep-benchmark-") as temporary_directory:
        benchmark_root = Path(temporary_directory)
        for case_index, case in enumerate(cases):
            if not isinstance(case, dict):
                raise ValueError("Every benchmark case must be an object.")
            segments = _parse_segments(list(case.get("segments", [])))
            case_root = benchmark_root / f"case-{case_index:04d}"
            legacy = _run_strategy("legacy", case, segments, case_root, settings)
            adaptive = _run_strategy("adaptive_v1", case, segments, case_root, settings)
            critical_timestamps = [
                float(item["timestampSeconds"]) for item in case.get("criticalStates", [])
            ]
            tolerance = float(case.get("criticalToleranceSeconds", 0.75))
            totals["critical"] += len(critical_timestamps)
            totals["legacyHits"] += _critical_hits(
                legacy.candidates, critical_timestamps, tolerance
            )
            totals["adaptiveHits"] += _critical_hits(
                adaptive.candidates, critical_timestamps, tolerance
            )
            totals["legacyRedundancy"] += _redundancy_ratio(legacy.candidates)
            totals["adaptiveRedundancy"] += _redundancy_ratio(adaptive.candidates)
            totals["legacyPngs"] += sum(
                candidate.materialization_state == "png_ready" for candidate in legacy.candidates
            )
            totals["adaptivePngs"] += sum(
                candidate.materialization_state == "png_ready" for candidate in adaptive.candidates
            )
            totals["legacyBatchCopies"] += legacy.batch_copy_count
            totals["adaptiveBatchCopies"] += adaptive.batch_copy_count
            totals["legacySeconds"] += legacy.elapsed_seconds
            totals["adaptiveSeconds"] += adaptive.elapsed_seconds
            totals["adaptivePeakMb"] = max(totals["adaptivePeakMb"], adaptive.peak_resident_set_mb)

    case_count = len(cases)
    legacy_recall = totals["legacyHits"] / totals["critical"] if totals["critical"] else 1.0
    recall = totals["adaptiveHits"] / totals["critical"] if totals["critical"] else 1.0
    missed_rate = 1.0 - recall
    redundancy_reduction = _safe_reduction(
        totals["legacyRedundancy"] / case_count,
        totals["adaptiveRedundancy"] / case_count,
    )
    png_reduction = _safe_reduction(totals["legacyPngs"], totals["adaptivePngs"])
    batch_copy_reduction = _safe_reduction(
        totals["legacyBatchCopies"], totals["adaptiveBatchCopies"]
    )
    cpu_increase = (
        totals["adaptiveSeconds"] / totals["legacySeconds"] - 1.0
        if totals["legacySeconds"] > 0
        else 0.0
    )
    ram_budget = float(dataset.get("machineRamBudgetMb", 0.0))
    gate_results = {
        "criticalStateRecall": recall >= PROMOTION_GATES["criticalStateRecall"],
        "missedCriticalStateRate": missed_rate <= PROMOTION_GATES["maximumMissedCriticalStateRate"],
        "redundancyReduction": redundancy_reduction
        >= PROMOTION_GATES["minimumRedundancyReduction"],
        "persistedPngReduction": png_reduction >= PROMOTION_GATES["minimumPngReduction"],
        "batchCopyReduction": batch_copy_reduction >= PROMOTION_GATES["minimumBatchCopyReduction"],
        "cpuTimeIncrease": cpu_increase <= PROMOTION_GATES["maximumCpuTimeIncrease"],
        "peakRam": ram_budget > 0 and totals["adaptivePeakMb"] <= ram_budget,
    }
    return {
        "algorithmVersion": "adaptive_v1.0",
        "caseCount": case_count,
        "metrics": {
            "legacyCriticalStateRecall": round(legacy_recall, 6),
            "criticalStateRecall": round(recall, 6),
            "missedCriticalStateRate": round(missed_rate, 6),
            "redundancyReduction": round(redundancy_reduction, 6),
            "persistedPngReduction": round(png_reduction, 6),
            "batchCopyReduction": round(batch_copy_reduction, 6),
            "cpuTimeIncrease": round(cpu_increase, 6),
            "adaptivePeakRamMb": totals["adaptivePeakMb"],
            "machineRamBudgetMb": ram_budget,
        },
        "gates": gate_results,
        "promoteAdaptiveByDefault": all(gate_results.values()),
    }


def main() -> int:
    """Parse one local annotation file and print a content-free JSON gate report."""
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--ocr-mode", choices=("off", "selective"), default="selective")
    parser.add_argument("--tesseract-path", default="tesseract")
    arguments = parser.parse_args()
    dataset = json.loads(arguments.dataset.read_text(encoding="utf-8"))
    settings = Settings(
        screenshot_selection_strategy="adaptive_v1",
        image_budget_profile="balanced",
        ocr_mode=arguments.ocr_mode,
        tesseract_path=arguments.tesseract_path,
    )
    print(json.dumps(run_benchmark(dataset, settings), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
