"""Including a generated document in the next export package (P06.4c)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from package_reader import open_package
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    ExportStatus,
    Generation,
    JobStatus,
    MediaAsset,
    OutputMode,
    Project,
    ProjectExport,
    ProjectState,
)
from app.services.processing import perform_export
from app.services.workspaces import create_project_workspace

DOCUMENT = "<!--\nnarrastep generated document\n-->\n\n# Plan\n\nOpen Settings [00:02].\n"
GENERATED = "05-generated-document/document.md"


@pytest.fixture(autouse=True)
def no_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the Celery dispatch; tests run the export function themselves."""
    from app.tasks.celery_app import export_project_task

    monkeypatch.setattr(export_project_task, "apply_async", lambda **_: None)


def reviewed_project(session: Session, name: str = "Plan") -> Project:
    """Create a project in review with a source file and an empty raw transcript."""
    project = Project(name=name, state=ProjectState.REVIEW, output_mode=OutputMode.TUTORIAL)
    session.add(project)
    session.flush()
    root = get_settings().data_root
    workspace = create_project_workspace(root, project.id)
    source = workspace / "source" / "source.mp4"
    source.write_bytes(b"generated test video")
    (workspace / "transcript" / "raw-transcript.json").write_text(
        '{"segments": []}', encoding="utf-8"
    )
    session.add(
        MediaAsset(
            project_id=project.id,
            display_filename="source.mp4",
            stored_filename="source.mp4",
            relative_path=source.relative_to(root).as_posix(),
            content_type="video/mp4",
            size_bytes=20,
            duration_seconds=1,
            width=16,
            height=9,
            container_format="mp4",
            metadata_json={},
        )
    )
    session.commit()
    return project


def add_generation(
    session: Session,
    project: Project,
    *,
    status: JobStatus = JobStatus.COMPLETED,
    with_file: bool = True,
) -> Generation:
    """Store a generation row and, unless told not to, its `document.md` in the workspace."""
    generation = Generation(
        project_id=project.id,
        model_id="gemma-4-e2b",
        status=status,
        prompt_version="generation-prompts-v1",
        language="en",
        completed_at=datetime(2026, 10, 31, 9, 0, tzinfo=UTC),
    )
    session.add(generation)
    session.flush()
    if with_file:
        root = get_settings().data_root
        document = (
            create_project_workspace(root, project.id)
            / "generations"
            / generation.id
            / "document.md"
        )
        document.parent.mkdir(parents=True)
        document.write_text(DOCUMENT, encoding="utf-8", newline="\n")
        generation.relative_path = document.relative_to(root).as_posix()
    session.commit()
    return generation


def run_export(client: TestClient, project: Project, body: dict[str, object]) -> dict[str, object]:
    """Queue an export through the API, run the worker function and return the API record."""
    response = client.post(f"/api/v1/projects/{project.id}/exports", json=body)
    assert response.status_code == 202, response.text
    export = response.json()
    perform_export(export["id"], get_settings())
    return export


def stored_zip(session: Session, export_id: str) -> Path:
    """Return the ZIP path of a completed export."""
    session.expire_all()
    stored = session.get(ProjectExport, export_id)
    assert stored is not None and stored.status == ExportStatus.COMPLETED, stored.error_code
    assert stored.relative_path is not None
    return get_settings().data_root / stored.relative_path


def test_chosen_generation_is_packed_and_described_in_the_manifest(
    client: TestClient, session: Session
) -> None:
    """Copy `document.md` byte for byte, list it in the manifest and in the archive README."""
    project = reviewed_project(session)
    generation = add_generation(session, project)

    export = run_export(client, project, {"includeGenerationId": generation.id})

    assert export["includeGenerationId"] == generation.id
    with open_package(stored_zip(session, export["id"])) as archive:  # type: ignore[arg-type]
        assert GENERATED in archive.namelist()
        assert archive.read(GENERATED).decode("utf-8") == DOCUMENT
        manifest = json.loads(archive.read("metadata/manifest.json"))
        assert manifest["schemaVersion"] == "0.3.0"
        assert manifest["paths"]["generatedDocument"] == GENERATED
        assert manifest["includedGeneration"] == {
            "generationId": generation.id,
            "modelId": "gemma-4-e2b",
            "promptVersion": "generation-prompts-v1",
            "language": "en",
            "generatedAt": "2026-10-31T09:00:00Z",
            "path": GENERATED,
        }
        assert "05-generated-document/" in archive.read("README.md").decode("utf-8")


def test_export_without_a_choice_has_no_document_and_null_manifest_entries(
    client: TestClient, session: Session
) -> None:
    """Leave the folder out when no generation is chosen, even if the project has one."""
    project = reviewed_project(session)
    add_generation(session, project)

    export = run_export(client, project, {})

    assert export["includeGenerationId"] is None
    with open_package(stored_zip(session, export["id"])) as archive:  # type: ignore[arg-type]
        assert not any(name.startswith("05-generated-document") for name in archive.namelist())
        manifest = json.loads(archive.read("metadata/manifest.json"))
        assert manifest["paths"]["generatedDocument"] is None
        assert manifest["includedGeneration"] is None
        assert "05-generated-document" not in archive.read("README.md").decode("utf-8")


def test_a_later_export_with_a_generation_leaves_older_exports_unchanged(
    client: TestClient, session: Session
) -> None:
    """Keep an earlier export's bytes and history record when the next one adds a document."""
    project = reviewed_project(session)
    first = run_export(client, project, {})
    first_zip = stored_zip(session, first["id"])  # type: ignore[arg-type]
    before = first_zip.read_bytes()

    generation = add_generation(session, project)
    second = run_export(client, project, {"includeGenerationId": generation.id})

    assert first_zip.read_bytes() == before
    with open_package(first_zip) as archive:
        assert GENERATED not in archive.namelist()
    with open_package(stored_zip(session, second["id"])) as archive:  # type: ignore[arg-type]
        assert GENERATED in archive.namelist()
    history = client.get(f"/api/v1/projects/{project.id}/exports").json()["items"]
    included = {item["id"]: item["includeGenerationId"] for item in history}
    assert included == {first["id"]: None, second["id"]: generation.id}


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("missing", 404, "GENERATION_NOT_FOUND"),
        ("other_project", 404, "GENERATION_NOT_FOUND"),
        ("processing", 409, "GENERATION_NOT_READY"),
        ("failed", 409, "GENERATION_NOT_READY"),
        ("file_gone", 404, "PROJECT_ASSET_MISSING"),
    ],
)
def test_a_generation_that_cannot_be_included_is_refused_before_anything_is_stored(
    client: TestClient, session: Session, case: str, status: int, code: str
) -> None:
    """Refuse an unknown, foreign, unfinished or file-less generation with no export row."""
    project = reviewed_project(session)
    other = reviewed_project(session, name="Other")
    generation_id = "00000000-0000-0000-0000-000000000000"
    if case == "other_project":
        generation_id = add_generation(session, other).id
    elif case == "processing":
        generation_id = add_generation(session, project, status=JobStatus.RUNNING).id
    elif case == "failed":
        generation_id = add_generation(session, project, status=JobStatus.FAILED).id
    elif case == "file_gone":
        generation_id = add_generation(session, project, with_file=False).id
        stored = session.get(Generation, generation_id)
        assert stored is not None
        stored.relative_path = f"projects/{project.id}/generations/{generation_id}/document.md"
        session.commit()

    response = client.post(
        f"/api/v1/projects/{project.id}/exports", json={"includeGenerationId": generation_id}
    )

    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(ProjectExport)) == 0
    stored_project = session.get(Project, project.id)
    assert stored_project is not None and stored_project.state == ProjectState.REVIEW


def test_a_document_that_disappears_before_the_worker_runs_fails_the_export_without_a_zip(
    client: TestClient, session: Session
) -> None:
    """Fail with PROJECT_ASSET_MISSING instead of silently building a package without it."""
    project = reviewed_project(session)
    generation = add_generation(session, project)
    response = client.post(
        f"/api/v1/projects/{project.id}/exports", json={"includeGenerationId": generation.id}
    )
    assert response.status_code == 202
    assert generation.relative_path is not None
    (get_settings().data_root / generation.relative_path).unlink()

    perform_export(response.json()["id"], get_settings())

    session.expire_all()
    failed = session.get(ProjectExport, response.json()["id"])
    assert failed is not None and failed.status == ExportStatus.FAILED
    assert failed.error_code == "PROJECT_ASSET_MISSING"
    assert failed.relative_path is None
    exports_folder = create_project_workspace(get_settings().data_root, project.id) / "exports"
    assert list(exports_folder.glob("*.zip*")) == []
    stored_project = session.get(Project, project.id)
    assert stored_project is not None and stored_project.state == ProjectState.REVIEW
