"""Verify enabling, disabling and deleting an installed generation model (P06.2c)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import GenerationModel, ModelInstallState
from app.services import model_download
from app.services.model_catalog import GEMMA_4_E2B, GEMMA_4_E4B
from app.services.model_download import model_directory
from app.services.model_manager import transition_model

State = ModelInstallState
MODEL = GEMMA_4_E2B.model_id
OTHER = GEMMA_4_E4B.model_id
URL = f"/api/v1/models/{MODEL}"


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    """Return settings in a temporary data root and make the API endpoints use them."""
    value = Settings(data_root=tmp_path)
    app.dependency_overrides[get_settings] = lambda: value
    yield value
    app.dependency_overrides.pop(get_settings, None)


def put_in_state(session: Session, settings: Settings, model_id: str, target: State) -> None:
    """Walk a model through the diagram to `target` and give it a generated file on disk."""
    path = [State.DOWNLOADING, State.VERIFYING, State.INSTALLED, State.ENABLED]
    if target is State.FAILED:
        path = [State.DOWNLOADING]
    elif target is not State.NOT_INSTALLED:
        path = path[: path.index(target) + 1]
    else:
        path = []
    for step in path:
        transition_model(session, settings, model_id, step)
    if target is State.FAILED:
        transition_model(session, settings, model_id, State.FAILED, error_message="Broken.")
    directory = model_directory(settings, model_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "weights.bin").write_bytes(b"generated")


def stored_state(session: Session, model_id: str) -> State:
    """Return the freshly read stored state of a model."""
    session.expire_all()
    row = session.get(GenerationModel, model_id)
    return row.state if row is not None else State.NOT_INSTALLED


def error_code(response: Any) -> str:
    """Return the error code of an API error response."""
    return str(response.json()["error"]["code"])


def test_enable_and_disable_follow_the_diagram(
    client: TestClient, session: Session, settings: Settings
) -> None:
    """Enable moves installed to enabled and disable returns it, keeping the files."""
    put_in_state(session, settings, MODEL, State.INSTALLED)

    enabled = client.post(f"{URL}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["state"] == "enabled"
    assert enabled.json()["id"] == MODEL
    assert enabled.json()["downloadedBytes"] == enabled.json()["totalBytes"]

    disabled = client.post(f"{URL}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["state"] == "installed"
    assert (model_directory(settings, MODEL) / "weights.bin").is_file()


@pytest.mark.parametrize(
    "state",
    [State.NOT_INSTALLED, State.DOWNLOADING, State.VERIFYING, State.FAILED, State.ENABLED],
)
def test_enable_is_refused_unless_installed(
    client: TestClient, session: Session, settings: Settings, state: State
) -> None:
    """Refuse enabling from every state but installed, without changing the state."""
    put_in_state(session, settings, MODEL, state)

    response = client.post(f"{URL}/enable")

    assert response.status_code == 409
    assert error_code(response) == "MODEL_STATE_CONFLICT"
    assert stored_state(session, MODEL) is state


@pytest.mark.parametrize(
    "state",
    [State.NOT_INSTALLED, State.DOWNLOADING, State.VERIFYING, State.FAILED, State.INSTALLED],
)
def test_disable_is_refused_unless_enabled(
    client: TestClient, session: Session, settings: Settings, state: State
) -> None:
    """Refuse disabling from every state but enabled."""
    put_in_state(session, settings, MODEL, state)

    response = client.post(f"{URL}/disable")

    assert response.status_code == 409
    assert error_code(response) == "MODEL_STATE_CONFLICT"
    assert stored_state(session, MODEL) is state


@pytest.mark.parametrize(
    "state",
    [State.NOT_INSTALLED, State.DOWNLOADING, State.VERIFYING, State.FAILED],
)
def test_delete_is_refused_unless_installed_or_enabled(
    client: TestClient, session: Session, settings: Settings, state: State
) -> None:
    """Refuse deleting from the other states and leave the files alone."""
    put_in_state(session, settings, MODEL, state)

    response = client.delete(URL)

    assert response.status_code == 409
    assert error_code(response) == "MODEL_STATE_CONFLICT"
    assert stored_state(session, MODEL) is state
    assert (model_directory(settings, MODEL) / "weights.bin").is_file()


@pytest.mark.parametrize("state", [State.INSTALLED, State.ENABLED])
def test_delete_removes_the_files_and_resets_the_row(
    client: TestClient, session: Session, settings: Settings, state: State
) -> None:
    """Delete only this model's folder and return it to not installed with no progress."""
    put_in_state(session, settings, MODEL, state)
    put_in_state(session, settings, OTHER, State.INSTALLED)

    response = client.delete(URL)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "not_installed"
    assert body["downloadedBytes"] == 0
    assert not model_directory(settings, MODEL).exists()
    assert (model_directory(settings, OTHER) / "weights.bin").is_file()
    assert stored_state(session, OTHER) is State.INSTALLED
    assert client.get(URL).json()["state"] == "not_installed"


def test_delete_is_refused_while_a_generation_runs(
    client: TestClient, session: Session, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Answer 409 `MODEL_IN_USE` and keep the files and the state."""
    put_in_state(session, settings, MODEL, State.ENABLED)
    monkeypatch.setattr(model_download, "generation_is_running", lambda _session: True)

    response = client.delete(URL)

    assert response.status_code == 409
    assert error_code(response) == "MODEL_IN_USE"
    assert stored_state(session, MODEL) is State.ENABLED
    assert (model_directory(settings, MODEL) / "weights.bin").is_file()


def test_kill_switch_refuses_enable_but_allows_disable_and_delete(
    client: TestClient, session: Session, settings: Settings
) -> None:
    """Leaving the feature stays possible when local generation is disabled."""
    put_in_state(session, settings, MODEL, State.INSTALLED)
    put_in_state(session, settings, OTHER, State.ENABLED)
    disabled = settings.model_copy(update={"local_generation": "disabled"})
    app.dependency_overrides[get_settings] = lambda: disabled

    refused = client.post(f"{URL}/enable")
    turned_off = client.post(f"/api/v1/models/{OTHER}/disable")
    deleted = client.delete(URL)

    assert refused.status_code == 403
    assert error_code(refused) == "LOCAL_GENERATION_DISABLED"
    assert turned_off.status_code == 200
    assert deleted.status_code == 200
    assert deleted.json()["state"] == "not_installed"


def test_unknown_model_answers_404(client: TestClient, settings: Settings) -> None:
    """Answer 404 `MODEL_NOT_FOUND` on the three endpoints."""
    for method, path in (
        ("post", "/api/v1/models/gemma-4-e9b/enable"),
        ("post", "/api/v1/models/gemma-4-e9b/disable"),
        ("delete", "/api/v1/models/gemma-4-e9b"),
    ):
        response = getattr(client, method)(path)
        assert response.status_code == 404, path
        assert error_code(response) == "MODEL_NOT_FOUND"
