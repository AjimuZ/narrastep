"""Install the pinned model files from a read-only import folder, never trusting a user path."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.database import SessionLocal
from app.errors import ApiError
from app.models import ModelInstallState
from app.services.model_catalog import (
    MODEL_CATALOG,
    MODEL_LICENSE_NAME,
    MODEL_LICENSE_URL,
    ModelFile,
    ModelOption,
    get_model_option,
)
from app.services.model_download import (
    CHUNK_BYTES,
    LICENSE_SOURCE,
    PARTIAL_SUFFIX,
    WRITE_FAILED_MESSAGE,
    DownloadControl,
    DownloadStopped,
    ModelDownloadError,
    _download_files,
    _fail,
    _finish_cancel,
    _verify_file,
    bytes_on_disk,
    model_directory,
    require_disk_space,
    start_download_worker,
    write_license_file,
)
from app.services.model_manager import (
    InvalidModelTransition,
    read_model_state,
    require_generation_available,
    require_known_model,
    transition_model,
)

logger = logging.getLogger(__name__)

State = ModelInstallState

IGNORED_LIST_LIMIT = 50
NOT_PINNED_REASON = "not_a_pinned_model_file"

COPY_FAILED_MESSAGE = (
    "A model file could not be copied from the import folder to the data folder. Check the free "
    "disk space and that the files can be read, then import again."
)
UNEXPECTED_MESSAGE = "The import stopped unexpectedly. Start it again to continue."


class ImportFileStatus(Enum):
    """Say what the import folder holds for one pinned file name."""

    FOUND = "found"
    MISSING = "missing"
    NOT_PINNED = "not_pinned"


def not_pinned_message(name: str) -> str:
    """Return the readable refusal for a file whose name matches but whose size or hash does not."""
    return f"The file {name} in the import folder is not a pinned model file and was not imported."


def inspect_import_file(root: Path, file: ModelFile) -> tuple[ImportFileStatus, Path | None]:
    """Look for one pinned file in the import folder by its pinned name, and return its status.

    The path is always `root / file.name` with a name from the catalog, so nothing a user sends
    can select a location. `FOUND` (with the path) needs a regular file that is not a link, lies
    directly in `root` and has the pinned size; any other entry of that name is `NOT_PINNED` and
    no entry at all is `MISSING`. The hash is checked after the copy, not here. Reads only; the
    folder is never modified.
    """
    candidate = root / file.name
    try:
        if candidate.is_symlink():
            return ImportFileStatus.NOT_PINNED, None
        if not candidate.exists():
            return ImportFileStatus.MISSING, None
        if not candidate.is_file() or candidate.resolve().parent != root:
            return ImportFileStatus.NOT_PINNED, None
        if candidate.stat().st_size != file.size_bytes:
            return ImportFileStatus.NOT_PINNED, None
    except OSError:
        return ImportFileStatus.MISSING, None
    return ImportFileStatus.FOUND, candidate


def _pinned_sizes() -> dict[str, int]:
    """Map every pinned file name of the catalog to its pinned size."""
    return {
        file.name: file.size_bytes for option in MODEL_CATALOG.values() for file in option.files
    }


def _ignored_entries(root: Path) -> tuple[list[dict[str, str]], int]:
    """Return up to `IGNORED_LIST_LIMIT` entries of the folder that are not a pinned model file.

    An entry is accepted only when its name and size equal a pinned file of any catalog model.
    The second value counts every ignored entry. An unreadable folder gives nothing.
    """
    pinned = _pinned_sizes()
    ignored: list[str] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                try:
                    accepted = (
                        entry.name in pinned
                        and entry.is_file(follow_symlinks=False)
                        and entry.stat(follow_symlinks=False).st_size == pinned[entry.name]
                    )
                except OSError:
                    accepted = False
                if not accepted:
                    ignored.append(entry.name)
    except OSError:
        return [], 0
    ignored.sort(key=str.casefold)
    items = [{"name": name, "reason": NOT_PINNED_REASON} for name in ignored[:IGNORED_LIST_LIMIT]]
    return items, len(ignored)


def import_preview(settings: Settings, model_id: str) -> dict[str, object]:
    """Describe what an import of `model_id` would use, with the license to show before it.

    Raises 404 `MODEL_NOT_FOUND`. `files` lists each pinned file with whether the folder holds a
    file of that name and size (the SHA-256 is checked at the import itself); `ignored` lists
    other entries as "not a pinned model file". A missing or unreadable folder answers
    `folderAvailable: false` with nothing found. Works under the kill switch and changes nothing.
    """
    option = require_known_model(model_id)
    root = settings.model_import_dir
    try:
        folder_available = root.is_dir()
    except OSError:
        folder_available = False
    ignored, ignored_count = _ignored_entries(root) if folder_available else ([], 0)
    return {
        "model_id": option.model_id,
        "folder_available": folder_available,
        "files": [
            {
                "name": file.name,
                "size_bytes": file.size_bytes,
                "found": folder_available
                and inspect_import_file(root, file)[0] is ImportFileStatus.FOUND,
            }
            for file in option.files
        ],
        "ignored": ignored,
        "ignored_count": ignored_count,
        "license_name": MODEL_LICENSE_NAME,
        "license_url": MODEL_LICENSE_URL,
        "license_text": LICENSE_SOURCE.read_text(encoding="utf-8"),
    }


def _require_importable_files(settings: Settings, option: ModelOption) -> None:
    """Raise when the folder lacks a pinned file or holds one that is not the pinned file.

    `404 MODEL_IMPORT_FILES_MISSING` names the first absent file; `422 MODEL_IMPORT_NOT_PINNED`
    names the first entry with a pinned name but another size, or that is not a regular file.
    """
    root = settings.model_import_dir
    for file in option.files:
        status, _ = inspect_import_file(root, file)
        if status is ImportFileStatus.MISSING:
            raise ApiError(
                "MODEL_IMPORT_FILES_MISSING",
                f"The import folder does not contain {file.name}.",
                404,
            )
        if status is ImportFileStatus.NOT_PINNED:
            raise ApiError("MODEL_IMPORT_NOT_PINNED", not_pinned_message(file.name), 422)


def begin_import(
    session: Session, settings: Settings, model_id: str, *, license_accepted: bool
) -> None:
    """Move a model to `downloading` and start copying its pinned files from the import folder.

    Checks, before any copy and in this order: the model is in the catalog (404
    `MODEL_NOT_FOUND`), the kill switch allows it (403 `LOCAL_GENERATION_DISABLED`), the caller
    saw the license (422 `LICENSE_NOT_ACCEPTED`), the model is `not_installed` or `failed` (409
    `MODEL_STATE_CONFLICT`), every pinned file is present with its pinned size (404
    `MODEL_IMPORT_FILES_MISSING`, 422 `MODEL_IMPORT_NOT_PINNED`) and the disk has room (507
    `INSUFFICIENT_DISK_SPACE`). The copy and the SHA-256 check run in the background and end in
    `installed`, or in `failed` with "not a pinned model file" for a hash mismatch.
    """
    option = require_known_model(model_id)
    require_generation_available(settings)
    if not license_accepted:
        raise ApiError(
            "LICENSE_NOT_ACCEPTED",
            "Read the license before importing the model files.",
            422,
        )
    state = read_model_state(session, model_id)
    if state not in (State.NOT_INSTALLED, State.FAILED):
        raise ApiError(
            "MODEL_STATE_CONFLICT",
            f"The model is {state.value.replace('_', ' ')}, so it cannot be imported now.",
            409,
        )
    _require_importable_files(settings, option)
    require_disk_space(settings, option)
    if state is State.FAILED:
        transition_model(session, settings, model_id, State.NOT_INSTALLED)
    transition_model(
        session,
        settings,
        model_id,
        State.DOWNLOADING,
        downloaded_bytes=bytes_on_disk(settings, option),
    )
    start_download_worker(settings, model_id, run_import)


def _copy_file(
    root: Path,
    file: ModelFile,
    directory: Path,
    control: DownloadControl,
    report: Callable[[int], None],
) -> None:
    """Copy one pinned file into `<name>.part`, then rename it to `<name>`.

    Reads the source only; a source that vanished, changed size or is no longer a regular pinned
    name raises `ModelDownloadError` with the "not a pinned model file" text, and a read or write
    error raises it with `COPY_FAILED_MESSAGE`. The copy always starts from byte 0, because the
    source is local and a partial copy was not hashed. `report` receives the bytes copied.
    """
    final = directory / file.name
    partial = directory / (file.name + PARTIAL_SUFFIX)
    if final.is_file() and final.stat().st_size == file.size_bytes:
        report(file.size_bytes)
        return
    final.unlink(missing_ok=True)
    status, source = inspect_import_file(root, file)
    if source is None:
        if status is ImportFileStatus.MISSING:
            raise ModelDownloadError(f"The import folder does not contain {file.name}.")
        raise ModelDownloadError(not_pinned_message(file.name))
    written = 0
    try:
        with source.open("rb") as reader, partial.open("wb") as writer:
            while chunk := reader.read(CHUNK_BYTES):
                control.check()
                writer.write(chunk)
                written += len(chunk)
                report(written)
    except OSError as error:
        raise ModelDownloadError(COPY_FAILED_MESSAGE) from error
    if written != file.size_bytes:
        partial.unlink(missing_ok=True)
        raise ModelDownloadError(not_pinned_message(file.name))
    partial.replace(final)
    report(file.size_bytes)


def _verify_imported_files(
    settings: Settings, option: ModelOption, control: DownloadControl
) -> None:
    """Hash every copied file; a mismatch deletes the copy and reports "not a pinned model file"."""
    directory = model_directory(settings, option.model_id)
    for file in option.files:
        try:
            _verify_file(file, directory, control)
        except ModelDownloadError as error:
            raise ModelDownloadError(not_pinned_message(file.name)) from error


def run_import(settings: Settings, model_id: str, control: DownloadControl) -> None:
    """Carry a `downloading` or `verifying` model to `installed`, or to `failed` with a reason.

    Requires a catalog model. Copies each pinned file from the import folder, stores the license
    text beside them, then verifies each file's SHA-256 exactly as a download does. A stop with
    `discard` removes the files and resets the model. Never raises: every failure becomes the
    `failed` state with a message that has no path. The import folder is only read.
    """
    option = get_model_option(model_id)
    root = settings.model_import_dir

    def copy_one(
        _option: ModelOption,
        file: ModelFile,
        directory: Path,
        stop: DownloadControl,
        report: Callable[[int], None],
    ) -> None:
        _copy_file(root, file, directory, stop, report)

    try:
        write_license_file(settings, option)
        with SessionLocal() as session:
            state = read_model_state(session, model_id)
        if state is State.DOWNLOADING:
            _download_files(settings, option, control, copy_one)
            with SessionLocal() as session:
                transition_model(session, settings, model_id, State.VERIFYING)
        elif state is not State.VERIFYING:
            return
        _verify_imported_files(settings, option, control)
        with SessionLocal() as session:
            transition_model(session, settings, model_id, State.INSTALLED)
    except DownloadStopped as stopped:
        if stopped.discard:
            _finish_cancel(model_id, settings)
    except ModelDownloadError as error:
        _fail(model_id, settings, str(error))
    except OSError:
        _fail(model_id, settings, WRITE_FAILED_MESSAGE)
    except InvalidModelTransition:
        logger.warning("Model %s changed state during its import.", model_id)
    except Exception as error:
        logger.error("Model import failed for %s (%s).", model_id, type(error).__name__)
        _fail(model_id, settings, UNEXPECTED_MESSAGE)
