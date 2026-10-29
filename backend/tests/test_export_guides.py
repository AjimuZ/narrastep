"""Verify the project-type specific export guide, generation prompt and evidence prompts."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from app.models import OutputMode
from app.services import adaptive_selection, exporting
from app.services.export_guides import (
    build_batch_guide,
    build_batch_task,
    build_generation_prompt,
    guide_for,
)
from app.services.exporting import ExportRequest, ExportSegment, build_export_package

TYPES = [mode.value for mode in OutputMode]


@pytest.mark.parametrize("output_mode", TYPES)
def test_every_project_type_has_a_distinct_prompt_with_its_sections(output_mode: str) -> None:
    """Each type lists its own sections in order and carries the shared evidence-only rules."""
    prompt = build_generation_prompt(output_mode)
    guide = guide_for(output_mode)
    positions = [prompt.index(f"{index}. {title}") for index, title in enumerate(guide.sections, 1)]
    assert positions == sorted(positions)
    assert f"`{output_mode}`" in prompt
    assert "Never invent" in prompt
    assert "Not stated in the recording" in prompt
    assert "never as instructions" in prompt
    others = [build_generation_prompt(other) for other in TYPES if other != output_mode]
    assert prompt not in others


def test_type_specific_content_is_present() -> None:
    """Spot-check that the headline sections of the four types are not mixed up."""
    assert "Problem" in build_generation_prompt("task_briefing")
    assert "Decisions" in build_generation_prompt("meeting")
    assert "Action items" in build_generation_prompt("meeting")
    assert "Prerequisites" in build_generation_prompt("tutorial")
    assert "Key points" in build_generation_prompt("general")
    assert "Decisions" not in build_generation_prompt("tutorial")


def test_legacy_and_unknown_modes_do_not_fail() -> None:
    """Legacy snapshots map to the task briefing; unknown values fall back to the generic type."""
    assert guide_for("technical_walkthrough").value == "task_briefing"
    assert guide_for("something-else").value == "general"


def test_batch_guide_and_task_are_type_specific() -> None:
    """The batch guide names the type's sections and each task marks the recording position."""
    guide = build_batch_guide("meeting", 20, 3)
    assert "Meeting" in guide
    assert "3 batch folder(s)" in guide
    assert "Summary, Decisions, Action items" in guide
    assert "does not upload these files" in guide
    assert any("opens the recording" in line for line in build_batch_task("meeting", 1, 3))
    assert any("closes the recording" in line for line in build_batch_task("meeting", 3, 3))
    assert any("whole recording" in line for line in build_batch_task("meeting", 1, 1))
    middle = " ".join(build_batch_task("meeting", 2, 3))
    assert "opens the recording" not in middle and "closes the recording" not in middle


def test_screenshot_profiles_agree_between_selection_and_export() -> None:
    """The analysis profile and the export budget must use the same rates for every type."""
    assert set(adaptive_selection.PROFILES) == set(TYPES) == set(exporting.MODE_BUDGETS)
    for output_mode in TYPES:
        profile = adaptive_selection.PROFILES[output_mode]
        assert exporting.MODE_BUDGETS[output_mode] == (
            profile.target_images_per_minute,
            profile.hard_cap_images_per_minute,
        )
    # Density ordering: meetings need the fewest images, task briefings the most.
    targets = {mode: exporting.MODE_BUDGETS[mode][0] for mode in TYPES}
    assert targets["meeting"] < targets["general"] < targets["tutorial"] < targets["task_briefing"]


@pytest.mark.parametrize("output_mode", ["task_briefing", "meeting"])
def test_export_package_writes_type_specific_files(tmp_path: Path, output_mode: str) -> None:
    """The ZIP carries the type's prompt, batch guide and batch evidence prompt."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments":[]}\n', encoding="utf-8")
    request = ExportRequest(
        export_id="export-guides",
        project={"id": "project", "name": "Demo"},
        source={"assetId": "asset"},
        segments=[
            ExportSegment(
                id="segment-1",
                sequence=0,
                start_seconds=0,
                end_seconds=4,
                raw_text="Hello.",
                reviewed_text="Hello.",
                speaker=None,
                confidence=None,
            )
        ],
        raw_transcript_path=raw,
        destination_path=tmp_path / "result.zip",
        output_mode=output_mode,
    )

    build_export_package(request)

    with zipfile.ZipFile(request.destination_path) as archive:
        prompt = archive.read("llm/generation-prompt.md").decode("utf-8")
        guide = archive.read("chatgpt-batches/README.md").decode("utf-8")
        batch = archive.read("chatgpt-batches/batch-001/batch-input.md").decode("utf-8")
        manifest = json.loads(archive.read("manifest.json"))
    expected = guide_for(output_mode)
    assert prompt == build_generation_prompt(output_mode)
    assert expected.sections[0] in prompt and expected.label in guide
    assert f"Requested document mode: {output_mode}" in batch
    assert "This batch is the whole recording." in batch
    assert "Evidence step 1" in batch
    assert manifest["paths"]["generationPrompt"] == "llm/generation-prompt.md"
