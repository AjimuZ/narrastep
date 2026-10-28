"""Transactional transcript structure and screenshot selection API tests."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    MediaAsset,
    Project,
    ProjectState,
    Screenshot,
    SegmentLineage,
    SegmentScreenshotAssociation,
    TranscriptSegment,
)
from app.services.workspaces import create_project_workspace


def _source_asset(project: Project, relative_path: str) -> MediaAsset:
    """Build one valid project-owned source record for recovery and materialization tests."""
    return MediaAsset(
        project_id=project.id,
        display_filename="source.mp4",
        stored_filename="source.mp4",
        relative_path=relative_path,
        content_type="video/mp4",
        size_bytes=16,
        duration_seconds=10.0,
        width=1280,
        height=720,
        container_format="mp4",
        metadata_json={},
    )


def test_split_then_merge_preserves_lineage_and_word_evidence(
    client: TestClient, session: Session
) -> None:
    """Archive sources, partition timed words, and atomically restore one merged child."""
    project = Project(name="Review", state=ProjectState.REVIEW)
    session.add(project)
    session.flush()
    source = TranscriptSegment(
        project_id=project.id,
        sequence=0,
        start_seconds=0.0,
        end_seconds=10.0,
        raw_text="Alpha beta",
        reviewed_text="Alpha beta",
        raw_words=[
            {"text": "Alpha ", "startSeconds": 0.0, "endSeconds": 4.0, "probability": 0.9},
            {"text": "beta", "startSeconds": 6.0, "endSeconds": 9.0, "probability": 0.8},
        ],
        confidence=0.85,
    )
    session.add(source)
    session.commit()

    edited = client.patch(f"/api/v1/segments/{source.id}", json={"reviewedText": "Alpha beta!"})
    assert edited.status_code == 200
    assert edited.json()["rawText"] == "Alpha beta"
    assert edited.json()["reviewedText"] == "Alpha beta!"

    split = client.post(
        f"/api/v1/segments/{source.id}/split",
        json={"leftReviewedText": "Alpha ", "rightReviewedText": "beta!", "splitSeconds": 5},
    )
    assert split.status_code == 200
    children = split.json()
    assert [item["segmentIndex"] for item in children] == [0, 1]
    assert [word["text"] for word in children[0]["words"]] == ["Alpha "]
    assert [word["text"] for word in children[1]["words"]] == ["beta"]

    merged = client.post(
        "/api/v1/segments/merge",
        json={"segmentIds": [children[0]["id"], children[1]["id"]]},
    )
    assert merged.status_code == 200
    assert merged.json()["reviewedText"] == "Alpha \nbeta!"
    assert merged.json()["segmentIndex"] == 0

    lineage_count = session.scalar(select(func.count()).select_from(SegmentLineage))
    assert lineage_count == 4
    active = client.get(f"/api/v1/projects/{project.id}/segments")
    assert [item["id"] for item in active.json()] == [merged.json()["id"]]


def test_screenshot_primary_can_be_changed_and_removed(
    client: TestClient, session: Session
) -> None:
    """Enforce one primary while retaining alternatives as reusable evidence."""
    project = Project(name="Screenshots", state=ProjectState.REVIEW)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    segment = TranscriptSegment(
        project_id=project.id,
        sequence=0,
        start_seconds=0,
        end_seconds=5,
        raw_text="Step",
        reviewed_text="Step",
        raw_words=[],
    )
    session.add(segment)
    session.flush()
    screenshots: list[Screenshot] = []
    for index, timestamp in enumerate((1.0, 4.0), start=1):
        path = workspace / "screenshots" / f"{index}.png"
        Image.new("RGB", (16, 9), (index * 30, 40, 50)).save(path)
        screenshot = Screenshot(
            project_id=project.id,
            relative_path=path.relative_to(get_settings().data_root).as_posix(),
            timestamp_seconds=timestamp,
            perceptual_hash=f"{index:016x}",
            quality_score=0.8,
            width=16,
            height=9,
        )
        session.add(screenshot)
        screenshots.append(screenshot)
    session.flush()
    session.add_all(
        [
            SegmentScreenshotAssociation(
                segment_id=segment.id,
                screenshot_id=screenshots[0].id,
                is_primary=True,
                rank=0,
                score=0.9,
            ),
            SegmentScreenshotAssociation(
                segment_id=segment.id,
                screenshot_id=screenshots[1].id,
                rank=1,
                score=0.7,
            ),
        ]
    )
    session.commit()

    selected = client.put(
        f"/api/v1/segments/{segment.id}/primary-screenshot/{screenshots[1].id}", json={}
    )
    assert selected.status_code == 200
    assert selected.json()["role"] == "primary"
    assert selected.json()["isUserSelected"] is True

    content = client.get(f"/api/v1/screenshots/{screenshots[1].id}/content")
    assert content.status_code == 200
    assert content.headers["content-type"] == "image/png"
    assert content.headers["content-disposition"].startswith("inline")

    associations = client.get(f"/api/v1/segments/{segment.id}/screenshots")
    assert associations.status_code == 200
    assert sum(item["role"] == "primary" for item in associations.json()) == 1

    removed = client.delete(f"/api/v1/segments/{segment.id}/primary-screenshot")
    assert removed.status_code == 204
    after = client.get(f"/api/v1/segments/{segment.id}/screenshots").json()
    assert all(item["role"] == "alternative" for item in after)


def test_exact_frame_recovery_creates_both_derivatives_and_dashboard_counts(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recover a bounded source frame as explicit primary evidence with both local derivatives."""
    project = Project(name="Recovery", state=ProjectState.REVIEW)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    source = workspace / "source" / "source.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"local video bytes")
    session.add(_source_asset(project, source.relative_to(get_settings().data_root).as_posix()))
    segment = TranscriptSegment(
        project_id=project.id,
        sequence=0,
        start_seconds=0,
        end_seconds=5,
        raw_text="Recover this state",
        reviewed_text="Recover this state",
        raw_words=[],
    )
    session.add(segment)
    session.commit()
    frame = np.full((90, 160, 3), 120, dtype=np.uint8)
    monkeypatch.setattr("app.routes.api.decode_exact_frame", lambda *_: frame)

    recovered = client.post(
        f"/api/v1/segments/{segment.id}/screenshot-from-video",
        json={"timestampSeconds": 3.25},
    )

    assert recovered.status_code == 201
    payload = recovered.json()
    assert payload["role"] == "primary"
    assert payload["isUserSelected"] is True
    assert payload["coverageScore"] == 1.0
    assert payload["screenshot"]["selectionReason"] == "manual_recovery"
    assert payload["screenshot"]["materializationState"] == "png_ready"
    assert payload["screenshot"]["sharedSegmentCount"] == 1
    assert payload["screenshot"]["clusterId"].startswith("manual-")
    assert payload["screenshot"]["visualChangeScore"] is None
    assert payload["screenshot"]["textChangeScore"] is None
    assert payload["roleRankReason"] == "manual_recovery"
    assert client.get(f"/api/v1/screenshots/{payload['screenshotId']}/thumbnail").status_code == 200
    assert client.get(f"/api/v1/screenshots/{payload['screenshotId']}/content").status_code == 200
    dashboard_project = client.get(f"/api/v1/projects/{project.id}").json()
    assert dashboard_project["activeSegmentCount"] == 1
    assert dashboard_project["screenshotCount"] == 1
    estimate_without_video = client.get(
        f"/api/v1/projects/{project.id}/export-estimate?includeOriginalVideo=false"
    )
    estimate_with_video = client.get(
        f"/api/v1/projects/{project.id}/export-estimate?includeOriginalVideo=true"
    )
    assert estimate_without_video.status_code == 200
    assert estimate_without_video.json()["canonicalImageCount"] == 1
    assert estimate_without_video.json()["isApproximate"] is True
    assert (
        estimate_with_video.json()["estimatedZipBytes"]
        > estimate_without_video.json()["estimatedZipBytes"]
    )

    outside = client.post(
        f"/api/v1/segments/{segment.id}/screenshot-from-video",
        json={"timestampSeconds": 11},
    )
    assert outside.status_code == 422
    assert outside.json()["error"]["code"] == "INVALID_SCREENSHOT_TIMESTAMP"


def test_thumbnail_only_content_materializes_once_from_local_source(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cache a thumbnail-only full PNG idempotently without changing its source timestamp."""
    project = Project(name="Late materialization", state=ProjectState.REVIEW)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    source = workspace / "source" / "source.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"local video bytes")
    session.add(_source_asset(project, source.relative_to(get_settings().data_root).as_posix()))
    thumbnail = workspace / "screenshots" / "thumbnails" / "late.webp"
    thumbnail.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (160, 90), (20, 30, 40)).save(thumbnail, format="WEBP")
    screenshot = Screenshot(
        project_id=project.id,
        relative_path=None,
        thumbnail_relative_path=thumbnail.relative_to(get_settings().data_root).as_posix(),
        materialization_state="thumbnail_only",
        selection_reason="coverage",
        timestamp_seconds=4.0,
        perceptual_hash="0123456789abcdef",
        quality_score=0.8,
        width=1280,
        height=720,
    )
    session.add(screenshot)
    session.commit()
    decode_calls: list[float] = []

    def fake_decode(_: object, timestamp_seconds: float) -> np.ndarray:
        """Record the authoritative timestamp used for late full-image reconstruction."""
        decode_calls.append(timestamp_seconds)
        return np.full((90, 160, 3), 80, dtype=np.uint8)

    monkeypatch.setattr("app.services.screenshot_materialization.decode_exact_frame", fake_decode)

    thumbnail_response = client.get(f"/api/v1/screenshots/{screenshot.id}/thumbnail")
    first_content = client.get(f"/api/v1/screenshots/{screenshot.id}/content")
    second_content = client.get(f"/api/v1/screenshots/{screenshot.id}/content")

    assert thumbnail_response.status_code == 200
    assert thumbnail_response.headers["content-type"] == "image/webp"
    assert first_content.status_code == second_content.status_code == 200
    assert decode_calls == [4.0]
    session.expire_all()
    materialized = session.get(Screenshot, screenshot.id)
    assert materialized is not None
    assert materialized.materialization_state == "png_ready"
    assert materialized.relative_path is not None


def test_split_rejects_blank_children_and_exporting_blocks_review_mutation(
    client: TestClient, session: Session
) -> None:
    """Enforce meaningful split text and serialize immutable export capture against edits."""
    project = Project(name="Busy review", state=ProjectState.REVIEW)
    session.add(project)
    session.flush()
    segment = TranscriptSegment(
        project_id=project.id,
        sequence=0,
        start_seconds=0,
        end_seconds=10,
        raw_text="Alpha beta",
        reviewed_text="Alpha beta",
        raw_words=[],
    )
    session.add(segment)
    session.commit()

    blank = client.post(
        f"/api/v1/segments/{segment.id}/split",
        json={"leftReviewedText": "", "rightReviewedText": "Alpha beta", "splitSeconds": 5},
    )
    assert blank.status_code == 422
    assert blank.json()["error"]["code"] == "INVALID_SEGMENT_OPERATION"

    project.state = ProjectState.EXPORTING
    session.commit()
    edited = client.patch(f"/api/v1/segments/{segment.id}", json={"reviewedText": "Changed"})
    split = client.post(
        f"/api/v1/segments/{segment.id}/split",
        json={"leftReviewedText": "Alpha ", "rightReviewedText": "beta", "splitSeconds": 5},
    )
    assert edited.status_code == split.status_code == 409
    assert edited.json()["error"]["code"] == "PROJECT_BUSY"


def test_split_preserves_low_ranked_explicit_primary_in_applicable_child(
    client: TestClient, session: Session
) -> None:
    """Keep explicit evidence when five higher-scoring alternatives would otherwise slice it."""
    project = Project(name="Selected evidence", state=ProjectState.REVIEW)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(get_settings().data_root, project.id)
    segment = TranscriptSegment(
        project_id=project.id,
        sequence=0,
        start_seconds=0,
        end_seconds=10,
        raw_text="Left right",
        reviewed_text="Left right",
        raw_words=[],
    )
    session.add(segment)
    session.flush()
    screenshots: list[Screenshot] = []
    for index, timestamp in enumerate((0.0, 1.5, 2.0, 2.5, 3.0, 3.5), start=1):
        path = workspace / "screenshots" / f"preserve-{index}.png"
        Image.new("RGB", (8, 8), (index, index, index)).save(path)
        screenshot = Screenshot(
            project_id=project.id,
            relative_path=path.relative_to(get_settings().data_root).as_posix(),
            timestamp_seconds=timestamp,
            perceptual_hash=f"{index:016x}",
            quality_score=0.0 if index == 1 else 1.0,
            width=8,
            height=8,
        )
        session.add(screenshot)
        screenshots.append(screenshot)
    session.flush()
    session.add_all(
        [
            SegmentScreenshotAssociation(
                segment_id=segment.id,
                screenshot_id=screenshot.id,
                is_primary=index == 0,
                user_selected=index == 0,
                rank=index,
                score=0.01 if index == 0 else 0.9,
            )
            for index, screenshot in enumerate(screenshots)
        ]
    )
    session.commit()

    response = client.post(
        f"/api/v1/segments/{segment.id}/split",
        json={"leftReviewedText": "Left ", "rightReviewedText": "right", "splitSeconds": 5},
    )
    assert response.status_code == 200
    left_id = response.json()[0]["id"]
    left_associations = client.get(f"/api/v1/segments/{left_id}/screenshots").json()
    primary = next(item for item in left_associations if item["role"] == "primary")
    assert primary["screenshotId"] == screenshots[0].id
    assert primary["isUserSelected"] is True
    assert len(left_associations) == 5
