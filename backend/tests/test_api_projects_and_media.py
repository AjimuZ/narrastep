"""Project CRUD, upload validation, and byte-range API tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models import Project, ProjectState
from app.services.media import MediaMetadata
from app.services.workspaces import acquire_project_operation, release_project_operation


def _create_project(client: TestClient, name: str = "Claims walkthrough") -> dict[str, object]:
    """Create one project through the same public contract used by the browser."""
    response = client.post(
        "/api/v1/projects",
        json={
            "name": name,
            "description": "Review a local workflow.",
            "mode": "task_briefing",
            "sourceLanguage": "auto",
        },
    )
    assert response.status_code == 201
    return response.json()


def test_health_and_documentation_assets_are_local(client: TestClient) -> None:
    """Expose database health and interactive documentation without CDN dependencies."""
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    health_payload = health.json()
    assert health_payload["version"] == "0.2.0"
    assert health_payload["database"] == "ok"
    assert health_payload["status"] in {"ok", "degraded"}
    assert health_payload["redis"] in {"ok", "unavailable"}
    assert health_payload["worker"] in {"ok", "unavailable"}

    documentation = client.get("/api/docs")
    assert documentation.status_code == 200
    assert "/api/assets/swagger-ui/swagger-ui-bundle.js" in documentation.text
    assert "cdn.jsdelivr.net" not in documentation.text
    assert '"validatorUrl": null' in documentation.text
    stylesheet = client.get("/api/assets/swagger-ui/swagger-ui.css")
    assert stylesheet.status_code == 200


def test_project_crud_uses_frontend_contract(client: TestClient) -> None:
    """Expose mode/status/video fields and stable UTC timestamps expected by Angular."""
    project = _create_project(client)
    assert project["mode"] == "task_briefing"
    assert project["status"] == "draft"
    assert project["videoAsset"] is None
    assert project["createdAt"].endswith("Z")

    updated = client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"name": "Updated name", "mode": "tutorial"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Updated name"
    assert updated.json()["mode"] == "tutorial"

    listing = client.get("/api/v1/projects")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == project["id"]

    deleted = client.delete(f"/api/v1/projects/{project['id']}")
    assert deleted.status_code == 204
    missing = client.get(f"/api/v1/projects/{project['id']}")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.parametrize("mode", ["task_briefing", "meeting", "tutorial", "general"])
def test_project_types_are_accepted(client: TestClient, mode: str) -> None:
    """Accept each ADR 0009 project type through the historical `mode` field."""
    response = client.post("/api/v1/projects", json={"name": "Typed", "mode": mode})
    assert response.status_code == 201
    assert response.json()["mode"] == mode


def test_legacy_technical_walkthrough_mode_is_rejected(client: TestClient) -> None:
    """Reject the retired Version 0.2 value instead of silently storing or remapping it."""
    created = client.post(
        "/api/v1/projects", json={"name": "Legacy", "mode": "technical_walkthrough"}
    )
    assert created.status_code == 422
    assert created.json()["error"]["code"] == "VALIDATION_ERROR"

    project = _create_project(client)
    updated = client.patch(
        f"/api/v1/projects/{project['id']}", json={"mode": "technical_walkthrough"}
    )
    assert updated.status_code == 422
    assert updated.json()["error"]["code"] == "VALIDATION_ERROR"


def test_upload_is_validated_once_and_supports_byte_ranges(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stream one source safely, reject replacement, and support native video seeking."""
    project = _create_project(client)
    metadata = MediaMetadata(
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        duration_seconds=12.5,
        size_bytes=16,
        width=1280,
        height=720,
        video_codec="h264",
        audio_codec="aac",
        frame_rate=30.0,
        content_type="video/mp4",
    )
    seen_path: list[Path] = []

    def fake_validate(path: Path, **_: object) -> MediaMetadata:
        """Record the staged path and return deterministic probe metadata."""
        seen_path.append(path)
        assert path.name.endswith(".mp4")
        return metadata

    monkeypatch.setattr("app.routes.api.validate_video", fake_validate)
    video_bytes = b"0123456789abcdef"
    uploaded = client.post(
        f"/api/v1/projects/{project['id']}/video",
        files={"file": ("../demo.mp4", video_bytes, "video/mp4")},
    )
    assert uploaded.status_code == 201
    assert uploaded.json()["originalFilename"] == "demo.mp4"
    assert uploaded.json()["projectId"] == project["id"]
    assert len(seen_path) == 1

    duplicate = client.post(
        f"/api/v1/projects/{project['id']}/video",
        files={"file": ("other.mp4", video_bytes, "video/mp4")},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "SOURCE_VIDEO_ALREADY_EXISTS"

    ranged = client.get(f"/api/v1/projects/{project['id']}/video", headers={"Range": "bytes=2-5"})
    assert ranged.status_code == 206
    assert ranged.content == b"2345"
    assert ranged.headers["content-range"] == "bytes 2-5/16"

    invalid_range = client.get(
        f"/api/v1/projects/{project['id']}/video", headers={"Range": "bytes=99-100"}
    )
    assert invalid_range.status_code == 416
    assert invalid_range.json()["error"]["code"] == "INVALID_BYTE_RANGE"
    assert invalid_range.headers["content-range"] == "bytes */16"


def test_invalid_request_uses_stable_error_envelope(client: TestClient) -> None:
    """Avoid leaking submitted data while returning useful validation locations."""
    response = client.post(
        "/api/v1/projects",
        json={"name": " ", "mode": "unknown", "sourceLanguage": "english"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert "unknown" not in str(body)


def test_completed_metadata_edit_returns_to_review_and_language_stays_evidence_bound(
    client: TestClient, session: Session
) -> None:
    """Return completed metadata edits to review but reject post-transcription language drift."""
    project = Project(name="Completed", state=ProjectState.COMPLETED, source_language="en")
    session.add(project)
    session.commit()

    renamed = client.patch(f"/api/v1/projects/{project.id}", json={"name": "Renamed"})
    assert renamed.status_code == 200
    assert renamed.json()["status"] == "review"

    language = client.patch(f"/api/v1/projects/{project.id}", json={"sourceLanguage": "it"})
    assert language.status_code == 409
    assert language.json()["error"]["code"] == "INVALID_PROJECT_STATE"


@pytest.mark.parametrize("field", ["name", "sourceLanguage", "mode"])
def test_project_update_rejects_explicit_null(client: TestClient, field: str) -> None:
    """Return stable validation errors instead of database failures for required metadata nulls."""
    project = _create_project(client)
    response = client.patch(f"/api/v1/projects/{project['id']}", json={field: None})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_project_operation_lock_blocks_upload_update_and_delete(client: TestClient) -> None:
    """Prevent conflicting project mutations while another process owns an upload reservation."""
    project = _create_project(client)
    reservation = acquire_project_operation(get_settings().data_root, str(project["id"]), "upload")
    try:
        upload = client.post(
            f"/api/v1/projects/{project['id']}/video",
            files={"file": ("demo.mp4", b"video", "video/mp4")},
        )
        updated = client.patch(f"/api/v1/projects/{project['id']}", json={"name": "Blocked"})
        deleted = client.delete(f"/api/v1/projects/{project['id']}")
    finally:
        release_project_operation(reservation)
    assert {upload.status_code, updated.status_code, deleted.status_code} == {409}
    assert upload.json()["error"]["code"] == "PROJECT_BUSY"


def test_oversized_multipart_body_is_rejected_before_media_probe(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply the ASGI body cap before FFprobe and without framework temporary spooling."""
    project = _create_project(client)
    settings = get_settings().model_copy(update={"max_upload_mb": 1})
    app.dependency_overrides[get_settings] = lambda: settings
    probed = False

    def unexpected_probe(*_: object, **__: object) -> None:
        """Fail the test if an oversized request reaches media validation."""
        nonlocal probed
        probed = True

    monkeypatch.setattr("app.routes.api.validate_video", unexpected_probe)
    try:
        response = client.post(
            f"/api/v1/projects/{project['id']}/video",
            content=b"--boundary--\r\n",
            headers={
                "Content-Type": "multipart/form-data; boundary=boundary",
                "Content-Length": str(3 * 1024 * 1024),
            },
        )
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert probed is False


def test_openapi_declares_real_stream_and_upload_contracts(client: TestClient) -> None:
    """Describe multipart input, binary outputs, SSE, and the stable validation envelope."""
    schema = client.get("/api/openapi.json").json()
    upload = schema["paths"]["/api/v1/projects/{project_id}/video"]["post"]
    assert (
        upload["requestBody"]["content"]["multipart/form-data"]["schema"]["properties"]["file"][
            "format"
        ]
        == "binary"
    )
    video = schema["paths"]["/api/v1/projects/{project_id}/video"]["get"]["responses"]
    assert "video/mp4" in video["200"]["content"]
    assert "206" in video and "416" in video
    events = schema["paths"]["/api/v1/jobs/{job_id}/events"]["get"]["responses"]
    assert "text/event-stream" in events["200"]["content"]
    assert "HTTPValidationError" not in schema.get("components", {}).get("schemas", {})
