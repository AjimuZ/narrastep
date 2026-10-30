"""Verify the persisted model states, the allowed transitions and the kill switch (P06.2a)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import GenerationModel, ModelInstallState
from app.services.model_catalog import GEMMA_4_E2B, GEMMA_4_E4B
from app.services.model_manager import (
    ALLOWED_TRANSITIONS,
    InvalidModelTransition,
    read_model_state,
    record_download_progress,
    transition_model,
)

State = ModelInstallState
MODEL = GEMMA_4_E2B.model_id


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Return settings with local generation available."""
    return Settings(data_root=tmp_path)


@pytest.fixture
def disabled_settings(tmp_path: Path) -> Settings:
    """Return settings with the kill switch set."""
    return Settings(data_root=tmp_path, local_generation="disabled")


def install(session: Session, settings: Settings, model_id: str = MODEL) -> None:
    """Walk a model through the download and verification to `installed`."""
    transition_model(session, settings, model_id, State.DOWNLOADING)
    transition_model(session, settings, model_id, State.VERIFYING)
    transition_model(session, settings, model_id, State.INSTALLED)


def test_a_model_without_a_row_is_not_installed(session: Session) -> None:
    """Start with nothing installed, as the stack does after a fresh migration."""
    assert read_model_state(session, MODEL) is State.NOT_INSTALLED
    assert session.query(GenerationModel).count() == 0


def test_the_diagram_has_exactly_the_documented_edges() -> None:
    """Keep the allowed transitions equal to the diagram of docs/LOCAL_GENERATION.md."""
    assert set(ALLOWED_TRANSITIONS) == set(State)
    assert {
        (current, target) for current, targets in ALLOWED_TRANSITIONS.items() for target in targets
    } == {
        (State.NOT_INSTALLED, State.DOWNLOADING),
        (State.DOWNLOADING, State.VERIFYING),
        (State.DOWNLOADING, State.FAILED),
        (State.VERIFYING, State.INSTALLED),
        (State.VERIFYING, State.FAILED),
        (State.INSTALLED, State.ENABLED),
        (State.ENABLED, State.INSTALLED),
        (State.INSTALLED, State.NOT_INSTALLED),
        (State.ENABLED, State.NOT_INSTALLED),
        (State.FAILED, State.NOT_INSTALLED),
    }


def test_full_lifecycle_persists_each_state_and_progress(
    session: Session, settings: Settings
) -> None:
    """Install, enable, disable and delete a model, reading each state back from SQLite."""
    row = transition_model(session, settings, MODEL, State.DOWNLOADING)
    assert (row.state, row.revision, row.total_bytes, row.downloaded_bytes) == (
        State.DOWNLOADING,
        GEMMA_4_E2B.revision,
        GEMMA_4_E2B.total_bytes,
        0,
    )
    record_download_progress(session, MODEL, 1_000_000)
    session.expire_all()
    assert session.get(GenerationModel, MODEL).downloaded_bytes == 1_000_000  # type: ignore[union-attr]

    transition_model(session, settings, MODEL, State.VERIFYING)
    assert read_model_state(session, MODEL) is State.VERIFYING
    row = transition_model(session, settings, MODEL, State.INSTALLED)
    assert row.downloaded_bytes == row.total_bytes == GEMMA_4_E2B.total_bytes
    assert transition_model(session, settings, MODEL, State.ENABLED).state is State.ENABLED
    assert transition_model(session, settings, MODEL, State.INSTALLED).state is State.INSTALLED
    transition_model(session, settings, MODEL, State.ENABLED)

    row = transition_model(session, settings, MODEL, State.NOT_INSTALLED)
    assert (row.state, row.revision, row.total_bytes, row.downloaded_bytes) == (
        State.NOT_INSTALLED,
        None,
        0,
        0,
    )
    assert read_model_state(session, MODEL) is State.NOT_INSTALLED


def test_download_can_resume_from_stored_progress(session: Session, settings: Settings) -> None:
    """Start a retry from a stored byte count and never past the pinned total."""
    transition_model(session, settings, MODEL, State.DOWNLOADING, downloaded_bytes=42)
    assert session.get(GenerationModel, MODEL).downloaded_bytes == 42  # type: ignore[union-attr]
    with pytest.raises(ValueError):
        record_download_progress(session, MODEL, GEMMA_4_E2B.total_bytes + 1)
    with pytest.raises(ValueError):
        record_download_progress(session, MODEL, -1)
    record_download_progress(session, MODEL, GEMMA_4_E2B.total_bytes)
    assert session.get(GenerationModel, MODEL).downloaded_bytes == GEMMA_4_E2B.total_bytes  # type: ignore[union-attr]


@pytest.mark.parametrize("failing_step", [State.DOWNLOADING, State.VERIFYING])
def test_failure_keeps_a_readable_message_until_the_model_is_cleared(
    session: Session, settings: Settings, failing_step: ModelInstallState
) -> None:
    """Fail from downloading or verifying, keep the message, then return to not installed."""
    transition_model(session, settings, MODEL, State.DOWNLOADING)
    if failing_step is State.VERIFYING:
        transition_model(session, settings, MODEL, State.VERIFYING)
    row = transition_model(
        session, settings, MODEL, State.FAILED, error_message="The file checksum did not match."
    )
    assert (row.state, row.error_message) == (State.FAILED, "The file checksum did not match.")

    row = transition_model(session, settings, MODEL, State.NOT_INSTALLED)
    assert (row.state, row.error_message) == (State.NOT_INSTALLED, None)


def test_failure_needs_a_message_and_other_states_clear_it(
    session: Session, settings: Settings
) -> None:
    """Refuse a silent failure and drop a stale message when the state changes."""
    transition_model(session, settings, MODEL, State.DOWNLOADING)
    for message in (None, "", "   "):
        with pytest.raises(ValueError):
            transition_model(session, settings, MODEL, State.FAILED, error_message=message)
    assert read_model_state(session, MODEL) is State.DOWNLOADING
    row = transition_model(session, settings, MODEL, State.VERIFYING, error_message="ignored")
    assert row.error_message is None


@pytest.mark.parametrize(
    ("setup", "target"),
    [
        ([], State.VERIFYING),
        ([], State.INSTALLED),
        ([], State.ENABLED),
        ([], State.FAILED),
        ([], State.NOT_INSTALLED),
        ([State.DOWNLOADING], State.INSTALLED),
        ([State.DOWNLOADING], State.ENABLED),
        ([State.DOWNLOADING], State.NOT_INSTALLED),
        ([State.DOWNLOADING], State.DOWNLOADING),
        ([State.DOWNLOADING, State.VERIFYING], State.ENABLED),
        ([State.DOWNLOADING, State.VERIFYING], State.DOWNLOADING),
        ([State.DOWNLOADING, State.VERIFYING], State.NOT_INSTALLED),
        ([State.DOWNLOADING, State.VERIFYING, State.INSTALLED], State.DOWNLOADING),
        ([State.DOWNLOADING, State.VERIFYING, State.INSTALLED], State.VERIFYING),
        ([State.DOWNLOADING, State.VERIFYING, State.INSTALLED], State.FAILED),
        ([State.DOWNLOADING, State.VERIFYING, State.INSTALLED, State.ENABLED], State.DOWNLOADING),
        ([State.DOWNLOADING, State.VERIFYING, State.INSTALLED, State.ENABLED], State.FAILED),
        ([State.DOWNLOADING, State.FAILED], State.DOWNLOADING),
        ([State.DOWNLOADING, State.FAILED], State.INSTALLED),
    ],
)
def test_invalid_transitions_are_refused_without_changing_the_state(
    session: Session,
    settings: Settings,
    setup: list[ModelInstallState],
    target: ModelInstallState,
) -> None:
    """Reject every change outside the diagram and leave the stored state as it was."""
    for step in setup:
        transition_model(
            session,
            settings,
            MODEL,
            step,
            error_message="failed" if step is State.FAILED else None,
        )
    before = read_model_state(session, MODEL)
    with pytest.raises(InvalidModelTransition) as raised:
        transition_model(session, settings, MODEL, target, error_message="failed")
    assert raised.value.current is before
    assert raised.value.target is target
    assert read_model_state(session, MODEL) is before


def test_models_are_independent_and_unknown_ones_are_refused(
    session: Session, settings: Settings
) -> None:
    """Keep E2B and E4B states apart and answer 404 for a model outside the catalog."""
    install(session, settings, GEMMA_4_E2B.model_id)
    assert read_model_state(session, GEMMA_4_E4B.model_id) is State.NOT_INSTALLED
    transition_model(session, settings, GEMMA_4_E4B.model_id, State.DOWNLOADING)
    row = session.get(GenerationModel, GEMMA_4_E4B.model_id)
    assert row is not None and row.revision == GEMMA_4_E4B.revision
    assert read_model_state(session, GEMMA_4_E2B.model_id) is State.INSTALLED

    with pytest.raises(ApiError) as raised:
        transition_model(session, settings, "gemma-4-e9b", State.DOWNLOADING)
    assert (raised.value.code, raised.value.status_code) == ("MODEL_NOT_FOUND", 404)
    assert session.get(GenerationModel, "gemma-4-e9b") is None


def test_kill_switch_refuses_downloads_and_enabling_but_not_removal(
    session: Session, settings: Settings, disabled_settings: Settings
) -> None:
    """Block a new download and enabling when disabled, yet allow the user to delete a model."""
    with pytest.raises(ApiError) as raised:
        transition_model(session, disabled_settings, MODEL, State.DOWNLOADING)
    assert (raised.value.code, raised.value.status_code) == ("LOCAL_GENERATION_DISABLED", 403)
    assert read_model_state(session, MODEL) is State.NOT_INSTALLED
    assert session.query(GenerationModel).count() == 0

    install(session, settings)
    with pytest.raises(ApiError):
        transition_model(session, disabled_settings, MODEL, State.ENABLED)
    assert read_model_state(session, MODEL) is State.INSTALLED

    transition_model(session, settings, MODEL, State.ENABLED)
    transition_model(session, disabled_settings, MODEL, State.INSTALLED)
    transition_model(session, disabled_settings, MODEL, State.NOT_INSTALLED)
    assert read_model_state(session, MODEL) is State.NOT_INSTALLED
