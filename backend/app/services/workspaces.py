"""Safe filesystem operations for project-owned source and derived artifacts."""

from __future__ import annotations

import logging
import os
import re
import shutil
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.errors import ApiError

SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._ -]+")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ProjectOperationReservation:
    """Own a cross-process project lock for one bounded conflicting operation."""

    project_id: str
    operation: str
    token: str
    lock_path: Path
    workspace_path: Path


def acquire_project_operation(
    data_root: Path, project_id: str, operation: str
) -> ProjectOperationReservation:
    """Acquire an atomic filesystem reservation shared by upload and project mutations."""
    workspace = create_project_workspace(data_root, project_id)
    lock_root = ensure_within_root(data_root, ".locks")
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f"{project_id}.lock"
    token = str(uuid.uuid4())
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise ApiError("PROJECT_BUSY", "The project has an active local operation.", 409) from error
    try:
        os.write(descriptor, f"{operation}:{token}".encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return ProjectOperationReservation(project_id, operation, token, lock_path, workspace)


def release_project_operation(reservation: ProjectOperationReservation) -> None:
    """Release a project reservation only when its ownership token still matches."""
    try:
        ownership = reservation.lock_path.read_text(encoding="ascii")
    except FileNotFoundError:
        return
    if ownership == f"{reservation.operation}:{reservation.token}":
        reservation.lock_path.unlink(missing_ok=True)


def sanitize_display_filename(filename: str | None) -> str:
    """Normalize an untrusted upload name for display without retaining path syntax."""
    candidate = Path((filename or "video").replace("\\", "/")).name
    normalized = unicodedata.normalize("NFKD", candidate).encode("ascii", "ignore").decode("ascii")
    cleaned = SAFE_FILENAME_PATTERN.sub("_", normalized).strip(" .")
    return (cleaned or "video")[:255]


def ensure_within_root(root: Path, relative_path: str | Path) -> Path:
    """Resolve a relative asset path and reject traversal outside the configured root."""
    root_path = root.resolve()
    candidate = (root_path / relative_path).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as error:
        raise ApiError("INVALID_ASSET_PATH", "The asset path is not valid.", 400) from error
    return candidate


def project_relative_path(project_id: str) -> Path:
    """Return the predictable relative workspace path for a UUID project identifier."""
    if not re.fullmatch(r"[0-9a-f-]{36}", project_id):
        raise ApiError("INVALID_PROJECT_ID", "The project identifier is not valid.", 400)
    return Path("projects") / project_id


def create_project_workspace(data_root: Path, project_id: str) -> Path:
    """Create the fixed directory layout used throughout a project's lifecycle."""
    workspace = ensure_within_root(data_root, project_relative_path(project_id))
    for directory in ("source", "audio", "transcript", "screenshots", "exports", "diagnostics"):
        (workspace / directory).mkdir(parents=True, exist_ok=True)
    return workspace


def stage_project_workspace_removal(data_root: Path, project_id: str) -> Path | None:
    """Atomically hide a project workspace before its database record is deleted."""
    workspace = ensure_within_root(data_root, project_relative_path(project_id))
    if not workspace.exists():
        return None
    trash_root = ensure_within_root(data_root, ".trash")
    trash_root.mkdir(parents=True, exist_ok=True)
    staged = trash_root / project_id
    if staged.exists():
        shutil.rmtree(staged)
    workspace.replace(staged)
    return staged


def restore_staged_workspace(data_root: Path, project_id: str, staged: Path | None) -> None:
    """Restore staged files when the matching database deletion cannot commit."""
    if staged is None or not staged.exists():
        return
    workspace = ensure_within_root(data_root, project_relative_path(project_id))
    workspace.parent.mkdir(parents=True, exist_ok=True)
    staged.replace(workspace)


def purge_staged_workspace(staged: Path | None) -> None:
    """Permanently remove a successfully deleted project's staged asset directory."""
    if staged is not None and staged.exists():
        shutil.rmtree(staged)


def reconcile_staged_workspace_trash(data_root: Path, existing_project_ids: set[str]) -> None:
    """Restore pre-commit staging or purge workspaces whose database deletion committed."""
    trash_root = ensure_within_root(data_root, ".trash")
    if not trash_root.is_dir():
        return
    for staged in trash_root.iterdir():
        project_id = staged.name
        if not staged.is_dir() or not re.fullmatch(r"[0-9a-f-]{36}", project_id):
            logger.warning("An unexpected workspace trash entry was left untouched.")
            continue
        try:
            if project_id in existing_project_ids:
                workspace = ensure_within_root(data_root, project_relative_path(project_id))
                if workspace.exists():
                    shutil.rmtree(staged)
                else:
                    workspace.parent.mkdir(parents=True, exist_ok=True)
                    staged.replace(workspace)
            else:
                shutil.rmtree(staged)
        except OSError as error:
            logger.error(
                "Workspace trash reconciliation failed for project %s (%s).",
                project_id,
                type(error).__name__,
            )


def reconcile_project_operation_reservations(
    data_root: Path, existing_project_ids: set[str]
) -> None:
    """Remove abandoned startup locks and request-owned upload partials after a restart."""
    lock_root = ensure_within_root(data_root, ".locks")
    if lock_root.is_dir():
        for lock_path in lock_root.glob("*.lock"):
            if re.fullmatch(r"[0-9a-f-]{36}\.lock", lock_path.name):
                lock_path.unlink(missing_ok=True)
    for project_id in existing_project_ids:
        workspace = ensure_within_root(data_root, project_relative_path(project_id))
        if not workspace.is_dir():
            continue
        try:
            for partial in (workspace / "source").glob(".upload-*.partial*"):
                partial.unlink(missing_ok=True)
        except OSError as error:
            logger.error(
                "Project operation recovery failed for project %s (%s).",
                project_id,
                type(error).__name__,
            )
