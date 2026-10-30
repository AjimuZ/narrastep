"""Verify model download, range resume, SHA-256 checks, cancel and the kill switch (P06.2b).

Every download runs against a local HTTP server that stands in for the pinned repository, with
small generated files; nothing is requested from the Internet.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import time
from collections import namedtuple
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.errors import ApiError
from app.main import app
from app.models import GenerationModel, ModelInstallState
from app.services import model_download
from app.services.model_catalog import (
    HUGGING_FACE_BASE_URL,
    MODEL_CATALOG,
    MODEL_LICENSE_NAME,
    MODEL_LICENSE_URL,
    ModelFile,
    ModelOption,
)
from app.services.model_download import (
    PARTIAL_SUFFIX,
    begin_download,
    bytes_on_disk,
    model_directory,
    resume_interrupted_downloads,
    stop_download_workers,
    wait_for_download,
)
from app.services.model_manager import read_model_state, transition_model

State = ModelInstallState
MODEL_ID = "gemma-4-e2b"
REVISION = "b" * 40
WEIGHTS_NAME = "tiny-weights.gguf"
PROJECTOR_NAME = "tiny-mmproj.gguf"
WEIGHTS = bytes(range(256)) * 200
PROJECTOR = bytes(reversed(range(256))) * 80
RESOLVE_PREFIX = f"/test/repo/resolve/{REVISION}"


def sha256_of(content: bytes) -> str:
    """Return the hex SHA-256 of generated content."""
    return hashlib.sha256(content).hexdigest()


def tiny_option() -> ModelOption:
    """Return a catalog entry with two small generated files in place of the real model."""
    return ModelOption(
        model_id=MODEL_ID,
        label="Light",
        repository="test/repo",
        revision=REVISION,
        files=(
            ModelFile(WEIGHTS_NAME, len(WEIGHTS), sha256_of(WEIGHTS)),
            ModelFile(PROJECTOR_NAME, len(PROJECTOR), sha256_of(PROJECTOR)),
        ),
    )


class FakeHub:
    """Serve generated files over HTTP with range requests, redirects and scripted failures."""

    def __init__(self) -> None:
        """Start a server on a free local port and publish the two tiny files."""
        self.files: dict[str, bytes] = {
            f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}": WEIGHTS,
            f"{RESOLVE_PREFIX}/{PROJECTOR_NAME}": PROJECTOR,
        }
        self.requests: list[tuple[str, str | None]] = []
        self.redirects: dict[str, str] = {}
        self.statuses: dict[str, int] = {}
        self.truncate_after: dict[str, int] = {}
        self.hold_after: dict[str, int] = {}
        self.ignore_range = False
        self.partial_sent = threading.Event()
        self.release = threading.Event()
        hub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                """Keep the test output quiet."""

            def do_GET(self) -> None:
                """Answer one request from the hub's scripted behavior."""
                try:
                    hub.answer(self)
                except ConnectionError:
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, handler: BaseHTTPRequestHandler) -> None:
        """Write the response for one GET, honoring `Range` unless `ignore_range` is set."""
        path = handler.path
        range_header = handler.headers.get("Range")
        self.requests.append((path, range_header))
        if path in self.redirects:
            handler.send_response(302)
            handler.send_header("Location", self.redirects[path])
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        name = path.rsplit("/", 1)[-1]
        if name in self.statuses:
            handler.send_response(self.statuses[name])
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        body = self.files.get(path)
        if body is None:
            handler.send_response(404)
            handler.send_header("Content-Length", "0")
            handler.end_headers()
            return
        start, status = 0, 200
        if range_header and not self.ignore_range:
            start, status = int(range_header.removeprefix("bytes=").rstrip("-")), 206
        payload = body[start:]
        handler.send_response(status)
        handler.send_header("Content-Length", str(len(payload)))
        if status == 206:
            handler.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
        handler.end_headers()
        cut = self.truncate_after.pop(name, None)
        hold = self.hold_after.pop(name, None)
        if cut is None and hold is None:
            handler.wfile.write(payload)
            return
        first = cut if cut is not None else hold
        handler.wfile.write(payload[:first])
        handler.wfile.flush()
        if hold is not None:
            self.partial_sent.set()
            self.release.wait(10)
            handler.wfile.write(payload[first:])

    def close(self) -> None:
        """Unblock any held response and stop the server."""
        self.release.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Generator[FakeHub]:
    """Point the catalog and the download address at the local server for one test."""
    server = FakeHub()
    monkeypatch.setitem(MODEL_CATALOG, MODEL_ID, tiny_option())
    monkeypatch.setattr(model_download, "DOWNLOAD_BASE_URL", server.url)
    yield server
    server.release.set()
    stop_download_workers()
    server.close()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings whose model folder lives in a temporary data root."""
    return Settings(data_root=tmp_path)


@pytest.fixture
def api_settings() -> Settings:
    """Return the application's own settings, whose data root the API endpoints use."""
    return get_settings()


def start(session: Session, settings: Settings) -> None:
    """Begin the tiny model's download and wait for the background worker to end."""
    begin_download(session, settings, MODEL_ID)
    assert wait_for_download(MODEL_ID, 15)
    session.expire_all()


def stored(session: Session) -> GenerationModel:
    """Return the tiny model's stored row, freshly read."""
    session.expire_all()
    row = session.get(GenerationModel, MODEL_ID)
    assert row is not None
    return row


def poll_state(client: TestClient, wanted: set[str]) -> dict[str, Any]:
    """Poll the progress endpoint until the model reaches one of `wanted` states."""
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        body: dict[str, Any] = client.get(f"/api/v1/models/{MODEL_ID}").json()
        if body["state"] in wanted:
            return body
        time.sleep(0.05)
    raise AssertionError(f"The model never reached {wanted}.")


def leave_interrupted_download(session: Session, settings: Settings, kept: int) -> Path:
    """Leave what a killed API process leaves: a `downloading` row and a partial first file."""
    transition_model(session, settings, MODEL_ID, State.DOWNLOADING, downloaded_bytes=kept)
    directory = model_directory(settings, MODEL_ID)
    directory.mkdir(parents=True)
    partial = directory / (WEIGHTS_NAME + PARTIAL_SUFFIX)
    partial.write_bytes(WEIGHTS[:kept])
    return directory


def test_download_verifies_installs_and_keeps_the_license_beside_the_files(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Fetch both files, verify them, and store the license text with them."""
    start(session, settings)

    row = stored(session)
    assert (row.state, row.error_message) == (State.INSTALLED, None)
    assert row.downloaded_bytes == row.total_bytes == len(WEIGHTS) + len(PROJECTOR)
    directory = model_directory(settings, MODEL_ID)
    assert (directory / WEIGHTS_NAME).read_bytes() == WEIGHTS
    assert (directory / PROJECTOR_NAME).read_bytes() == PROJECTOR
    assert not list(directory.glob(f"*{PARTIAL_SUFFIX}"))
    license_text = (directory / "LICENSE.txt").read_text(encoding="utf-8")
    assert "Apache License" in license_text and "Version 2.0" in license_text
    assert all(path.startswith(f"{RESOLVE_PREFIX}/") for path, _ in hub.requests)
    assert all(range_header is None for _, range_header in hub.requests)


def test_a_restart_resumes_the_partial_file_with_a_range_request(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Continue a download left `downloading` by a stopped API from the bytes already on disk."""
    leave_interrupted_download(session, settings, kept=1234)
    assert read_model_state(session, MODEL_ID) is State.DOWNLOADING

    resume_interrupted_downloads(settings)
    assert wait_for_download(MODEL_ID, 15)

    row = stored(session)
    assert row.state is State.INSTALLED
    assert (model_directory(settings, MODEL_ID) / WEIGHTS_NAME).read_bytes() == WEIGHTS
    assert hub.requests[0] == (f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}", "bytes=1234-")
    assert hub.requests[1] == (f"{RESOLVE_PREFIX}/{PROJECTOR_NAME}", None)


def test_a_real_stop_keeps_progress_and_the_next_start_continues_it(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Stop a running download as a shutdown does, then resume it with a range request."""
    hub.hold_after[WEIGHTS_NAME] = 3000
    begin_download(session, settings, MODEL_ID)
    assert hub.partial_sent.wait(10)
    partial = model_directory(settings, MODEL_ID) / (WEIGHTS_NAME + PARTIAL_SUFFIX)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if partial.is_file() and partial.stat().st_size >= 3000:
            break
        time.sleep(0.02)
    threading.Timer(0.3, hub.release.set).start()
    stop_download_workers(timeout=10)

    row = stored(session)
    assert row.state is State.DOWNLOADING
    assert row.downloaded_bytes == partial.stat().st_size == 3000

    resume_interrupted_downloads(settings)
    assert wait_for_download(MODEL_ID, 15)

    assert stored(session).state is State.INSTALLED
    weights_requests = [request for request in hub.requests if request[0].endswith(WEIGHTS_NAME)]
    assert weights_requests[-1][1] == "bytes=3000-"
    assert (model_directory(settings, MODEL_ID) / WEIGHTS_NAME).read_bytes() == WEIGHTS


def test_the_application_restart_resumes_through_the_lifespan(
    session: Session, api_settings: Settings, hub: FakeHub
) -> None:
    """Resume at API startup, with the progress that SQLite kept across the restart."""
    leave_interrupted_download(session, api_settings, kept=500)
    assert stored(session).downloaded_bytes == 500

    with TestClient(app) as client:
        assert wait_for_download(MODEL_ID, 15)
        body = poll_state(client, {"installed", "failed"})

    assert body["state"] == "installed"
    assert body["downloadedBytes"] == body["totalBytes"]
    assert hub.requests[0][1] == "bytes=500-"


def test_an_interrupted_connection_fails_readably_and_a_retry_resumes(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Keep the partial file when the connection drops, and continue from it on retry."""
    hub.truncate_after[WEIGHTS_NAME] = 4000
    start(session, settings)

    row = stored(session)
    assert row.state is State.FAILED
    assert row.error_message is not None and "interrupted" in row.error_message
    assert row.downloaded_bytes == 4000
    partial = model_directory(settings, MODEL_ID) / (WEIGHTS_NAME + PARTIAL_SUFFIX)
    assert partial.stat().st_size == 4000

    start(session, settings)

    assert stored(session).state is State.INSTALLED
    assert hub.requests[1] == (f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}", "bytes=4000-")


def test_a_server_that_ignores_the_range_restarts_the_file(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Write the whole file again when the server answers a range request with a plain 200."""
    leave_interrupted_download(session, settings, kept=700)
    hub.ignore_range = True

    resume_interrupted_downloads(settings)
    assert wait_for_download(MODEL_ID, 15)

    assert stored(session).state is State.INSTALLED
    assert (model_directory(settings, MODEL_ID) / WEIGHTS_NAME).read_bytes() == WEIGHTS


def test_a_wrong_sha256_deletes_the_file_and_fails_with_a_readable_message(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Delete a file whose content differs from the pinned hash and say which one."""
    corrupted = bytearray(WEIGHTS)
    corrupted[10] ^= 0xFF
    hub.files[f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}"] = bytes(corrupted)
    start(session, settings)

    row = stored(session)
    assert row.state is State.FAILED
    assert row.error_message is not None
    assert WEIGHTS_NAME in row.error_message and "SHA-256" in row.error_message
    directory = model_directory(settings, MODEL_ID)
    assert not (directory / WEIGHTS_NAME).exists()
    assert not (directory / (WEIGHTS_NAME + PARTIAL_SUFFIX)).exists()

    hub.files[f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}"] = WEIGHTS
    start(session, settings)

    assert stored(session).state is State.INSTALLED
    assert (directory / WEIGHTS_NAME).read_bytes() == WEIGHTS


def test_a_body_larger_than_the_pinned_size_is_removed(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Refuse and delete a file that grows past its pinned size."""
    hub.files[f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}"] = WEIGHTS + b"extra"
    start(session, settings)

    row = stored(session)
    assert row.state is State.FAILED
    assert row.error_message is not None and "larger than expected" in row.error_message
    assert bytes_on_disk(settings, tiny_option()) == 0


def test_an_http_error_fails_with_the_status(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Report an HTTP error status of the repository without a stack trace or URL."""
    hub.statuses[WEIGHTS_NAME] = 503
    start(session, settings)

    row = stored(session)
    assert row.state is State.FAILED
    assert row.error_message == f"Hugging Face answered with HTTP 503 for {WEIGHTS_NAME}."


def test_a_redirect_inside_the_pinned_host_is_followed_with_the_range(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Follow the storage redirect Hugging Face uses and keep the resume offset through it."""
    leave_interrupted_download(session, settings, kept=900)
    hub.redirects[f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}"] = "/storage/weights"
    hub.files["/storage/weights"] = WEIGHTS

    resume_interrupted_downloads(settings)
    assert wait_for_download(MODEL_ID, 15)

    assert stored(session).state is State.INSTALLED
    assert hub.requests[:2] == [
        (f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}", "bytes=900-"),
        ("/storage/weights", "bytes=900-"),
    ]


def test_a_redirect_to_another_host_is_refused_without_a_request(
    session: Session, settings: Settings, hub: FakeHub
) -> None:
    """Never request from a host other than the pinned repository's own."""
    other_host = hub.url.replace("127.0.0.1", "localhost")
    hub.redirects[f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}"] = f"{other_host}/elsewhere"
    hub.files["/elsewhere"] = WEIGHTS
    start(session, settings)

    row = stored(session)
    assert row.state is State.FAILED
    assert row.error_message is not None and "pinned repository" in row.error_message
    assert [path for path, _ in hub.requests] == [f"{RESOLVE_PREFIX}/{WEIGHTS_NAME}"]


def test_only_the_pinned_repository_hosts_are_trusted() -> None:
    """Accept the real hosts and their storage subdomains, nothing else and no downgrade."""
    assert model_download.DOWNLOAD_BASE_URL == HUGGING_FACE_BASE_URL == "https://huggingface.co"
    trusted = model_download._host_is_pinned
    for url in (
        "https://huggingface.co/a",
        "https://cdn-lfs.huggingface.co/a",
        "https://cas-bridge.xethub.hf.co/a",
    ):
        assert trusted(url, HUGGING_FACE_BASE_URL), url
    for url in (
        "http://huggingface.co/a",
        "https://hf.co/a",
        "https://nothf.co/a",
        "https://huggingface.co.example.org/a",
        "https://example.org/a",
    ):
        assert not trusted(url, HUGGING_FACE_BASE_URL), url
    assert trusted("http://127.0.0.1:9/a", "http://127.0.0.1:8")
    assert not trusted("http://cdn.hf.co/a", "http://127.0.0.1:8")


def test_the_kill_switch_refuses_a_download_before_any_request(
    session: Session, hub: FakeHub
) -> None:
    """Answer 403 `LOCAL_GENERATION_DISABLED` and touch neither the network nor the disk."""
    disabled = Settings(data_root=get_settings().data_root, local_generation="disabled")

    with pytest.raises(ApiError) as raised:
        begin_download(session, disabled, MODEL_ID)

    assert (raised.value.code, raised.value.status_code) == ("LOCAL_GENERATION_DISABLED", 403)
    assert hub.requests == []
    assert session.get(GenerationModel, MODEL_ID) is None
    assert not model_directory(disabled, MODEL_ID).exists()


def test_a_restart_with_the_kill_switch_fails_the_download_instead_of_resuming(
    session: Session, hub: FakeHub, tmp_path: Path
) -> None:
    """Do not resume while disabled: the model fails with a message and keeps its partial file."""
    directory = leave_interrupted_download(session, Settings(data_root=tmp_path), kept=100)

    resume_interrupted_downloads(Settings(data_root=tmp_path, local_generation="disabled"))

    row = stored(session)
    assert row.state is State.FAILED
    assert row.error_message is not None and "disabled" in row.error_message
    assert hub.requests == []
    assert (directory / (WEIGHTS_NAME + PARTIAL_SUFFIX)).exists()


def test_not_enough_disk_space_refuses_the_download(
    session: Session, settings: Settings, hub: FakeHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Answer 507 `INSUFFICIENT_DISK_SPACE` when the model would cross the reserve."""
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda _path: usage(10, 10, 0))
    reserved = settings.model_copy(update={"disk_reserve_mb": 1})

    with pytest.raises(ApiError) as raised:
        begin_download(session, reserved, MODEL_ID)

    assert (raised.value.code, raised.value.status_code) == ("INSUFFICIENT_DISK_SPACE", 507)
    assert session.get(GenerationModel, MODEL_ID) is None
    assert hub.requests == []


def test_api_starts_a_download_and_reports_progress_and_license(
    client: TestClient, api_settings: Settings, hub: FakeHub
) -> None:
    """Start through the endpoint, poll it to `installed`, and read license data with the state."""
    listing = client.get("/api/v1/models").json()
    assert listing["localGeneration"] == "available"
    assert [item["state"] for item in listing["items"]] == ["not_installed", "not_installed"]

    response = client.post(f"/api/v1/models/{MODEL_ID}/download")

    assert response.status_code == 202
    body = response.json()
    assert body["id"] == MODEL_ID
    assert body["state"] in {"downloading", "installed"}
    assert body["licenseName"] == MODEL_LICENSE_NAME
    assert body["licenseUrl"] == MODEL_LICENSE_URL
    assert body["files"] == [
        {"name": WEIGHTS_NAME, "sizeBytes": len(WEIGHTS)},
        {"name": PROJECTOR_NAME, "sizeBytes": len(PROJECTOR)},
    ]
    done = poll_state(client, {"installed", "failed"})
    assert done["state"] == "installed"
    assert done["downloadedBytes"] == done["totalBytes"] == len(WEIGHTS) + len(PROJECTOR)
    assert done["errorMessage"] is None
    assert (model_directory(api_settings, MODEL_ID) / "LICENSE.txt").is_file()

    again = client.post(f"/api/v1/models/{MODEL_ID}/download")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "MODEL_STATE_CONFLICT"


def test_api_reports_a_failed_download_with_its_message(client: TestClient, hub: FakeHub) -> None:
    """Show the readable failure of a wrong checksum through the progress endpoint."""
    hub.files[f"{RESOLVE_PREFIX}/{PROJECTOR_NAME}"] = PROJECTOR[::-1]

    assert client.post(f"/api/v1/models/{MODEL_ID}/download").status_code == 202
    body = poll_state(client, {"failed", "installed"})

    assert body["state"] == "failed"
    assert PROJECTOR_NAME in body["errorMessage"] and "SHA-256" in body["errorMessage"]


def test_api_cancel_stops_the_download_and_removes_the_files(
    client: TestClient, api_settings: Settings, hub: FakeHub
) -> None:
    """Cancel a running download: the model returns to `not_installed` with no files left."""
    hub.hold_after[WEIGHTS_NAME] = 2000
    assert client.post(f"/api/v1/models/{MODEL_ID}/download").status_code == 202
    assert hub.partial_sent.wait(10)
    threading.Timer(0.3, hub.release.set).start()

    response = client.post(f"/api/v1/models/{MODEL_ID}/cancel")

    assert response.status_code == 202
    assert response.json()["state"] == "not_installed"
    assert response.json()["downloadedBytes"] == 0
    assert not model_directory(api_settings, MODEL_ID).exists()
    assert client.get(f"/api/v1/models/{MODEL_ID}").json()["state"] == "not_installed"


def test_api_cancel_without_a_worker_cleans_up_a_stale_download(
    client: TestClient, session: Session, api_settings: Settings, hub: FakeHub
) -> None:
    """Cancel a `downloading` row that no thread serves, as after a crash with the switch off."""
    directory = leave_interrupted_download(session, api_settings, kept=50)

    response = client.post(f"/api/v1/models/{MODEL_ID}/cancel")

    assert response.status_code == 202
    assert response.json()["state"] == "not_installed"
    assert not directory.exists()


def test_api_refuses_cancel_when_nothing_is_in_progress(client: TestClient, hub: FakeHub) -> None:
    """Answer 409 `MODEL_STATE_CONFLICT` for a cancel of a model that is not being downloaded."""
    response = client.post(f"/api/v1/models/{MODEL_ID}/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "MODEL_STATE_CONFLICT"


def test_api_answers_404_for_an_unknown_model(client: TestClient, hub: FakeHub) -> None:
    """Answer 404 `MODEL_NOT_FOUND` on every endpoint for an identifier outside the catalog."""
    for method, path in (
        ("get", "/api/v1/models/gemma-4-e9b"),
        ("post", "/api/v1/models/gemma-4-e9b/download"),
        ("post", "/api/v1/models/gemma-4-e9b/cancel"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "MODEL_NOT_FOUND"
    assert hub.requests == []


def test_api_kill_switch_answers_403_and_lists_the_models(client: TestClient, hub: FakeHub) -> None:
    """Refuse the download endpoint when disabled, while the list still explains why."""
    disabled = get_settings().model_copy(update={"local_generation": "disabled"})
    app.dependency_overrides[get_settings] = lambda: disabled
    try:
        refused = client.post(f"/api/v1/models/{MODEL_ID}/download")
        listing = client.get("/api/v1/models")
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "LOCAL_GENERATION_DISABLED"
    assert listing.json()["localGeneration"] == "disabled"
    assert hub.requests == []


def test_the_license_text_is_shipped_with_the_application() -> None:
    """Keep the bundled license that is copied beside the model files."""
    text = model_download.LICENSE_SOURCE.read_text(encoding="utf-8")
    assert "Apache License" in text and MODEL_LICENSE_URL in text


def test_a_partial_file_larger_than_pinned_is_dropped_from_the_resume_count(
    settings: Settings,
) -> None:
    """Count only usable bytes so the stored progress never exceeds the total."""
    directory = model_directory(settings, MODEL_ID)
    directory.mkdir(parents=True)
    (directory / (WEIGHTS_NAME + PARTIAL_SUFFIX)).write_bytes(b"x" * (len(WEIGHTS) + 1))
    (directory / PROJECTOR_NAME).write_bytes(PROJECTOR[:-1])

    assert bytes_on_disk(settings, tiny_option()) == 0
    assert not (directory / (WEIGHTS_NAME + PARTIAL_SUFFIX)).exists()
    assert not (directory / PROJECTOR_NAME).exists()
