"""Immutable ZIP package construction tests."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from app.services import exporting
from app.services.diagnostics import MetricsCollector
from app.services.exporting import (
    ExportRequest,
    ExportSegment,
    build_export_package,
    estimate_export_package,
)


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
    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        assert {
            "manifest.json",
            "transcript/raw-transcript.json",
            "transcript/reviewed-transcript.md",
            "transcript/subtitles.srt",
            "screenshots/step-001.png",
            "llm/llm-input.md",
            "llm/generation-prompt.md",
            "chatgpt-batches/README.md",
            "chatgpt-batches/batch-001/batch-input.md",
            "chatgpt-batches/batch-001/step-001.png",
            "metadata/source.json",
        }.issubset(names)
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["schemaVersion"] == "0.2.0"
        assert manifest["createdAt"].endswith("Z")
        assert manifest["segments"][0]["primaryScreenshotId"] == "shot-1"
        assert manifest["segments"][0]["primaryScreenshotPath"] == "screenshots/step-001.png"
        assert manifest["segments"][1]["primaryScreenshotId"] is None
        assert manifest["paths"]["rawTranscript"] == "transcript/raw-transcript.json"
        assert manifest["paths"]["llmInput"] == "llm/llm-input.md"
        reviewed = archive.read("transcript/reviewed-transcript.md").decode("utf-8")
        assert "No screenshot was included for this step" in reviewed
        assert "(../screenshots/step-001.png)" in reviewed
        llm_input = archive.read("llm/llm-input.md").decode("utf-8")
        assert "../screenshots/step-001.png" in llm_input
        assert "Do not invent unsupported steps" in llm_input
        batch_input = archive.read("chatgpt-batches/batch-001/batch-input.md").decode("utf-8")
        assert "Narrastep analysis batch 1 of 1" in batch_input
        assert "Evidence step 1" in batch_input
        assert "Screenshot: step-001.png" in batch_input
        assert "Evidence step 2" in batch_input
        assert "Screenshot: none included" in batch_input
        assert all(not name.startswith("source/") for name in names)


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

    with zipfile.ZipFile(destination) as archive:
        assert "source/original-video.mp4" in archive.namelist()
        assert "chatgpt-batches/batch-001/batch-input.md" in archive.namelist()
        assert not any(
            name.startswith("chatgpt-batches/batch-001/step-") for name in archive.namelist()
        )
        manifest = json.loads(archive.read("manifest.json"))
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
    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        screenshot_files = [name for name in archive.namelist() if name.startswith("screenshots/")]
    assert screenshot_files == ["screenshots/step-001.png"]
    assert [item["sourceScreenshotId"] for item in manifest["screenshots"]] == ["shared-source-id"]
    assert [item["path"] for item in manifest["associations"]] == [
        "screenshots/step-001.png",
        "screenshots/step-001.png",
    ]
    assert [item["primaryScreenshotPath"] for item in manifest["segments"]] == [
        "screenshots/step-001.png",
        "screenshots/step-001.png",
    ]
    with zipfile.ZipFile(destination) as archive:
        batch_images = [
            name
            for name in archive.namelist()
            if name.startswith("chatgpt-batches/batch-001/step-")
        ]
    assert batch_images == ["chatgpt-batches/batch-001/step-001.png"]
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

    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        screenshot_files = [name for name in archive.namelist() if name.startswith("screenshots/")]
        llm_input = archive.read("llm/llm-input.md").decode("utf-8")
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

    with zipfile.ZipFile(destination) as archive:
        names = archive.namelist()
        batch_inputs = sorted(
            name
            for name in names
            if name.startswith("chatgpt-batches/batch-") and name.endswith("/batch-input.md")
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

    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
        second_batch = archive.read("chatgpt-batches/batch-002/batch-input.md").decode("utf-8")
    assert "chatgpt-batches/batch-001/step-001.png" in names
    assert "chatgpt-batches/batch-002/step-001.png" in names
    assert "chatgpt-batches/batch-002/step-003.png" in names
    assert "## Evidence step 4" in second_batch
    assert "Screenshot: step-001.png" in second_batch


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

    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        screenshot_files = [name for name in archive.namelist() if name.startswith("screenshots/")]
    assert screenshot_files == ["screenshots/step-003.png"]
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

    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        screenshot_files = [name for name in archive.namelist() if name.startswith("screenshots/")]
        batch_inputs = [
            name
            for name in archive.namelist()
            if name.startswith("chatgpt-batches/batch-") and name.endswith("/batch-input.md")
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
    with zipfile.ZipFile(destination) as archive:
        assert "screenshots/step-001.png" in archive.namelist()
    assert not list(tmp_path.glob("screenshots/*.png"))
