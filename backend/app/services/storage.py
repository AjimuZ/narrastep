"""Storage accounting per project and the guarded removal of the original recording (P09.2)."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.models import ExportStatus, Project, ProjectExport, ProjectState, utc_now
from app.services.workspaces import ensure_within_root, project_relative_path

logger = logging.getLogger(__name__)

# Workspace folders reported on their own; everything else (diagnostics, scratch files, recording
# chunks) is reported as "other" so the categories always add up to the project total.
CATEGORY_FOLDERS = ("source", "audio", "transcript", "screenshots", "exports")

# Reasons a project's original video cannot be removed now, as exposed by the API.
REASON_NO_VIDEO = "no_video"
REASON_ALREADY_REMOVED = "already_removed"
REASON_BUSY = "busy"
REASON_PACKAGE_REQUIRED = "package_required"


@dataclass(frozen=True, slots=True)
class StorageBreakdown:
    """Bytes a project workspace uses, split by the folders of its fixed layout."""

    source: int = 0
    audio: int = 0
    transcript: int = 0
    screenshots: int = 0
    exports: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        """Return the sum of every category."""
        return (
            self.source
            + self.audio
            + self.transcript
            + self.screenshots
            + self.exports
            + self.other
        )


def directory_bytes(directory: Path) -> int:
    """Return the size of the regular files below `directory`, or 0 when it does not exist.

    Symbolic links are neither followed nor counted, so a link inside a workspace can neither
    inflate the figure nor leak sizes from outside the data root. A file that disappears or
    cannot be read while walking is skipped: the figure is an accounting aid, not evidence.
    """
    total = 0
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def measure_project_storage(data_root: Path, project_id: str) -> StorageBreakdown:
    """Measure one project workspace; a missing workspace measures zero.

    Raises `ApiError` (`INVALID_PROJECT_ID`) for an identifier that is not a UUID.
    """
    workspace = ensure_within_root(data_root, project_relative_path(project_id))
    sizes = dict.fromkeys(CATEGORY_FOLDERS, 0)
    for folder in CATEGORY_FOLDERS:
        sizes[folder] = directory_bytes(workspace / folder)
    known = sum(sizes.values())
    return StorageBreakdown(
        **sizes,
        other=max(0, directory_bytes(workspace) - known),
    )


def source_video_bytes(project: Project, data_root: Path) -> int:
    """Return the size on disk of the project's original video, or 0 when it is removed or gone."""
    asset = project.media_asset
    if asset is None or asset.source_removed_at is not None:
        return 0
    try:
        return ensure_within_root(data_root, asset.relative_path).stat().st_size
    except OSError, ApiError:
        return 0


def has_current_package(session: Session, project: Project, data_root: Path) -> bool:
    """Return whether the project's reviewed work is already in a package that still exists.

    Editing a completed project returns it to review, so `completed` means the newest package
    matches the current review. The package file must also still be on disk: a record alone is
    not a package.
    """
    if project.state != ProjectState.COMPLETED:
        return False
    exports = session.scalars(
        select(ProjectExport)
        .where(
            ProjectExport.project_id == project.id,
            ProjectExport.status == ExportStatus.COMPLETED,
            ProjectExport.relative_path.is_not(None),
        )
        .order_by(desc(ProjectExport.created_at))
    )
    for export in exports:
        try:
            if (
                export.relative_path
                and ensure_within_root(data_root, export.relative_path).is_file()
            ):
                return True
        except ApiError:
            continue
    return False


def removal_blocked_reason(session: Session, project: Project, data_root: Path) -> str | None:
    """Explain why the original video cannot be removed now, or return None when it can.

    The video is removable once, and only when the project is idle and a package of its
    current review exists on disk. Returns one of the `REASON_*` codes otherwise.
    """
    asset = project.media_asset
    if asset is None:
        return REASON_NO_VIDEO
    if asset.source_removed_at is not None:
        return REASON_ALREADY_REMOVED
    if project.state in {ProjectState.QUEUED, ProjectState.PROCESSING, ProjectState.EXPORTING}:
        return REASON_BUSY
    if not has_current_package(session, project, data_root):
        return REASON_PACKAGE_REQUIRED
    return None


def remove_source_files(data_root: Path, project_id: str) -> int:
    """Delete every file in the project's `source/` folder and return the bytes freed.

    The folder holds the original video and, for recorder projects, the kept microphone and
    system-audio tracks; all of them are the original recording. The folder itself stays because
    the workspace layout requires it. Files already missing are ignored, so a retry after a
    partial failure finishes the job. Raises `OSError` when a file cannot be deleted.
    """
    workspace = ensure_within_root(data_root, project_relative_path(project_id))
    source = workspace / "source"
    freed = 0
    if not source.is_dir():
        return freed
    for entry in source.iterdir():
        if entry.is_dir() and not entry.is_symlink():
            freed += directory_bytes(entry)
            shutil.rmtree(entry)
            continue
        size = 0 if entry.is_symlink() else entry.stat().st_size
        entry.unlink(missing_ok=True)
        freed += size
    return freed


def remove_original_video(session: Session, project: Project, data_root: Path) -> int:
    """Remove the original recording of a project that has a current package; return bytes freed.

    The caller holds the project operation reservation. Files are deleted before the record is
    marked, so a crash in between leaves a project that still offers the removal and finishes it
    on retry, never one that claims a removal that did not happen. Raises `ApiError`:
    `SOURCE_VIDEO_REMOVED` (409) when it was already removed, `PROJECT_BUSY` (409) during
    background work, `PACKAGE_REQUIRED` (409) when no current package exists and
    `PROJECT_ASSET_MISSING` (404) when the project has no video.
    """
    reason = removal_blocked_reason(session, project, data_root)
    if reason == REASON_NO_VIDEO:
        raise ApiError("PROJECT_ASSET_MISSING", "The project has no original video.", 404)
    if reason == REASON_ALREADY_REMOVED:
        raise ApiError("SOURCE_VIDEO_REMOVED", "The original video was already removed.", 409)
    if reason == REASON_BUSY:
        raise ApiError("PROJECT_BUSY", "The project has active background work.", 409)
    if reason == REASON_PACKAGE_REQUIRED:
        raise ApiError(
            "PACKAGE_REQUIRED",
            "Create a package of the current review before removing the original video.",
            409,
        )
    asset = project.media_asset
    assert asset is not None  # `removal_blocked_reason` returned None, so the asset exists.
    freed = remove_source_files(data_root, project.id)
    asset.source_removed_at = utc_now()
    session.commit()
    logger.info("Original video removed for project %s (%d bytes).", project.id, freed)
    return freed
