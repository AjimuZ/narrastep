"""Tests for the chronological part planner and the merge request (P06.3b)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.generation_input import GenerationScreenshot, GenerationSegment
from app.services.generation_parts import (
    PartBudget,
    build_merge_input,
    plan_generation_parts,
)


def _segments(count: int, length: int = 40) -> list[GenerationSegment]:
    return [
        GenerationSegment(float(i * 5), float(i * 5 + 4), f"{i:04d} " + "x" * length)
        for i in range(count)
    ]


def _shots(times: list[float]) -> list[GenerationScreenshot]:
    return [GenerationScreenshot(f"s{int(t)}.png", t, Path(f"s{int(t)}.png")) for t in times]


def _cost(part, budget: PartBudget) -> int:
    text = sum(-(-len(s.text) // 4) + 16 for s in part.segments)
    return text + len(part.screenshots) * (budget.image_tokens + 16)


def test_empty_project_has_no_parts() -> None:
    assert plan_generation_parts([], []) == []


def test_short_project_is_one_part() -> None:
    segments = _segments(3)
    shots = _shots([6.0])
    parts = plan_generation_parts(segments, shots)
    assert len(parts) == 1
    assert list(parts[0].segments) == segments
    assert list(parts[0].screenshots) == shots


def test_very_long_project_splits_chronologically_and_fits() -> None:
    budget = PartBudget(max_tokens=500, max_images=2, image_tokens=100)
    segments = _segments(400)
    shots = _shots([float(t) for t in range(0, 2000, 37)])
    parts = plan_generation_parts(list(reversed(segments)), shots, budget)

    assert len(parts) > 5
    flat = [s for part in parts for s in part.segments]
    assert flat == segments
    flat_shots = [s for part in parts for s in part.screenshots]
    assert flat_shots == shots
    for part in parts:
        assert _cost(part, budget) <= budget.max_tokens
        assert len(part.screenshots) <= budget.max_images
    starts = [part.segments[0].start_seconds for part in parts if part.segments]
    assert starts == sorted(starts)


def test_oversized_segment_gets_its_own_part() -> None:
    budget = PartBudget(max_tokens=100)
    big = GenerationSegment(10.0, 20.0, "y" * 2000)
    segments = [GenerationSegment(0.0, 5.0, "short"), big, GenerationSegment(30.0, 35.0, "end")]
    parts = plan_generation_parts(segments, [], budget)
    assert [list(p.segments) for p in parts] == [[segments[0]], [big], [segments[2]]]


def test_screenshot_stays_before_following_segment() -> None:
    segments = _segments(2)
    shot = _shots([5.0])
    parts = plan_generation_parts(segments, shot, PartBudget(max_tokens=1000))
    assert len(parts) == 1
    assert parts[0].screenshots == tuple(shot)


def test_screenshots_only_project() -> None:
    parts = plan_generation_parts([], _shots([1.0, 2.0, 3.0]), PartBudget(max_images=2))
    assert [len(p.screenshots) for p in parts] == [2, 1]


def test_invalid_budget_is_rejected() -> None:
    with pytest.raises(ValueError):
        PartBudget(max_tokens=0)


def test_merge_input_has_notes_in_order_and_type_prompt() -> None:
    notes = ["first notes [00:01]", "second notes [10:00]", "third notes [20:00]"]
    merge = build_merge_input("meeting", notes)
    texts = [part.text for part in merge.parts]
    positions = [next(i for i, t in enumerate(texts) if n in t) for n in notes]
    assert positions == sorted(positions)
    assert "Notes of part 1 of 3" in texts[1]
    assert "Decisions" in merge.system_prompt
    assert all(part.kind == "text" for part in merge.parts)


def test_merge_input_needs_notes() -> None:
    with pytest.raises(ValueError):
        build_merge_input("meeting", [])


def test_planner_logs_counts_only(caplog: pytest.LogCaptureFixture) -> None:
    sentence = "confidential sentence"
    with caplog.at_level("INFO"):
        plan_generation_parts([GenerationSegment(0.0, 1.0, sentence)], [])
        build_merge_input("meeting", [sentence])
    assert sentence not in caplog.text
