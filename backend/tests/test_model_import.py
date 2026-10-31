"""Verify the import of pinned model files from a read-only folder (P06.2d).

Every file is a small generated one that replaces the real model through the catalog; nothing is
downloaded and no real model file is read.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.errors import ApiError
from app.main import app
from app.models import GenerationModel, ModelInstallState
from app.schemas import ModelImportRequest
from app.services.model_catalog import MODEL_CATALOG, MODEL_LICENSE_NAME, ModelFile, ModelOption
from app.services.model_download import (
    DownloadControl,
    model_directory,
    stop_download_workers,
    wait_for_download,
)
from app.services.model_import import begin_import, import_preview, run_import
from app.services.model_manager import read_model_state, transition_model

State = ModelInstallState
MODEL_ID = "gemma-4-e2b"
WEIGHTS_NAME = "tiny-weights.gguf"
PROJECTOR_NAME = "tiny-mmproj.gguf"
WEIGHTS = bytes(range(256)) * 200
PROJECTOR = bytes(reversed(range(256))) * 80
URL = f"/api/v1/models/{MODEL_ID}"
NOT_PINNED = "not a pinned model file"


def sha256_of(content: bytes) -> str:
    """Return the hex SHA-256 of generated content."""
    return hashlib.sha256(content).hexdigest()


@pytest.fixture(autouse=True)
def tiny_catalog(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Replace the Light model with two small generated files for one test."""
    option = ModelOption(
        model_id=MODEL_ID,
        label="Light",
        repository="test/repo",
        revision="b" * 40,
        files=(
            ModelFile(WEIGHTS_NAME, len(WEIGHTS), sha256_of(WEIGHTS)),
            ModelFile(PROJECTOR_NAME, len(PROJECTOR), sha256_of(PROJECTOR)),
        ),
    )
    monkeypatch.setitem(MODEL_CATALOG, MODEL_ID, option)
    yield
    stop_download_workers()


@pytest.fixture
def settings(tmp_path: Path) -> Generator[Settings]:
    """Return settings with a data root and an import folder, used by the endpoints too."""
    (tmp_path / "import").mkdir()
    value = Settings(data_root=tmp_path / "data", model_import_dir=tmp_path / "import")
    app.dependency_overrides[get_settings] = lambda: value
    yield value
    app.dependency_overrides.pop(get_settings, None)


def put_pinned_files(settings: Settings) -> None:
    """Place both generated pinned files in the import folder."""
    (settings.model_import_dir / WEIGHTS_NAME).write_bytes(WEIGHTS)
    (settings.model_import_dir / PROJECTOR_NAME).write_bytes(PROJECTOR)


def snapshot(folder: Path) -> dict[str, tuple[bool, int, int, str]]:
    """Describe every entry of a folder: kind, size, modification time and content hash."""
    result: dict[str, tuple[bool, int, int, str]] = {}
    for entry in sorted(folder.iterdir()):
        info = entry.lstat()
        digest = sha256_of(entry.read_bytes()) if entry.is_file() and not entry.is_symlink() else ""
        result[entry.name] = (entry.is_dir(), info.st_size, info.st_mtime_ns, digest)
    return result


def import_and_wait(session: Session, settings: Settings) -> GenerationModel:
    """Start the import with the license accepted, wait for its worker and return the stored row."""
    begin_import(session, settings, MODEL_ID, license_accepted=True)
    assert wait_for_download(MODEL_ID, 15)
    session.expire_all()
    row = session.get(GenerationModel, MODEL_ID)
    assert row is not None
    return row


def poll_state(client: TestClient, wanted: set[str]) -> dict[str, Any]:
    """Poll the model endpoint until it reports one of `wanted` states."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        body: dict[str, Any] = client.get(URL).json()
        if body["state"] in wanted:
            return body
        time.sleep(0.05)
    raise AssertionError(f"The model never reached {wanted}.")


def test_pinned_files_are_installed_and_the_license_is_kept_beside_them(
    session: Session, settings: Settings
) -> None:
    """Copy both files, verify them like a download, and leave the import folder untouched."""
    put_pinned_files(settings)
    before = snapshot(settings.model_import_dir)

    row = import_and_wait(session, settings)

    assert (row.state, row.error_message) == (State.INSTALLED, None)
    assert row.downloaded_bytes == row.total_bytes == len(WEIGHTS) + len(PROJECTOR)
    directory = model_directory(settings, MODEL_ID)
    assert (directory / WEIGHTS_NAME).read_bytes() == WEIGHTS
    assert (directory / PROJECTOR_NAME).read_bytes() == PROJECTOR
    assert not list(directory.glob("*.part"))
    license_text = (directory / "LICENSE.txt").read_text(encoding="utf-8")
    assert "Apache License" in license_text and "Version 2.0" in license_text
    assert snapshot(settings.model_import_dir) == before


def test_a_file_with_the_pinned_name_and_size_but_another_hash_is_refused(
    session: Session, settings: Settings
) -> None:
    """Fail with "not a pinned model file", remove the copy and leave the source alone."""
    put_pinned_files(settings)
    (settings.model_import_dir / WEIGHTS_NAME).write_bytes(bytes(len(WEIGHTS)))
    before = snapshot(settings.model_import_dir)

    row = import_and_wait(session, settings)

    assert row.state is State.FAILED
    assert row.error_message is not None and NOT_PINNED in row.error_message
    assert WEIGHTS_NAME in row.error_message
    assert str(settings.model_import_dir) not in row.error_message
    assert not (model_directory(settings, MODEL_ID) / WEIGHTS_NAME).exists()
    assert snapshot(settings.model_import_dir) == before


def test_a_file_of_another_size_is_refused_before_anything_is_copied(
    session: Session, settings: Settings
) -> None:
    """Answer 422 `MODEL_IMPORT_NOT_PINNED` and keep the model not installed."""
    put_pinned_files(settings)
    (settings.model_import_dir / PROJECTOR_NAME).write_bytes(PROJECTOR[:-1])

    with pytest.raises(ApiError) as raised:
        begin_import(session, settings, MODEL_ID, license_accepted=True)

    assert (raised.value.code, raised.value.status_code) == ("MODEL_IMPORT_NOT_PINNED", 422)
    assert NOT_PINNED in raised.value.message and PROJECTOR_NAME in raised.value.message
    assert read_model_state(session, MODEL_ID) is State.NOT_INSTALLED
    assert not model_directory(settings, MODEL_ID).exists()


def test_a_file_with_another_name_is_ignored_and_the_import_finds_nothing(
    session: Session, settings: Settings
) -> None:
    """Never accept a file by content under a different name."""
    (settings.model_import_dir / "renamed.gguf").write_bytes(WEIGHTS)
    (settings.model_import_dir / "mmproj-copy.gguf").write_bytes(PROJECTOR)

    preview: dict[str, Any] = import_preview(settings, MODEL_ID)
    assert [item["found"] for item in preview["files"]] == [False, False]
    assert preview["ignored"] == [
        {"name": "mmproj-copy.gguf", "reason": "not_a_pinned_model_file"},
        {"name": "renamed.gguf", "reason": "not_a_pinned_model_file"},
    ]
    with pytest.raises(ApiError) as raised:
        begin_import(session, settings, MODEL_ID, license_accepted=True)
    assert (raised.value.code, raised.value.status_code) == ("MODEL_IMPORT_FILES_MISSING", 404)
    assert read_model_state(session, MODEL_ID) is State.NOT_INSTALLED


def test_a_link_to_a_pinned_file_is_not_followed(session: Session, settings: Settings) -> None:
    """A link could point outside the folder, so it counts as "not a pinned model file"."""
    outside = settings.model_import_dir.parent / "outside"
    outside.mkdir()
    (outside / "real.gguf").write_bytes(WEIGHTS)
    (settings.model_import_dir / WEIGHTS_NAME).symlink_to(outside / "real.gguf")
    (settings.model_import_dir / PROJECTOR_NAME).write_bytes(PROJECTOR)

    with pytest.raises(ApiError) as raised:
        begin_import(session, settings, MODEL_ID, license_accepted=True)

    assert raised.value.code == "MODEL_IMPORT_NOT_PINNED"
    preview: dict[str, Any] = import_preview(settings, MODEL_ID)
    assert preview["files"][0]["found"] is False


def test_a_directory_with_a_pinned_name_is_refused(session: Session, settings: Settings) -> None:
    """Only a regular file can be a pinned model file."""
    (settings.model_import_dir / WEIGHTS_NAME).mkdir()
    (settings.model_import_dir / PROJECTOR_NAME).write_bytes(PROJECTOR)

    with pytest.raises(ApiError) as raised:
        begin_import(session, settings, MODEL_ID, license_accepted=True)

    assert raised.value.code == "MODEL_IMPORT_NOT_PINNED"


def test_a_missing_import_folder_is_reported_and_nothing_is_created(
    session: Session, settings: Settings
) -> None:
    """Report `folderAvailable: false` and refuse the import without creating the folder."""
    settings.model_import_dir.rmdir()

    preview: dict[str, Any] = import_preview(settings, MODEL_ID)
    assert preview["folder_available"] is False
    assert preview["ignored_count"] == 0
    with pytest.raises(ApiError) as raised:
        begin_import(session, settings, MODEL_ID, license_accepted=True)
    assert raised.value.code == "MODEL_IMPORT_FILES_MISSING"
    assert not settings.model_import_dir.exists()


def test_the_license_must_be_accepted_first(session: Session, settings: Settings) -> None:
    """Refuse with 422 `LICENSE_NOT_ACCEPTED` before looking at the folder or the state."""
    put_pinned_files(settings)

    with pytest.raises(ApiError) as raised:
        begin_import(session, settings, MODEL_ID, license_accepted=False)

    assert (raised.value.code, raised.value.status_code) == ("LICENSE_NOT_ACCEPTED", 422)
    assert read_model_state(session, MODEL_ID) is State.NOT_INSTALLED
    assert not model_directory(settings, MODEL_ID).exists()


def test_the_kill_switch_refuses_the_import(session: Session, tmp_path: Path) -> None:
    """Answer 403 before any file is read; the preview stays available."""
    (tmp_path / "import").mkdir()
    disabled = Settings(
        data_root=tmp_path / "data",
        model_import_dir=tmp_path / "import",
        local_generation="disabled",
    )

    with pytest.raises(ApiError) as raised:
        begin_import(session, disabled, MODEL_ID, license_accepted=True)

    assert (raised.value.code, raised.value.status_code) == ("LOCAL_GENERATION_DISABLED", 403)
    assert import_preview(disabled, MODEL_ID)["model_id"] == MODEL_ID


def test_an_installed_model_cannot_be_imported_again(session: Session, settings: Settings) -> None:
    """Answer 409 `MODEL_STATE_CONFLICT` from any state but `not_installed` and `failed`."""
    put_pinned_files(settings)
    assert import_and_wait(session, settings).state is State.INSTALLED

    with pytest.raises(ApiError) as raised:
        begin_import(session, settings, MODEL_ID, license_accepted=True)

    assert (raised.value.code, raised.value.status_code) == ("MODEL_STATE_CONFLICT", 409)


def test_a_failed_import_succeeds_once_the_pinned_file_is_in_the_folder(
    session: Session, settings: Settings
) -> None:
    """Retry from `failed`: the next import replaces the earlier leftovers."""
    put_pinned_files(settings)
    (settings.model_import_dir / PROJECTOR_NAME).write_bytes(bytes(len(PROJECTOR)))
    assert import_and_wait(session, settings).state is State.FAILED

    (settings.model_import_dir / PROJECTOR_NAME).write_bytes(PROJECTOR)
    row = import_and_wait(session, settings)

    assert (row.state, row.error_message) == (State.INSTALLED, None)
    directory = model_directory(settings, MODEL_ID)
    assert (directory / PROJECTOR_NAME).read_bytes() == PROJECTOR


def test_a_cancel_stops_the_copy_and_removes_the_files(
    session: Session, settings: Settings
) -> None:
    """Run the worker with a stop that discards: the model returns to `not_installed`."""
    put_pinned_files(settings)
    transition_model(session, settings, MODEL_ID, State.DOWNLOADING)
    control = DownloadControl()
    control.request_stop(discard=True)

    run_import(settings, MODEL_ID, control)

    assert read_model_state(session, MODEL_ID) is State.NOT_INSTALLED
    assert not model_directory(settings, MODEL_ID).exists()
    assert (settings.model_import_dir / WEIGHTS_NAME).read_bytes() == WEIGHTS


def test_no_request_field_selects_a_path() -> None:
    """The body holds the license confirmation only, and the preview and answers name no path."""
    assert set(ModelImportRequest.model_fields) == {"license_accepted"}


def test_api_previews_the_license_and_imports_the_files(
    client: TestClient, session: Session, settings: Settings
) -> None:
    """Read the license before the import, import through the endpoint and poll to `installed`."""
    put_pinned_files(settings)
    (settings.model_import_dir / "notes.txt").write_text("generated", encoding="utf-8")
    before = snapshot(settings.model_import_dir)

    preview = client.get(f"{URL}/import")
    assert preview.status_code == 200
    body = preview.json()
    assert body["modelId"] == MODEL_ID
    assert body["folderAvailable"] is True
    assert body["files"] == [
        {"name": WEIGHTS_NAME, "sizeBytes": len(WEIGHTS), "found": True},
        {"name": PROJECTOR_NAME, "sizeBytes": len(PROJECTOR), "found": True},
    ]
    assert body["ignored"] == [{"name": "notes.txt", "reason": "not_a_pinned_model_file"}]
    assert body["ignoredCount"] == 1
    assert body["licenseName"] == MODEL_LICENSE_NAME
    assert "Apache License" in body["licenseText"]
    assert str(settings.model_import_dir) not in preview.text

    started = client.post(f"{URL}/import", json={"licenseAccepted": True})
    assert started.status_code == 202
    assert started.json()["state"] in {"downloading", "verifying", "installed"}
    done = poll_state(client, {"installed", "failed"})
    assert done["state"] == "installed"
    assert done["downloadedBytes"] == done["totalBytes"]
    assert snapshot(settings.model_import_dir) == before


def test_api_refuses_an_import_without_the_license_or_with_a_path(
    client: TestClient, settings: Settings
) -> None:
    """Answer 422 for an unaccepted license, a missing body and any extra field such as a path."""
    put_pinned_files(settings)

    refused = client.post(f"{URL}/import", json={"licenseAccepted": False})
    assert (refused.status_code, refused.json()["error"]["code"]) == (422, "LICENSE_NOT_ACCEPTED")
    for payload in (None, {}, {"licenseAccepted": True, "path": "/etc"}):
        response = client.post(f"{URL}/import", json=payload)
        assert (response.status_code, response.json()["error"]["code"]) == (
            422,
            "VALIDATION_ERROR",
        )
    assert client.get(URL).json()["state"] == "not_installed"


def test_api_ignores_a_path_in_the_query_string(client: TestClient, settings: Settings) -> None:
    """Read only the configured folder, which holds nothing here, whatever the query says."""
    elsewhere = settings.model_import_dir.parent / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / WEIGHTS_NAME).write_bytes(WEIGHTS)
    (elsewhere / PROJECTOR_NAME).write_bytes(PROJECTOR)

    response = client.post(
        f"{URL}/import?path={elsewhere}&directory={elsewhere}", json={"licenseAccepted": True}
    )

    assert (response.status_code, response.json()["error"]["code"]) == (
        404,
        "MODEL_IMPORT_FILES_MISSING",
    )


def test_api_answers_404_for_an_unknown_model(client: TestClient, settings: Settings) -> None:
    """Both import endpoints answer `MODEL_NOT_FOUND` for an identifier outside the catalog."""
    assert client.get("/api/v1/models/unknown/import").status_code == 404
    response = client.post("/api/v1/models/unknown/import", json={"licenseAccepted": True})
    assert (response.status_code, response.json()["error"]["code"]) == (404, "MODEL_NOT_FOUND")
