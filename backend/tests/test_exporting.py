"""Immutable ZIP package construction tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pytest
from package_reader import open_package
from PIL import Image

from app.services import exporting
from app.services.diagnostics import MetricsCollector
from app.services.export_names import export_archive_name
from app.services.exporting import (
    ExportRequest,
    ExportSegment,
    batch_folder_name,
    build_export_package,
    estimate_export_package,
)

BATCHES = "04-chatgpt-batches"
BATCH_1 = f"{BATCHES}/batch-01-of-01"
SCREENSHOTS = "02-screenshots"


def test_export_contains_complete_honest_package(tmp_path: Path) -> None:
    """Normalize selected evidence and represent a missing primary without fabrication."""
    raw = tmp_path / "raw-transcript.json"
    raw.write_text('{"language":"en","segments":[]}\n', encoding="utf-8")
    screenshot = tmp_path / "candidate.jpg"
    Image.new("RGB", (24, 12), (12, 34, 56)).save(screenshot)
    destination = tmp_path / "exports" / "result.zip"
    request = ExportRequest(
        export_id="export-1",
        project={"id": "project-1", "name": "Demo", "description": None},
        source={"assetId": "asset-1", "durationSeconds": 8.0},
        segments=[
            ExportSegment(
                id="segment-1",
                sequence=0,
                start_seconds=0,
                end_seconds=4,
                raw_text="Open settings.",
                reviewed_text="Open Settings.",
                speaker=None,
                confidence=0.9,
                screenshot_id="shot-1",
                screenshot_path=screenshot,
                screenshot_timestamp_seconds=2,
            ),
            ExportSegment(
                id="segment-2",
                sequence=1,
                start_seconds=4,
                end_seconds=8,
                raw_text="Save.",
                reviewed_text="Save the form.",
                speaker=None,
                confidence=None,
            ),
        ],
        raw_transcript_path=raw,
        destination_path=destination,
        output_mode="tutorial",
    )

    result = build_export_package(request)

    assert result == destination
    assert not destination.with_suffix(".zip.partial").exists()
    with open_package(destination) as archive:
        names = set(archive.namelist())
        assert {
            "metadata/manifest.json",
            "03-transcript/raw-transcript.json",
            "03-transcript/reviewed-transcript.md",
            "03-transcript/subtitles.srt",
            "02-screenshots/step-001_00m00s.png",
            "01-ai-input/llm-input.md",
            "01-ai-input/generation-prompt.md",
            "04-chatgpt-batches/README.md",
            f"{BATCH_1}/batch-input.md",
            f"{BATCH_1}/step-001_00m00s.png",
            "metadata/source.json",
        }.issubset(names)
        manifest = json.loads(archive.read("metadata/manifest.json"))
        assert manifest["schemaVersion"] == "0.3.0"
        assert manifest["createdAt"].endswith("Z")
        assert manifest["segments"][0]["primaryScreenshotId"] == "shot-1"
        assert (
            manifest["segments"][0]["primaryScreenshotPath"] == "02-screenshots/step-001_00m00s.png"
        )
        assert manifest["segments"][1]["primaryScreenshotId"] is None
        assert manifest["paths"]["rawTranscript"] == "03-transcript/raw-transcript.json"
        assert manifest["paths"]["llmInput"] == "01-ai-input/llm-input.md"
        assert manifest["paths"]["manifest"] == "metadata/manifest.json"
        reviewed = archive.read("03-transcript/reviewed-transcript.md").decode("utf-8")
        assert "No screenshot was included for this step" in reviewed
        assert "(../02-screenshots/step-001_00m00s.png)" in reviewed
        llm_input = archive.read("01-ai-input/llm-input.md").decode("utf-8")
        assert "../02-screenshots/step-001_00m00s.png" in llm_input
        assert "Do not invent unsupported steps" in llm_input
        batch_input = archive.read(f"{BATCH_1}/batch-input.md").decode("utf-8")
        assert "Narrastep analysis batch 1 of 1" in batch_input
        assert "Evidence step 1" in batch_input
        assert "Screenshot: step-001_00m00s.png" in batch_input
        assert "Evidence step 2" in batch_input
        assert "Screenshot: none included" in batch_input
        # Without speaker labels nothing about speakers is written.
        assert "Speaker" not in llm_input + batch_input + reviewed
        assert all(not name.startswith("source/") for name in names)


def test_speaker_labels_appear_in_transcripts_and_ai_input(tmp_path: Path) -> None:
    """Name the speaker of labelled steps in every transcript file and explain the labels once.

    An unlabelled step gets no speaker line, and the manifest keeps the stored values.
    """
    raw = tmp_path / "raw-transcript.json"
    raw.write_text('{"language":"en","segments":[]}\n', encoding="utf-8")
    destination = tmp_path / "exports" / "result.zip"
    segments = [
        ExportSegment(
            id=f"segment-{index}",
            sequence=index,
            start_seconds=index * 4,
            end_seconds=index * 4 + 4,
            raw_text=text,
            reviewed_text=text,
            speaker=speaker,
            confidence=0.9,
        )
        for index, (speaker, text) in enumerate(
            [("you", "I will send the report."), ("others", "Thanks."), (None, "Bye.")]
        )
    ]
    request = ExportRequest(
        export_id="export-1",
        project={"id": "project-1", "name": "Call", "description": None},
        source={"assetId": "asset-1", "durationSeconds": 12.0},
        segments=segments,
        raw_transcript_path=raw,
        destination_path=destination,
        output_mode="meeting",
    )

    build_export_package(request)

    with open_package(destination) as archive:
        reviewed = archive.read("03-transcript/reviewed-transcript.md").decode("utf-8")
        assert "## Step 1\n\n**Speaker:** You\n\nI will send the report." in reviewed
        assert "## Step 2\n\n**Speaker:** Others\n\nThanks." in reviewed
        assert "## Step 3\n\nBye." in reviewed
        subtitles = archive.read("03-transcript/subtitles.srt").decode("utf-8")
        assert "You: I will send the report.\n" in subtitles
        assert "Others: Thanks.\n" in subtitles
        assert "\nBye.\n" in subtitles
        for name in ("01-ai-input/llm-input.md", f"{BATCH_1}/batch-input.md"):
            text = archive.read(name).decode("utf-8")
            assert text.count(exporting.SPEAKER_NOTE) == 1, name
            assert "Speaker: You\nTranscript: I will send the report." in text, name
            assert "Speaker: Others\nTranscript: Thanks." in text, name
            step_3 = text.split("## Evidence step 3", 1)[1]
            assert "Speaker:" not in step_3, name
        manifest = json.loads(archive.read("metadata/manifest.json"))
        assert [item["speaker"] for item in manifest["segments"]] == ["you", "others", None]


def test_export_includes_original_only_when_explicitly_requested(tmp_path: Path) -> None:
    """Copy the source into a predictable package path only after explicit opt-in."""
    raw = tmp_path / "raw-transcript.json"
    raw.write_text('{"segments":[]}\n', encoding="utf-8")
    original = tmp_path / "capture.MP4"
    original.write_bytes(b"video")
    destination = tmp_path / "result.zip"
    request = ExportRequest(
        export_id="export-video",
        project={"id": "project", "name": "Demo"},
        source={"assetId": "asset"},
        segments=[],
        raw_transcript_path=raw,
        destination_path=destination,
        output_mode="general",
        original_video_path=original,
        include_original_video=True,
    )

    build_export_package(request)

    with open_package(destination) as archive:
        assert "source/original-video.mp4" in archive.namelist()
        assert f"{BATCH_1}/batch-input.md" in archive.namelist()
        assert not any(name.startswith(f"{BATCH_1}/step-") for name in archive.namelist())
        manifest = json.loads(archive.read("metadata/manifest.json"))
        assert manifest["includedOriginalVideo"] is True


@pytest.mark.parametrize("missing_asset", ["raw", "screenshot"])
def test_export_fails_when_persisted_evidence_is_missing(
    tmp_path: Path, missing_asset: str
) -> None:
    """Distinguish corrupted selected evidence from an intentional no-screenshot segment."""
    raw = tmp_path / "raw.json"
    screenshot = tmp_path / "shot.png"
    if missing_asset != "raw":
        raw.write_text('{"segments": []}', encoding="utf-8")
    if missing_asset != "screenshot":
        Image.new("RGB", (8, 8)).save(screenshot)
    destination = tmp_path / "missing.zip"
    request = ExportRequest(
        export_id="missing",
        project={"id": "project", "name": "Missing"},
        source={"assetId": "asset"},
        segments=[
            ExportSegment(
                id="segment",
                sequence=0,
                start_seconds=0,
                end_seconds=1,
                raw_text="Raw",
                reviewed_text="Reviewed",
                speaker=None,
                confidence=None,
                screenshot_id="shot",
                screenshot_path=screenshot,
            )
        ],
        raw_transcript_path=raw,
        destination_path=destination,
        output_mode="general",
    )
    with pytest.raises(FileNotFoundError):
        build_export_package(request)
    assert not destination.exists()
    assert not destination.with_suffix(".zip.partial").exists()


def test_export_removes_partial_zip_after_writer_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Remove request-owned partial output even when ZIP construction fails midway."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    destination = tmp_path / "failed.zip"
    request = ExportRequest(
        export_id="failed",
        project={"id": "project", "name": "Failure"},
        source={"assetId": "asset"},
        segments=[],
        raw_transcript_path=raw,
        destination_path=destination,
        output_mode="general",
    )

    def fail_zip(_: Path, partial: Path) -> None:
        """Leave bytes behind before simulating a ZIP writer failure."""
        partial.write_bytes(b"partial")
        raise OSError("simulated")

    monkeypatch.setattr(exporting, "_write_zip", fail_zip)
    with pytest.raises(OSError):
        build_export_package(request)
    assert not destination.with_suffix(".zip.partial").exists()


def test_reused_source_screenshot_has_unambiguous_step_artifacts(tmp_path: Path) -> None:
    """Write one shared image while retaining every segment-to-evidence association."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    screenshot = tmp_path / "shared.png"
    Image.new("RGB", (8, 8)).save(screenshot)
    segments = [
        ExportSegment(
            id=f"segment-{index}",
            sequence=index,
            start_seconds=index,
            end_seconds=index + 1,
            raw_text="Raw",
            reviewed_text="Reviewed",
            speaker=None,
            confidence=None,
            screenshot_id="shared-source-id",
            screenshot_path=screenshot,
            screenshot_timestamp_seconds=0.5,
        )
        for index in range(2)
    ]
    destination = tmp_path / "shared.zip"
    build_export_package(
        ExportRequest(
            export_id="shared",
            project={"id": "project", "name": "Shared"},
            source={"assetId": "asset"},
            segments=segments,
            raw_transcript_path=raw,
            destination_path=destination,
            output_mode="tutorial",
            created_at=datetime(2026, 7, 15),
        )
    )
    with open_package(destination) as archive:
        manifest = json.loads(archive.read("metadata/manifest.json"))
        screenshot_files = [
            name for name in archive.namelist() if name.startswith(f"{SCREENSHOTS}/")
        ]
    assert screenshot_files == ["02-screenshots/step-001_00m00s.png"]
    assert [item["sourceScreenshotId"] for item in manifest["screenshots"]] == ["shared-source-id"]
    assert [item["path"] for item in manifest["associations"]] == [
        "02-screenshots/step-001_00m00s.png",
        "02-screenshots/step-001_00m00s.png",
    ]
    assert [item["primaryScreenshotPath"] for item in manifest["segments"]] == [
        "02-screenshots/step-001_00m00s.png",
        "02-screenshots/step-001_00m00s.png",
    ]
    with open_package(destination) as archive:
        batch_images = [name for name in archive.namelist() if name.startswith(f"{BATCH_1}/step-")]
    assert batch_images == [f"{BATCH_1}/step-001_00m00s.png"]
    assert manifest["createdAt"].endswith("Z")


def test_automatic_export_keeps_every_distinct_primary_image(
    tmp_path: Path,
) -> None:
    """Keep complete automatic visual evidence after global representative deduplication."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    segments = []
    for index in range(14):
        screenshot = tmp_path / f"candidate-{index}.png"
        Image.new("RGB", (8, 8), (index, index, index)).save(screenshot)
        segments.append(
            ExportSegment(
                id=f"segment-{index}",
                sequence=index,
                start_seconds=index,
                end_seconds=index + 1,
                raw_text=f"Raw {index}",
                reviewed_text=f"Reviewed {index}",
                speaker=None,
                confidence=None,
                screenshot_id=f"shot-{index}",
                screenshot_path=screenshot,
                screenshot_timestamp_seconds=index + 0.5,
                screenshot_quality_score=index / 20,
                screenshot_association_score=index / 20,
            )
        )

    destination = tmp_path / "bounded.zip"
    build_export_package(
        ExportRequest(
            export_id="bounded",
            project={"id": "project", "name": "Bounded"},
            source={"assetId": "asset"},
            segments=segments,
            raw_transcript_path=raw,
            destination_path=destination,
            output_mode="tutorial",
        )
    )

    with open_package(destination) as archive:
        manifest = json.loads(archive.read("metadata/manifest.json"))
        screenshot_files = [
            name for name in archive.namelist() if name.startswith(f"{SCREENSHOTS}/")
        ]
        llm_input = archive.read("01-ai-input/llm-input.md").decode("utf-8")
    assert len(screenshot_files) == 14
    assert len(manifest["screenshots"]) == 14
    assert len(manifest["segments"]) == 14
    assert all(item["primaryScreenshotId"] is not None for item in manifest["segments"])
    assert llm_input.count("## Evidence step") == 14
    assert "Screenshot: none included" not in llm_input


@pytest.mark.parametrize(
    ("image_count", "expected_batch_count"),
    [(0, 1), (1, 1), (19, 1), (20, 2), (39, 3)],
)
def test_chatgpt_batches_respect_the_configured_file_limit(
    tmp_path: Path,
    image_count: int,
    expected_batch_count: int,
) -> None:
    """Keep every chronological step while limiting each upload folder to twenty files."""
    case_root = tmp_path / f"case-{image_count}"
    case_root.mkdir()
    raw = case_root / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    segments = []
    segment_count = max(1, image_count)
    for index in range(segment_count):
        screenshot = case_root / f"candidate-{index}.png"
        screenshot_id = None
        screenshot_path = None
        if index < image_count:
            Image.new("RGB", (8, 8), (index, 20, 40)).save(screenshot)
            screenshot_id = f"shot-{index}"
            screenshot_path = screenshot
        segments.append(
            ExportSegment(
                id=f"segment-{index}",
                sequence=index,
                start_seconds=index,
                end_seconds=index + 1,
                raw_text=f"Raw {index}",
                reviewed_text=f"Reviewed {index}",
                speaker=None,
                confidence=None,
                screenshot_id=screenshot_id,
                screenshot_path=screenshot_path,
            )
        )

    destination = case_root / "batched.zip"
    build_export_package(
        ExportRequest(
            export_id=f"batched-{image_count}",
            project={"id": "project", "name": "Batched"},
            source={"assetId": "asset"},
            segments=segments,
            raw_transcript_path=raw,
            destination_path=destination,
            output_mode="tutorial",
        )
    )

    with open_package(destination) as archive:
        names = archive.namelist()
        batch_inputs = sorted(
            name
            for name in names
            if name.startswith(f"{BATCHES}/batch-") and name.endswith("/batch-input.md")
        )
        assert len(batch_inputs) == expected_batch_count
        evidence_step_count = 0
        for batch_input in batch_inputs:
            batch_root = batch_input.rsplit("/", maxsplit=1)[0] + "/"
            batch_files = [name for name in names if name.startswith(batch_root)]
            assert len(batch_files) <= 20
            assert sum(name.endswith("/batch-input.md") for name in batch_files) == 1
            evidence_step_count += (
                archive.read(batch_input).decode("utf-8").count("## Evidence step")
            )
        assert evidence_step_count == segment_count


def test_reused_screenshot_is_copied_when_later_batch_needs_it(tmp_path: Path) -> None:
    """Keep consecutive batches self-contained even when visual evidence returns later."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    screenshots = []
    for index in range(3):
        screenshot = tmp_path / f"candidate-{index}.png"
        Image.new("RGB", (8, 8), (index * 30, 20, 40)).save(screenshot)
        screenshots.append(screenshot)
    source_ids = ["shot-a", "shot-b", "shot-c", "shot-a"]
    source_paths = [screenshots[0], screenshots[1], screenshots[2], screenshots[0]]
    segments = [
        ExportSegment(
            id=f"segment-{index}",
            sequence=index,
            start_seconds=index,
            end_seconds=index + 1,
            raw_text="Raw",
            reviewed_text=f"Reviewed {index}",
            speaker=None,
            confidence=None,
            screenshot_id=source_ids[index],
            screenshot_path=source_paths[index],
        )
        for index in range(4)
    ]
    destination = tmp_path / "reused-across-batches.zip"
    build_export_package(
        ExportRequest(
            export_id="reused-across-batches",
            project={"id": "project", "name": "Reused"},
            source={"assetId": "asset"},
            segments=segments,
            raw_transcript_path=raw,
            destination_path=destination,
            output_mode="tutorial",
            chatgpt_max_files_per_batch=3,
        )
    )

    with open_package(destination) as archive:
        names = set(archive.namelist())
        second_batch = archive.read(f"{BATCHES}/batch-02-of-02/batch-input.md").decode("utf-8")
    assert f"{BATCHES}/batch-01-of-02/step-001_00m00s.png" in names
    assert f"{BATCHES}/batch-02-of-02/step-001_00m00s.png" in names
    assert f"{BATCHES}/batch-02-of-02/step-003_00m02s.png" in names
    assert "## Evidence step 4" in second_batch
    assert "Screenshot: step-001_00m00s.png" in second_batch


def test_visually_similar_automatic_images_reuse_the_best_representative(
    tmp_path: Path,
) -> None:
    """Collapse repeated screens globally and keep the strongest automatic evidence."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    segments = []
    for index, score in enumerate((0.1, 0.5, 0.9)):
        screenshot = tmp_path / f"similar-{index}.png"
        Image.new("RGB", (8, 8), (index * 20, 40, 60)).save(screenshot)
        segments.append(
            ExportSegment(
                id=f"segment-{index}",
                sequence=index,
                start_seconds=index,
                end_seconds=index + 1,
                raw_text="Raw",
                reviewed_text="Reviewed",
                speaker=None,
                confidence=None,
                screenshot_id=f"shot-{index}",
                screenshot_path=screenshot,
                screenshot_timestamp_seconds=index + 0.5,
                screenshot_perceptual_hash="0123456789abcdef",
                screenshot_quality_score=score,
                screenshot_association_score=score,
            )
        )

    destination = tmp_path / "similar.zip"
    build_export_package(
        ExportRequest(
            export_id="similar",
            project={"id": "project", "name": "Similar"},
            source={"assetId": "asset"},
            segments=segments,
            raw_transcript_path=raw,
            destination_path=destination,
            output_mode="tutorial",
        )
    )

    with open_package(destination) as archive:
        manifest = json.loads(archive.read("metadata/manifest.json"))
        screenshot_files = [
            name for name in archive.namelist() if name.startswith(f"{SCREENSHOTS}/")
        ]
    assert screenshot_files == ["02-screenshots/step-003_00m02s.png"]
    assert [item["sourceScreenshotId"] for item in manifest["screenshots"]] == ["shot-2"]
    assert {item["sourceScreenshotId"] for item in manifest["associations"]} == {"shot-2"}
    assert len(manifest["associations"]) == 3


def test_manual_images_are_preserved_across_multiple_batches(tmp_path: Path) -> None:
    """Treat explicit review choices as mandatory evidence across bounded upload folders."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    segments = []
    for index in range(4):
        screenshot = tmp_path / f"manual-{index}.png"
        Image.new("RGB", (8, 8), (index * 30, 20, 10)).save(screenshot)
        segments.append(
            ExportSegment(
                id=f"segment-{index}",
                sequence=index,
                start_seconds=index,
                end_seconds=index + 1,
                raw_text="Raw",
                reviewed_text="Reviewed",
                speaker=None,
                confidence=None,
                screenshot_id=f"manual-shot-{index}",
                screenshot_path=screenshot,
                screenshot_perceptual_hash="0123456789abcdef",
                screenshot_user_selected=True,
            )
        )

    destination = tmp_path / "manual.zip"
    build_export_package(
        ExportRequest(
            export_id="manual",
            project={"id": "project", "name": "Manual"},
            source={"assetId": "asset"},
            segments=segments,
            raw_transcript_path=raw,
            destination_path=destination,
            output_mode="tutorial",
            chatgpt_max_files_per_batch=2,
        )
    )

    with open_package(destination) as archive:
        manifest = json.loads(archive.read("metadata/manifest.json"))
        screenshot_files = [
            name for name in archive.namelist() if name.startswith(f"{SCREENSHOTS}/")
        ]
        batch_inputs = [
            name
            for name in archive.namelist()
            if name.startswith(f"{BATCHES}/batch-") and name.endswith("/batch-input.md")
        ]
    assert len(screenshot_files) == 4
    assert len(manifest["screenshots"]) == 4
    assert len(manifest["associations"]) == 4
    assert len(batch_inputs) == 4


def test_thumbnail_only_estimate_and_export_share_selection_and_actual_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Estimate late PNG bytes conservatively and materialize only inside the immutable export."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    thumbnail = tmp_path / "candidate.webp"
    Image.new("RGB", (160, 90), (20, 40, 60)).save(thumbnail, format="WEBP")
    source_video = tmp_path / "source.mp4"
    source_video.write_bytes(b"local source")
    destination = tmp_path / "thumbnail-only.zip"
    segment = ExportSegment(
        id="segment",
        sequence=0,
        start_seconds=0,
        end_seconds=2,
        raw_text="Raw",
        reviewed_text="Reviewed",
        speaker=None,
        confidence=0.9,
        screenshot_id="thumbnail-only",
        screenshot_path=None,
        screenshot_thumbnail_path=thumbnail,
        screenshot_source_video_path=source_video,
        screenshot_timestamp_seconds=1.0,
        screenshot_perceptual_hash="0123456789abcdef",
        screenshot_quality_score=0.9,
        screenshot_association_score=0.9,
        screenshot_width=1280,
        screenshot_height=720,
    )
    request = ExportRequest(
        export_id="thumbnail-only",
        project={"id": "project", "name": "Late materialization"},
        source={"assetId": "asset", "durationSeconds": 2},
        segments=[segment],
        raw_transcript_path=raw,
        destination_path=destination,
        output_mode="tutorial",
        original_video_path=source_video,
        selection_strategy="adaptive_v1",
        duration_seconds=2,
    )
    monkeypatch.setattr(
        exporting,
        "decode_exact_frame",
        lambda *_: np.full((720, 1280, 3), 80, dtype=np.uint8),
    )

    estimate = estimate_export_package(request)
    metrics = MetricsCollector("project", "export", "export", "adaptive_v1")
    build_export_package(request, metrics)

    assert estimate.canonical_image_count == 1
    assert estimate.batch_count == 1
    assert estimate.batch_image_copy_count == 1
    assert estimate.estimated_zip_bytes >= round(1280 * 720 * 0.75)
    assert metrics.counters["exportCanonicalImageCount"] == 1
    assert metrics.counters["batchCount"] == 1
    assert metrics.counters["exportBatchCopyCount"] == 1
    with open_package(destination) as archive:
        assert "02-screenshots/step-001_00m00s.png" in archive.namelist()
    assert not list(tmp_path.glob("02-screenshots/*.png"))


def test_archive_has_one_named_root_and_numbered_layout(tmp_path: Path) -> None:
    """Name the root folder after the archive and list every path of the ADR 0013 layout."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    screenshot = tmp_path / "late.png"
    Image.new("RGB", (8, 8), (9, 9, 9)).save(screenshot)
    video = tmp_path / "capture.MP4"
    video.write_bytes(b"video")
    created_at = datetime(2026, 10, 28, 15, 30, tzinfo=UTC)
    archive_name = export_archive_name(created_at, "meeting", "Sprint Planning", 2)
    assert archive_name == "2026-10-28_meeting_sprint-planning_export-02"
    segments = [
        ExportSegment(
            id=f"segment-{index}",
            sequence=index,
            start_seconds=start,
            end_seconds=start + 5,
            raw_text="Raw",
            reviewed_text="Reviewed",
            speaker=None,
            confidence=None,
            screenshot_id="shot-late" if index == 2 else None,
            screenshot_path=screenshot if index == 2 else None,
        )
        for index, start in enumerate((0.0, 20.0, 42.9))
    ]
    destination = tmp_path / "exports" / "narrastep-export-id.zip"
    build_export_package(
        ExportRequest(
            export_id="export-id",
            project={"id": "project", "name": "Sprint Planning"},
            source={"assetId": "asset"},
            segments=segments,
            raw_transcript_path=raw,
            destination_path=destination,
            output_mode="meeting",
            original_video_path=video,
            include_original_video=True,
            created_at=created_at,
            archive_name=archive_name,
        )
    )

    with open_package(destination) as archive:
        assert archive.root == archive_name
        assert all(name.startswith(f"{archive_name}/") for name in archive.raw_names())
        names = set(archive.namelist())
        manifest = json.loads(archive.read("metadata/manifest.json"))
        readme = archive.read("README.md").decode("utf-8")
        prompt_guide = archive.read(f"{BATCHES}/README.md").decode("utf-8")
    assert names == {
        "README.md",
        "01-ai-input/generation-prompt.md",
        "01-ai-input/llm-input.md",
        "02-screenshots/step-003_00m42s.png",
        "03-transcript/raw-transcript.json",
        "03-transcript/reviewed-transcript.md",
        "03-transcript/subtitles.srt",
        f"{BATCHES}/README.md",
        f"{BATCHES}/batch-01-of-01/batch-input.md",
        f"{BATCHES}/batch-01-of-01/step-003_00m42s.png",
        "metadata/manifest.json",
        "metadata/source.json",
        "source/original-video.mp4",
    }
    assert manifest["schemaVersion"] == "0.3.0"
    assert manifest["archiveName"] == archive_name
    assert manifest["segments"][2]["primaryScreenshotPath"] == "02-screenshots/step-003_00m42s.png"
    assert manifest["paths"]["readme"] == "README.md"
    assert manifest["paths"]["chatgptBatches"] == BATCHES
    assert manifest["paths"]["originalVideo"] == "source/original-video.mp4"
    # Every path the manifest lists exists in the archive.
    listed = [value for value in manifest["paths"].values() if value is not None]
    assert all(
        path in names or any(name.startswith(f"{path}/") for name in names) for path in listed
    )
    assert "Meeting" in readme and "(1 in this export)" in readme
    assert "../01-ai-input/generation-prompt.md" in prompt_guide


def test_archive_without_a_name_uses_the_adr_pattern(tmp_path: Path) -> None:
    """Derive the root folder from date, type and project name when no name is supplied."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    destination = tmp_path / "default.zip"
    build_export_package(
        ExportRequest(
            export_id="default",
            project={"id": "project", "name": "Démo été"},
            source={"assetId": "asset"},
            segments=[],
            raw_transcript_path=raw,
            destination_path=destination,
            output_mode="tutorial",
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        )
    )
    with open_package(destination) as archive:
        assert archive.root == "2026-01-02_tutorial_demo-ete_export-01"


def test_batch_folders_are_numbered_in_reading_order() -> None:
    """Pad batch numbers so folders sort chronologically, widening past ninety-nine batches."""
    assert batch_folder_name(1, 3) == "batch-01-of-03"
    assert batch_folder_name(12, 12) == "batch-12-of-12"
    assert batch_folder_name(7, 100) == "batch-007-of-100"


@pytest.mark.parametrize(
    "archive_name",
    ["../escape", "a/b", "a\\b", ".hidden", "", "name.", "with space", "é" * 3],
)
def test_unsafe_archive_names_are_rejected_without_output(
    tmp_path: Path, archive_name: str
) -> None:
    """Refuse names that are not plain portable file names before anything is written."""
    raw = tmp_path / "raw.json"
    raw.write_text('{"segments": []}', encoding="utf-8")
    destination = tmp_path / "unsafe.zip"
    request = ExportRequest(
        export_id="unsafe",
        project={"id": "project", "name": "Unsafe"},
        source={"assetId": "asset"},
        segments=[],
        raw_transcript_path=raw,
        destination_path=destination,
        output_mode="general",
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
        archive_name=archive_name or None,
    )
    if archive_name == "":
        # An empty name falls back to the generated pattern instead of failing.
        build_export_package(request)
        assert destination.exists()
        return
    with pytest.raises(ValueError):
        build_export_package(request)
    assert not destination.exists()
    assert not destination.with_suffix(".zip.partial").exists()
