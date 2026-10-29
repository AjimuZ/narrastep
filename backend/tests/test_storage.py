"""Storage accounting and the guarded removal of the original video (P09.2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command
from app.config import get_settings
from app.models import (
    ExportStatus,
    MediaAsset,
    Project,
    ProjectExport,
    ProjectState,
    Screenshot,
    SegmentScreenshotAssociation,
    TranscriptSegment,
)
from app.services.storage import directory_bytes, measure_project_storage
from app.services.workspaces import create_project_workspace

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _project(
    session: Session,
    state: ProjectState = ProjectState.REVIEW,
    name: str = "Storage",
    *,
    with_package: bool = False,
) -> tuple[Project, Path]:
    """Arrange a project with a 1000-byte source video, a microphone track and optional package."""
    settings = get_settings()
    project = Project(name=name, state=state)
    session.add(project)
    session.flush()
    workspace = create_project_workspace(settings.data_root, project.id)
    source = workspace / "source" / "source.mp4"
    source.write_bytes(b"v" * 1000)
    (workspace / "source" / "microphone.webm").write_bytes(b"m" * 200)
    session.add(
        MediaAsset(
            project_id=project.id,
            display_filename="demo.mp4",
            stored_filename="source.mp4",
            relative_path=source.relative_to(settings.data_root).as_posix(),
            content_type="video/mp4",
            size_bytes=1000,
            duration_seconds=4,
            width=640,
            height=360,
            container_format="mp4",
            metadata_json={},
        )
    )
    if with_package:
        export = ProjectExport(project_id=project.id, status=ExportStatus.COMPLETED)
        session.add(export)
        session.flush()
        archive = workspace / "exports" / f"narrastep-{export.id}.zip"
        archive.write_bytes(b"z" * 300)
        export.relative_path = archive.relative_to(settings.data_root).as_posix()
        export.size_bytes = 300
    session.commit()
    return project, workspace


def _get(client: TestClient, project: Project) -> dict[str, object]:
    """Return the project's entry of the storage list."""
    items = client.get("/api/v1/storage").json()["items"]
    return next(item for item in items if item["projectId"] == project.id)


def test_measures_each_folder_and_ignores_symbolic_links(session: Session, tmp_path: Path) -> None:
    """Add up regular files by folder; a link to a large outside file adds nothing."""
    project, workspace = _project(session, with_package=True)
    (workspace / "audio" / "a.wav").write_bytes(b"a" * 50)
    (workspace / "transcript" / "t.json").write_bytes(b"t" * 20)
    (workspace / "screenshots" / "full").mkdir()
    (workspace / "screenshots" / "full" / "s.png").write_bytes(b"s" * 70)
    (workspace / "diagnostics" / "d.json").write_bytes(b"d" * 10)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"o" * 5000)
    os.symlink(outside, workspace / "audio" / "link.bin")

    usage = measure_project_storage(get_settings().data_root, project.id)

    assert (usage.source, usage.audio, usage.transcript) == (1200, 50, 20)
    assert (usage.screenshots, usage.exports, usage.other) == (70, 300, 10)
    assert usage.total == 1650


def test_missing_workspace_measures_zero(session: Session) -> None:
    """Report zero for a project whose folder is gone, and for a folder that does not exist."""
    project, workspace = _project(session)
    assert directory_bytes(workspace / "nope") == 0
    for child in sorted(workspace.rglob("*"), reverse=True):
        child.unlink() if child.is_file() else child.rmdir()
    workspace.rmdir()

    assert measure_project_storage(get_settings().data_root, project.id).total == 0


def test_storage_lists_projects_largest_first_with_free_space(
    client: TestClient, session: Session
) -> None:
    """Return the per-project figures, their sum and the free space of the data volume."""
    small, _ = _project(session, name="Small")
    large, workspace = _project(session, name="Large", with_package=True)
    (workspace / "audio" / "a.wav").write_bytes(b"a" * 4000)

    response = client.get("/api/v1/storage")

    assert response.status_code == 200
    body = response.json()
    assert [item["projectId"] for item in body["items"]] == [large.id, small.id]
    assert body["items"][0]["categories"] == {
        "source": 1200,
        "audio": 4000,
        "transcript": 0,
        "screenshots": 0,
        "exports": 300,
        "other": 0,
    }
    assert body["items"][0]["totalBytes"] == 5500
    assert body["items"][0]["sourceVideoBytes"] == 1000
    assert body["totalBytes"] == 5500 + 1200
    assert body["freeBytes"] > 0


def test_removal_is_refused_before_any_export(client: TestClient, session: Session) -> None:
    """Keep the video when no package exists: the project is only in review."""
    project, workspace = _project(session)
    assert _get(client, project)["removalBlockedReason"] == "package_required"

    response = client.delete(f"/api/v1/projects/{project.id}/source-video")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PACKAGE_REQUIRED"
    assert (workspace / "source" / "source.mp4").is_file()
    assert (workspace / "source" / "microphone.webm").is_file()
    session.expire_all()
    asset = session.get(Project, project.id).media_asset  # type: ignore[union-attr]
    assert asset is not None and asset.source_removed_at is None


def test_removal_is_refused_when_the_package_file_is_gone(
    client: TestClient, session: Session
) -> None:
    """A completed export record without its ZIP on disk is not a package."""
    project, workspace = _project(session, ProjectState.COMPLETED, with_package=True)
    for archive in (workspace / "exports").glob("*.zip"):
        archive.unlink()

    response = client.delete(f"/api/v1/projects/{project.id}/source-video")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PACKAGE_REQUIRED"
    assert (workspace / "source" / "source.mp4").is_file()


def test_removal_is_refused_after_the_review_changed_since_the_package(
    client: TestClient, session: Session
) -> None:
    """Editing a completed project returns it to review, so the old package no longer counts."""
    project, workspace = _project(session, ProjectState.REVIEW, with_package=True)

    response = client.delete(f"/api/v1/projects/{project.id}/source-video")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PACKAGE_REQUIRED"
    assert (workspace / "source" / "source.mp4").is_file()


def test_removal_is_refused_during_background_work(client: TestClient, session: Session) -> None:
    """Never touch the source while processing or an export may be reading it."""
    project, workspace = _project(session, ProjectState.EXPORTING, with_package=True)

    response = client.delete(f"/api/v1/projects/{project.id}/source-video")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROJECT_BUSY"
    assert (workspace / "source" / "source.mp4").is_file()


def test_removal_of_an_unknown_project_and_of_a_project_without_video(
    client: TestClient, session: Session
) -> None:
    """Answer 404 for both: there is nothing to remove."""
    assert client.delete("/api/v1/projects/nope/source-video").status_code in {400, 404}
    project = Project(name="Empty", state=ProjectState.DRAFT)
    session.add(project)
    session.commit()

    response = client.delete(f"/api/v1/projects/{project.id}/source-video")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_ASSET_MISSING"
    assert _get(client, project)["removalBlockedReason"] == "no_video"


def test_removal_after_export_frees_the_source_and_keeps_the_package(
    client: TestClient, session: Session
) -> None:
    """Delete the video and kept tracks, keep the package and report the freed space once."""
    project, workspace = _project(session, ProjectState.COMPLETED, with_package=True)
    before = _get(client, project)
    assert before["removalBlockedReason"] is None
    archive = next((workspace / "exports").glob("*.zip"))

    response = client.delete(f"/api/v1/projects/{project.id}/source-video")

    assert response.status_code == 200
    body = response.json()
    assert body["categories"]["source"] == 0
    assert body["sourceVideoBytes"] == 0
    assert body["totalBytes"] == before["totalBytes"] - 1200
    assert body["sourceRemovedAt"] is not None
    assert body["removalBlockedReason"] == "already_removed"
    assert not list((workspace / "source").iterdir())
    assert archive.is_file()
    stored = client.get(f"/api/v1/projects/{project.id}").json()["videoAsset"]
    assert stored["sourceRemovedAt"] is not None
    assert stored["sizeBytes"] == 1000

    again = client.delete(f"/api/v1/projects/{project.id}/source-video")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "SOURCE_VIDEO_REMOVED"


def test_removal_finishes_after_a_partial_failure(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed deletion leaves the record unmarked, so a retry removes what is left."""
    project, workspace = _project(session, ProjectState.COMPLETED, with_package=True)
    original_unlink = Path.unlink
    calls = {"count": 0}

    def flaky_unlink(self: Path, missing_ok: bool = False) -> None:
        if self.parent.name == "source":
            calls["count"] += 1
            if calls["count"] == 2:
                raise PermissionError("locked")
        original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    with pytest.raises(PermissionError):
        client.delete(f"/api/v1/projects/{project.id}/source-video")
    monkeypatch.undo()
    session.expire_all()
    asset = session.get(Project, project.id).media_asset  # type: ignore[union-attr]
    assert asset is not None and asset.source_removed_at is None
    assert _get(client, project)["removalBlockedReason"] is None

    retry = client.delete(f"/api/v1/projects/{project.id}/source-video")

    assert retry.status_code == 200
    assert not list((workspace / "source").iterdir())


def test_features_that_need_the_video_say_it_was_removed(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Playback, exact-frame recovery and packages that need the video fail with one clear code."""
    project, workspace = _project(session, ProjectState.COMPLETED, with_package=True)
    segment = TranscriptSegment(
        project_id=project.id,
        sequence=0,
        start_seconds=0.0,
        end_seconds=2.0,
        raw_text="Hello",
        reviewed_text="Hello",
    )
    session.add(segment)
    session.flush()
    screenshot = Screenshot(
        project_id=project.id,
        timestamp_seconds=1.0,
        width=640,
        height=360,
        perceptual_hash="0",
        quality_score=0.5,
    )
    session.add(screenshot)
    session.flush()
    session.add(
        SegmentScreenshotAssociation(
            segment_id=segment.id, screenshot_id=screenshot.id, is_primary=True, rank=0, score=0.5
        )
    )
    session.commit()
    assert client.delete(f"/api/v1/projects/{project.id}/source-video").status_code == 200

    video = client.get(f"/api/v1/projects/{project.id}/video")
    assert video.status_code == 410
    assert video.json()["error"]["code"] == "SOURCE_VIDEO_REMOVED"

    recover = client.post(
        f"/api/v1/segments/{segment.id}/screenshot-from-video", json={"timestampSeconds": 1.0}
    )
    assert recover.status_code == 409
    assert recover.json()["error"]["code"] == "SOURCE_VIDEO_REMOVED"

    content = client.get(f"/api/v1/screenshots/{screenshot.id}/content")
    assert content.status_code == 404
    assert content.json()["error"]["code"] == "SOURCE_VIDEO_REMOVED"

    with_video = client.post(
        f"/api/v1/projects/{project.id}/exports", json={"includeOriginalVideo": True}
    )
    assert with_video.status_code == 409
    assert with_video.json()["error"]["code"] == "SOURCE_VIDEO_REMOVED"

    needs_frame = client.post(
        f"/api/v1/projects/{project.id}/exports", json={"includeOriginalVideo": False}
    )
    assert needs_frame.status_code == 409
    assert needs_frame.json()["error"]["code"] == "SOURCE_VIDEO_REMOVED"

    # A screenshot already saved as an image does not need the video, so a package is queued.
    image = workspace / "screenshots" / "full" / f"{screenshot.id}.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"png")
    screenshot.relative_path = image.relative_to(get_settings().data_root).as_posix()
    session.commit()
    from app.tasks.celery_app import export_project_task

    monkeypatch.setattr(export_project_task, "apply_async", lambda **_: None)
    queued = client.post(
        f"/api/v1/projects/{project.id}/exports", json={"includeOriginalVideo": False}
    )
    assert queued.status_code == 202


def test_deleting_a_project_still_removes_everything(client: TestClient, session: Session) -> None:
    """Project deletion is independent of the video removal and leaves no workspace behind."""
    project, workspace = _project(session, ProjectState.COMPLETED, with_package=True)
    assert client.delete(f"/api/v1/projects/{project.id}/source-video").status_code == 200

    assert client.delete(f"/api/v1/projects/{project.id}").status_code == 204
    assert not workspace.exists()
    assert client.get("/api/v1/storage").json()["items"] == []


def test_source_removal_revision_adds_a_nullable_column_and_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep existing videos marked as present on upgrade and drop the column on downgrade."""
    data_root = tmp_path / "migration-storage"
    database_url = f"sqlite:///{(data_root / 'narrastep.db').as_posix()}"
    monkeypatch.setenv("NARRASTEP_DATA_DIR", str(data_root))
    monkeypatch.setenv("NARRASTEP_DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    try:
        command.upgrade(config, "20261029_0008")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, source_language, state, created_at,"
                    " updated_at) VALUES ('p', 'P', 'auto', 'review', '2026-10-29', '2026-10-29')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO media_assets (id, project_id, display_filename, stored_filename,"
                    " relative_path, content_type, size_bytes, duration_seconds, width, height,"
                    " container_format, metadata_json, created_at) VALUES ('a', 'p', 'd.mp4',"
                    " 's.mp4', 'projects/p/source/s.mp4', 'video/mp4', 1, 1.0, 1, 1, 'mp4', '{}',"
                    " '2026-10-29')"
                )
            )
        engine.dispose()

        command.upgrade(config, "head")
        command.check(config)
        engine = create_engine(database_url)
        with engine.connect() as connection:
            row = connection.execute(text("SELECT source_removed_at FROM media_assets")).scalar()
            assert row is None
        engine.dispose()

        command.downgrade(config, "20261029_0008")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            columns = {
                row[1] for row in connection.execute(text("PRAGMA table_info(media_assets)"))
            }
        engine.dispose()
        assert "source_removed_at" not in columns
    finally:
        get_settings.cache_clear()
