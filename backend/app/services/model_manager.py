"""Persisted install state of the local generation models and the transitions between them."""

from __future__ import annotations

from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import Generation, GenerationModel, JobStatus, ModelInstallState, utc_now
from app.services.model_catalog import (
    MODEL_CATALOG,
    MODEL_LICENSE_NAME,
    MODEL_LICENSE_URL,
    ModelOption,
    get_model_option,
)

State = ModelInstallState

# The diagram of docs/LOCAL_GENERATION.md ("States"). Deleting is one step from `installed` or
# `enabled` to `not_installed`; a `failed` model returns to `not_installed` once its leftovers are
# removed. `downloading` to `downloading` is not listed: a restart resumes without a transition.
ALLOWED_TRANSITIONS: Final[dict[ModelInstallState, frozenset[ModelInstallState]]] = {
    State.NOT_INSTALLED: frozenset({State.DOWNLOADING}),
    State.DOWNLOADING: frozenset({State.VERIFYING, State.FAILED}),
    State.VERIFYING: frozenset({State.INSTALLED, State.FAILED}),
    State.INSTALLED: frozenset({State.ENABLED, State.NOT_INSTALLED}),
    State.ENABLED: frozenset({State.INSTALLED, State.NOT_INSTALLED}),
    State.FAILED: frozenset({State.NOT_INSTALLED}),
}

# Moving into one of these states needs `NARRASTEP_LOCAL_GENERATION=available`. Leaving the feature
# (delete, disable, fail) stays possible so the kill switch never traps disk space.
KILL_SWITCH_BLOCKED_TARGETS: Final = frozenset({State.DOWNLOADING, State.ENABLED})

# Generation statuses that still hold the worker or a model: kept here so the model manager does
# not import the job module, which imports the manager.
ACTIVE_GENERATION_STATUSES: Final = (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.CANCELLING)


class InvalidModelTransition(ValueError):
    """Signal a requested state change that the state diagram does not contain."""

    def __init__(self, current: ModelInstallState, target: ModelInstallState) -> None:
        """Name both states; the message carries no path and no transcript text."""
        super().__init__(f"A model cannot change from {current.value} to {target.value}.")
        self.current = current
        self.target = target


def require_known_model(model_id: str) -> ModelOption:
    """Return the pinned option for `model_id` or raise a 404 `ApiError` for an unknown one."""
    if model_id not in MODEL_CATALOG:
        raise ApiError("MODEL_NOT_FOUND", "This generation model is not available.", 404)
    return get_model_option(model_id)


def require_generation_available(settings: Settings) -> None:
    """Raise a 403 `ApiError` when the kill switch `NARRASTEP_LOCAL_GENERATION` is `disabled`."""
    if not settings.local_generation_available:
        raise ApiError("LOCAL_GENERATION_DISABLED", "Local generation is disabled.", 403)


def read_model_state(session: Session, model_id: str) -> ModelInstallState:
    """Return the stored state of `model_id`; a model without a row is `not_installed`."""
    row = session.get(GenerationModel, model_id)
    return row.state if row is not None else State.NOT_INSTALLED


def model_payload(session: Session, option: ModelOption) -> dict[str, object]:
    """Describe one catalog model for the API: stored state, progress, files and license.

    A model without a row reports `not_installed` with no progress. The license name and link
    always come with the state so the interface can show them before the download.
    """
    row = session.get(GenerationModel, option.model_id)
    return {
        "id": option.model_id,
        "label": option.label,
        "state": row.state if row is not None else State.NOT_INSTALLED,
        "downloaded_bytes": row.downloaded_bytes if row is not None else 0,
        "total_bytes": option.total_bytes,
        "files": [{"name": item.name, "size_bytes": item.size_bytes} for item in option.files],
        "repository": option.repository,
        "revision": option.revision,
        "license_name": MODEL_LICENSE_NAME,
        "license_url": MODEL_LICENSE_URL,
        "error_message": row.error_message if row is not None else None,
        "updated_at": row.updated_at if row is not None else None,
    }


def transition_model(
    session: Session,
    settings: Settings,
    model_id: str,
    target: ModelInstallState,
    *,
    downloaded_bytes: int | None = None,
    error_message: str | None = None,
) -> GenerationModel:
    """Move `model_id` to `target`, persist it and return the row.

    Requires a catalog model and a transition of `ALLOWED_TRANSITIONS`. Raises `ApiError` (404) for
    an unknown model, `ApiError` (403) when the kill switch blocks the target, and
    `InvalidModelTransition` for a change outside the diagram; nothing is written in those cases.
    `failed` needs a readable `error_message`; every other target clears it. Entering `downloading`
    pins the repository revision and total size and starts at `downloaded_bytes` (0 by default);
    `verifying` and `installed` or `enabled` report everything as downloaded; `not_installed`
    clears the progress. The caller's session is committed.
    """
    option = require_known_model(model_id)
    row = session.get(GenerationModel, model_id)
    current = row.state if row is not None else State.NOT_INSTALLED
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidModelTransition(current, target)
    if target in KILL_SWITCH_BLOCKED_TARGETS:
        require_generation_available(settings)
    if target is State.FAILED and not (error_message and error_message.strip()):
        raise ValueError("A failed model needs a readable error message.")

    if row is None:
        row = GenerationModel(model_id=model_id, state=current)
        session.add(row)
    row.state = target
    row.error_message = error_message if target is State.FAILED else None
    if target is State.DOWNLOADING:
        row.revision = option.revision
        row.total_bytes = option.total_bytes
        row.downloaded_bytes = downloaded_bytes or 0
    elif target is State.NOT_INSTALLED:
        row.revision = None
        row.total_bytes = 0
        row.downloaded_bytes = 0
    elif target in {State.VERIFYING, State.INSTALLED, State.ENABLED}:
        row.downloaded_bytes = row.total_bytes
    row.updated_at = utc_now()
    session.commit()
    return row


def generation_is_running(session: Session) -> bool:
    """Tell whether a document generation is queued, running or being cancelled.

    Deleting a model asks this first; a queued generation counts because the worker would start
    it on files that are being removed.
    """
    active = session.scalar(
        select(func.count())
        .select_from(Generation)
        .where(Generation.status.in_(ACTIVE_GENERATION_STATUSES))
    )
    return bool(active)


def project_has_active_generation(session: Session, project_id: str) -> bool:
    """Tell whether the project has a generation that is queued, running or being cancelled.

    Deleting the project or its original video asks this first, because the job reads both.
    """
    active = session.scalar(
        select(func.count())
        .select_from(Generation)
        .where(
            Generation.project_id == project_id,
            Generation.status.in_(ACTIVE_GENERATION_STATUSES),
        )
    )
    return bool(active)


def _refuse_state(model_id: str, state: ModelInstallState, action: str) -> ApiError:
    """Build the 409 `MODEL_STATE_CONFLICT` error for an action the current state forbids."""
    return ApiError(
        "MODEL_STATE_CONFLICT",
        f"The model is {state.value.replace('_', ' ')}, so it cannot be {action} now.",
        409,
    )


def enable_model(session: Session, settings: Settings, model_id: str) -> None:
    """Move an `installed` model to `enabled`.

    Raises 404 `MODEL_NOT_FOUND`, 403 `LOCAL_GENERATION_DISABLED` (kill switch) and 409
    `MODEL_STATE_CONFLICT` from any other state.
    """
    require_known_model(model_id)
    require_generation_available(settings)
    state = read_model_state(session, model_id)
    if state is not State.INSTALLED:
        raise _refuse_state(model_id, state, "enabled")
    transition_model(session, settings, model_id, State.ENABLED)


def disable_model(session: Session, settings: Settings, model_id: str) -> None:
    """Move an `enabled` model back to `installed`; allowed under the kill switch.

    Raises 404 `MODEL_NOT_FOUND` and 409 `MODEL_STATE_CONFLICT` from any other state.
    """
    require_known_model(model_id)
    state = read_model_state(session, model_id)
    if state is not State.ENABLED:
        raise _refuse_state(model_id, state, "disabled")
    transition_model(session, settings, model_id, State.INSTALLED)


def record_download_progress(session: Session, model_id: str, downloaded_bytes: int) -> None:
    """Persist the bytes on disk for a `downloading` model so a restart can resume.

    Raises `InvalidModelTransition` when the model is not `downloading`, and
    `ValueError` for a negative number or one above the pinned total. The session is committed.
    """
    row = session.get(GenerationModel, model_id)
    current = row.state if row is not None else State.NOT_INSTALLED
    if row is None or current is not State.DOWNLOADING:
        raise InvalidModelTransition(current, State.DOWNLOADING)
    if downloaded_bytes < 0 or downloaded_bytes > row.total_bytes:
        raise ValueError("Download progress must stay between zero and the pinned total.")
    row.downloaded_bytes = downloaded_bytes
    row.updated_at = utc_now()
    session.commit()
