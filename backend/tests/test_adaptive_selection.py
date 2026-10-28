"""Deterministic coverage for adaptive screenshot selection rules."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from app.config import Settings
from app.services import adaptive_selection
from app.services.adaptive_selection import (
    PROFILES,
    DocumentUnit,
    _AdaptiveCandidate,
    _Cluster,
    _is_transient_event,
    _looks_like_dialog,
    build_document_units,
    candidate_times_for_windows,
    fuse_interest_windows,
    global_cluster_candidates,
    select_coverage_candidates,
    visual_delta,
)
from app.services.diagnostics import MetricsCollector
from app.services.pipeline import FrameCandidate, TranscriptSegment, TranscriptWord


class _TokenEngine:
    """Return predetermined token sets while recording safe OCR path inputs."""

    def __init__(self, tokens: list[frozenset[str]]) -> None:
        """Store ordered results for one deterministic clustering test."""
        self.tokens = iter(tokens)
        self.calls: list[tuple[str, str]] = []

    def recognize_tokens(self, image_path: Path, language: str) -> frozenset[str]:
        """Return the next token set without invoking an external executable."""
        self.calls.append((image_path.name, language))
        return next(self.tokens)


def _segment() -> TranscriptSegment:
    """Build one transcript segment with a meaningful one-second word pause."""
    return TranscriptSegment(
        source_index=0,
        start_seconds=0.0,
        end_seconds=8.0,
        text="local test transcript",
        confidence=0.9,
        words=(
            TranscriptWord("first", 0.0, 2.0, 0.9),
            TranscriptWord("second", 3.2, 5.0, 0.9),
            TranscriptWord("third", 5.1, 8.0, 0.9),
        ),
    )


def _candidate(
    index: int,
    timestamp: float,
    perceptual_hash: str,
    *,
    quality: float = 0.8,
    mandatory: bool = False,
) -> _AdaptiveCandidate:
    """Build one in-memory candidate with a stable synthetic grayscale frame."""
    frame = np.full((64, 96, 3), 30 + index * 20, dtype=np.uint8)
    return _AdaptiveCandidate(
        frame_candidate=FrameCandidate(
            timestamp_seconds=timestamp,
            frame=frame,
            stability=0.95,
            sharpness=100.0,
            exposure_penalty=0.0,
            quality_score=quality,
            perceptual_hash=perceptual_hash,
            source_index=index,
        ),
        visual_change_score=0.5,
        is_mandatory=mandatory,
        selection_reason="quality",
    )


def _metrics() -> MetricsCollector:
    """Create one content-free metrics collector for selection tests."""
    return MetricsCollector("project", "job", "processing", "adaptive_v1")


def test_document_units_split_at_long_word_pause_without_losing_stable_units() -> None:
    """Keep units split when the pre-pause unit is already at least two seconds long."""
    units = build_document_units([_segment()])

    assert [(unit.start_seconds, unit.end_seconds) for unit in units] == [
        (0.0, 2.6),
        (2.6, 8.0),
    ]


def test_document_units_merge_a_short_following_unit() -> None:
    """Merge a short post-pause unit when the combined document interval stays bounded."""
    segment = TranscriptSegment(
        source_index=0,
        start_seconds=0.0,
        end_seconds=4.0,
        text="long then short",
        confidence=0.9,
        words=(
            TranscriptWord("long", 0.0, 2.0, 0.9),
            TranscriptWord("short", 3.1, 4.0, 0.9),
        ),
    )

    units = build_document_units([segment])

    assert [(unit.start_seconds, unit.end_seconds) for unit in units] == [(0.0, 4.0)]


def test_fallback_window_decodes_only_its_midpoint_anchor() -> None:
    """Avoid unnecessary pre/post decodes for transcript-only fallback windows."""
    units = [DocumentUnit(0, 2.0, 6.0)]

    windows = fuse_interest_windows([], units, 10.0)

    assert len(windows) == 1
    assert windows[0].fallback is True
    assert candidate_times_for_windows(windows, 10.0) == [4.0]


def test_each_uncovered_document_unit_keeps_its_own_midpoint_fallback() -> None:
    """Do not merge adjacent transcript fallbacks or treat one fallback as a visual event."""
    units = [DocumentUnit(0, 0.0, 2.0), DocumentUnit(1, 2.0, 4.0)]

    windows = fuse_interest_windows([], units, 4.0)

    assert all(window.fallback for window in windows)
    assert candidate_times_for_windows(windows, 4.0) == [1.0, 3.0]


def test_transient_duration_excludes_prechange_and_stability_buffers() -> None:
    """Classify a brief event from its changed samples rather than its retained scan buffers."""
    assert _is_transient_event(5.0, 6.0, 0.3) is True
    assert _is_transient_event(5.0, 8.1, 0.3) is False


def test_micro_region_masking_keeps_cursor_noise_below_meaningful_change() -> None:
    """Suppress one-pixel noise while retaining a larger localized interface change."""
    base = np.zeros((120, 160), dtype=np.uint8)
    cursor_noise = base.copy()
    cursor_noise[10, 10] = 255
    dialog_change = base.copy()
    dialog_change[30:90, 50:120] = 255

    noise_score = visual_delta(base, cursor_noise)
    dialog_score = visual_delta(base, dialog_change)

    assert noise_score < 0.01
    assert dialog_score >= 0.12
    assert dialog_score > noise_score


def test_centered_dialog_state_is_recognized_as_mandatory_evidence() -> None:
    """Recognize a centered bounded overlay while rejecting an ordinary flat screen."""
    flat_screen = np.full((180, 320, 3), 30, dtype=np.uint8)
    dialog_screen = flat_screen.copy()
    dialog_screen[45:145, 70:250] = 190
    dialog_screen[48:142, 73:247] = 140

    assert _looks_like_dialog(flat_screen) is False
    assert _looks_like_dialog(dialog_screen) is True


def test_global_clustering_reuses_non_consecutive_screen_and_selects_quality_medoid(
    tmp_path: Path,
) -> None:
    """Group an A-B-A sequence globally and retain the clearer A as its medoid."""
    first = _candidate(0, 1.0, "0000000000000000", quality=0.7)
    middle = _candidate(1, 2.0, "ffffffffffffffff", quality=0.9)
    returned = _candidate(2, 3.0, "0000000000000000", quality=0.95)

    clusters = global_cluster_candidates(
        [first, middle, returned],
        ocr_engine=None,
        ocr_language="eng",
        ocr_root=tmp_path,
        metrics=_metrics(),
    )

    assert len(clusters) == 2
    duplicate_cluster = next(cluster for cluster in clusters if len(cluster.members) == 2)
    assert duplicate_cluster.representative is returned
    assert returned.cluster_id is not None


def test_borderline_cluster_requires_ocr_token_overlap(tmp_path: Path) -> None:
    """Keep structurally identical borderline frames separate when OCR meaning differs."""
    left = _candidate(0, 1.0, "0000000000000000")
    right = _candidate(1, 2.0, "00000000000000ff")
    right.frame_candidate.frame[:] = left.frame_candidate.frame
    engine = _TokenEngine([frozenset({"settings", "account"}), frozenset({"error", "payment"})])

    clusters = global_cluster_candidates(
        [left, right],
        ocr_engine=engine,  # type: ignore[arg-type]
        ocr_language="eng",
        ocr_root=tmp_path,
        metrics=_metrics(),
    )

    assert len(clusters) == 2
    assert len(engine.calls) == 2
    assert all(language == "eng" for _, language in engine.calls)


def test_text_sensitive_direct_duplicate_still_requires_ocr_overlap(tmp_path: Path) -> None:
    """Do not hide localized text changes even when the direct pHash boundary is met."""
    left = _candidate(0, 1.0, "0000000000000000")
    right = _candidate(1, 2.0, "0000000000000000")
    left.text_sensitive = True
    right.frame_candidate.frame[:] = left.frame_candidate.frame
    engine = _TokenEngine([frozenset({"account", "ready"}), frozenset({"payment", "pending"})])

    clusters = global_cluster_candidates(
        [left, right],
        ocr_engine=engine,  # type: ignore[arg-type]
        ocr_language="eng",
        ocr_root=tmp_path,
        metrics=_metrics(),
    )

    assert len(clusters) == 2
    assert right.text_change_score == 1.0


def test_coverage_budget_preserves_mandatory_overflow() -> None:
    """Apply the compact target to automatic evidence while retaining mandatory states."""
    mandatory = _candidate(0, 1.0, "0000000000000000", mandatory=True)
    automatic = [_candidate(index, float(index + 1), f"{index + 1:016x}") for index in range(1, 6)]
    clusters = [_Cluster([candidate], candidate) for candidate in [mandatory, *automatic]]

    selected = select_coverage_candidates(
        clusters,
        [],
        60.0,
        PROFILES["tutorial"],
        "compact",
        max_alternatives=0,
    )

    assert mandatory in selected
    assert len([candidate for candidate in selected if not candidate.is_mandatory]) == 1


@pytest.mark.parametrize("cancel_on_check", [1, 2, 3, 4])
def test_adaptive_orchestration_observes_cancellation_at_each_bounded_stage(
    cancel_on_check: int, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Stop before scan handoff, clustering, coverage, or thumbnail persistence continues."""
    monkeypatch.setattr(adaptive_selection, "scan_interest_windows", lambda *_args, **_kw: ([], 0))
    monkeypatch.setattr(adaptive_selection, "extract_fine_candidates", lambda *_args, **_kw: [])
    monkeypatch.setattr(adaptive_selection, "global_cluster_candidates", lambda *_args, **_kw: [])
    monkeypatch.setattr(adaptive_selection, "select_coverage_candidates", lambda *_args, **_kw: [])
    checks = 0

    def cancel_at_boundary() -> None:
        """Raise on one requested bounded-stage cancellation check."""
        nonlocal checks
        checks += 1
        if checks == cancel_on_check:
            raise RuntimeError("cancelled")

    settings = Settings(
        environment="test",
        data_root=tmp_path / "data",
        database_url=f"sqlite:///{(tmp_path / 'data' / 'test.db').as_posix()}",
        ocr_mode="off",
        screenshot_selection_strategy="adaptive_v1",
    )

    with pytest.raises(RuntimeError, match="cancelled"):
        adaptive_selection.extract_adaptive_frames_to_directory(
            tmp_path / "source.mp4",
            10.0,
            [],
            tmp_path / "output",
            project_mode="tutorial",
            detected_language="en",
            config=settings,
            metrics=_metrics(),
            cancellation_check=cancel_at_boundary,
        )

    assert checks == cancel_on_check
