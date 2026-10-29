"""Edge-case tests for the slug and export file-name builders."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.services.export_names import (
    SLUG_MAX_LENGTH,
    export_archive_name,
    export_zip_file_name,
    screenshot_file_name,
    slugify_project_name,
)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Sprint Planning", "sprint-planning"),
        ("  Café   déjà-vu!  ", "cafe-deja-vu"),
        ("Straße Æther Łódź Øl", "strasse-aether-lodz-ol"),
        ("Riunione città – Q&A", "riunione-citta-q-a"),
        ("../../etc/passwd", "etc-passwd"),
        ("a\\b/c:d*e", "a-b-c-d-e"),
        ("Plan 🚀 launch 🎉", "plan-launch"),
        ("---a---b---", "a-b"),
    ],
)
def test_slug_transliterates_and_separates(name: str, expected: str) -> None:
    assert slugify_project_name(name) == expected


@pytest.mark.parametrize("name", [None, "", "   ", "🚀🎉", "日本語", "---", "..."])
def test_slug_falls_back_when_nothing_survives(name: str | None) -> None:
    assert slugify_project_name(name) == "project"


def test_slug_is_cut_without_trailing_hyphen() -> None:
    name = "a" * 47 + " bcd"
    slug = slugify_project_name(name)
    assert slug == "a" * 47
    assert len(slugify_project_name("x" * 200)) == SLUG_MAX_LENGTH


@pytest.mark.parametrize("name", ["CON", "nul", "Com1", "LPT9", "aux"])
def test_slug_avoids_windows_reserved_names(name: str) -> None:
    assert slugify_project_name(name) == f"{name.lower()}-project"


def test_slug_keeps_names_that_only_start_with_reserved_words() -> None:
    assert slugify_project_name("console") == "console"
    assert slugify_project_name("com10") == "com10"


def test_archive_name_follows_adr_pattern() -> None:
    created = datetime(2026, 10, 28, 23, 59, tzinfo=UTC)
    assert (
        export_zip_file_name(created, "meeting", "Sprint Planning", 2)
        == "2026-10-28_meeting_sprint-planning_export-02.zip"
    )
    assert (
        export_archive_name(date(2026, 1, 5), "task_briefing", None, 100)
        == "2026-01-05_task_briefing_project_export-100"
    )


@pytest.mark.parametrize("project_type", ["", "Meeting", "../x", "a b"])
def test_archive_name_rejects_bad_type(project_type: str) -> None:
    with pytest.raises(ValueError):
        export_archive_name(date(2026, 1, 1), project_type, "x", 1)


def test_archive_name_rejects_bad_sequence() -> None:
    with pytest.raises(ValueError):
        export_archive_name(date(2026, 1, 1), "meeting", "x", 0)


@pytest.mark.parametrize(
    ("step", "seconds", "expected"),
    [
        (1, 0, "step-001_00m00s.png"),
        (12, 75.9, "step-012_01m15s.png"),
        (1234, 3900, "step-1234_65m00s.png"),
    ],
)
def test_screenshot_file_name(step: int, seconds: float, expected: str) -> None:
    assert screenshot_file_name(step, seconds) == expected


def test_screenshot_file_name_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        screenshot_file_name(0, 1)
    with pytest.raises(ValueError):
        screenshot_file_name(1, -0.1)
