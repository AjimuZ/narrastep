"""Download, resume, verify and cancel the pinned generation model files in `/data/models`."""

from __future__ import annotations

import hashlib
import http.client
import logging
import re
import shutil
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import SessionLocal
from app.errors import ApiError
from app.models import GenerationModel, ModelInstallState
from app.services.model_catalog import (
    HUGGING_FACE_BASE_URL,
    MODEL_CATALOG,
    ModelFile,
    ModelOption,
    get_model_option,
)
from app.services.model_manager import (
    InvalidModelTransition,
    generation_is_running,
    read_model_state,
    record_download_progress,
    require_generation_available,
    require_known_model,
    transition_model,
)

logger = logging.getLogger(__name__)

State = ModelInstallState

MODELS_DIRECTORY_NAME = "models"
LICENSE_FILE_NAME = "LICENSE.txt"
PARTIAL_SUFFIX = ".part"
LICENSE_SOURCE = Path(__file__).resolve().parent.parent / "resources" / "gemma-license.txt"

# The only address the application requests from. Tests replace it with a local server; no setting
# or request can, so a download never leaves the pinned repository (CLAUDE.md rules 11 and 16).
DOWNLOAD_BASE_URL = HUGGING_FACE_BASE_URL
# Hugging Face answers a file address with a redirect to its own storage hosts; these are the only
# hosts a redirect may reach, and only while the base address is the real one.
PINNED_REDIRECT_SUFFIXES = (".huggingface.co", ".hf.co")

CHUNK_BYTES = 1024 * 1024
SOCKET_TIMEOUT_SECONDS = 30
PROGRESS_INTERVAL_SECONDS = 1.0
PROGRESS_INTERVAL_BYTES = 16 * 1024 * 1024
CANCEL_WAIT_SECONDS = 15.0

ACTIVE_STATES = (State.DOWNLOADING, State.VERIFYING)

CANCELLED_MESSAGE = "The download was cancelled."
DISABLED_MESSAGE = "Local generation was disabled, so the download was stopped."
INTERRUPTED_MESSAGE = "The download was interrupted. Start it again to continue where it stopped."
WRITE_FAILED_MESSAGE = (
    "The model file could not be written to the data folder. Check the free disk space and "
    "start the download again."
)
UNEXPECTED_MESSAGE = "The download stopped unexpectedly. Start it again to continue."


class ModelDownloadError(Exception):
    """Signal a failed download or verification whose `str` is safe to show the user.

    The message names a file and the reason, never a path, a URL with credentials or file content.
    """


class DownloadStopped(Exception):
    """Unwind a worker that was asked to stop: `discard` is a cancel, otherwise a process stop."""

    def __init__(self, discard: bool) -> None:
        """Remember whether the model's files are to be removed."""
        super().__init__("The download was stopped.")
        self.discard = discard


class DownloadControl:
    """Let the API ask one running worker to stop between two chunks."""

    def __init__(self) -> None:
        """Start in the running state."""
        self._stop = threading.Event()
        self.discard = False

    def request_stop(self, *, discard: bool) -> None:
        """Ask the worker to stop; `discard` also removes the model's files (a cancel)."""
        self.discard = discard
        self._stop.set()

    def check(self) -> None:
        """Raise `DownloadStopped` once a stop was requested."""
        if self._stop.is_set():
            raise DownloadStopped(self.discard)


class _Worker:
    """Pair a download thread with the control that stops it."""

    def __init__(self, thread: threading.Thread, control: DownloadControl) -> None:
        """Keep both so a later request can stop or wait for the thread."""
        self.thread = thread
        self.control = control


_workers: dict[str, _Worker] = {}
_workers_lock = threading.Lock()


def models_root(settings: Settings) -> Path:
    """Return the folder holding every model, `<data root>/models`."""
    return settings.data_root / MODELS_DIRECTORY_NAME


def model_directory(settings: Settings, model_id: str) -> Path:
    """Return the folder of one catalog model; raises `KeyError` for an identifier outside it."""
    return models_root(settings) / get_model_option(model_id).model_id


def bytes_on_disk(settings: Settings, option: ModelOption) -> int:
    """Count the bytes a download can resume from: complete files plus the partial ones.

    A file of the wrong size, or a partial file larger than the pinned size, is removed and counts
    as nothing, so the result never exceeds `option.total_bytes`.
    """
    directory = model_directory(settings, option.model_id)
    total = 0
    for file in option.files:
        final = directory / file.name
        partial = directory / (file.name + PARTIAL_SUFFIX)
        if final.is_file():
            if final.stat().st_size == file.size_bytes:
                total += file.size_bytes
                continue
            final.unlink()
        if partial.is_file():
            size = partial.stat().st_size
            if size > file.size_bytes:
                partial.unlink()
            else:
                total += size
    return total


def write_license_file(settings: Settings, option: ModelOption) -> None:
    """Keep the license text beside the model files, in `<data root>/models/<model>/LICENSE.txt`."""
    directory = model_directory(settings, option.model_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / LICENSE_FILE_NAME
    text = LICENSE_SOURCE.read_text(encoding="utf-8")
    if not target.is_file() or target.read_text(encoding="utf-8") != text:
        target.write_text(text, encoding="utf-8", newline="\n")


def remove_model_files(settings: Settings, option: ModelOption) -> None:
    """Delete everything stored for the model, including partial files and the license copy."""
    shutil.rmtree(model_directory(settings, option.model_id), ignore_errors=True)


def require_disk_space(settings: Settings, option: ModelOption) -> None:
    """Raise `INSUFFICIENT_DISK_SPACE` (507) when the rest of the download crosses the reserve."""
    models_root(settings).mkdir(parents=True, exist_ok=True)
    remaining = option.total_bytes - bytes_on_disk(settings, option)
    if shutil.disk_usage(models_root(settings)).free - remaining < settings.disk_reserve_bytes:
        raise ApiError(
            "INSUFFICIENT_DISK_SPACE",
            "There is not enough free disk space for the model.",
            507,
        )


def _host_is_pinned(url: str, base_url: str) -> bool:
    """Return True when `url` stays on the base address or, for the real one, its storage hosts."""
    target = urlsplit(url)
    base = urlsplit(base_url)
    if target.scheme != base.scheme or not target.hostname or not base.hostname:
        return False
    if target.hostname == base.hostname:
        return True
    return base_url == HUGGING_FACE_BASE_URL and target.hostname.endswith(PINNED_REDIRECT_SUFFIXES)


class _PinnedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the pinned repository's own hosts."""

    def __init__(self, base_url: str) -> None:
        """Remember the address redirects are compared with."""
        self._base_url = base_url

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        """Refuse a redirect away from the pinned hosts; otherwise keep the `Range` header."""
        if not _host_is_pinned(newurl, self._base_url):
            raise ModelDownloadError(
                "The download was redirected away from the pinned repository and was refused."
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url: str, offset: int, base_url: str) -> http.client.HTTPResponse:
    """Open `url` with a range request from `offset` (none at 0) through the pinned redirects."""
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    opener = urllib.request.build_opener(_PinnedRedirectHandler(base_url))
    request = urllib.request.Request(url, headers=headers, method="GET")
    response: http.client.HTTPResponse = opener.open(request, timeout=SOCKET_TIMEOUT_SECONDS)
    return response


def _range_start(content_range: str | None) -> int | None:
    """Return the first byte of a `Content-Range: bytes a-b/c` header, or None when unreadable."""
    match = re.match(r"bytes (\d+)-\d+/(\d+|\*)$", (content_range or "").strip())
    return int(match.group(1)) if match else None


def _fetch_file(
    option: ModelOption,
    file: ModelFile,
    directory: Path,
    control: DownloadControl,
    report: Callable[[int], None],
) -> None:
    """Fetch one file into `<name>.part`, resuming from its size, then rename it to `<name>`.

    `report` receives the bytes of this file stored so far. A connection that ends early keeps the
    partial file and raises `ModelDownloadError` so the next start resumes; a response longer than
    the pinned size, a server that cannot continue a range or a redirect away from the pinned
    hosts also raise it, after removing what is unusable.
    """
    final = directory / file.name
    partial = directory / (file.name + PARTIAL_SUFFIX)
    if final.is_file() and final.stat().st_size == file.size_bytes:
        report(file.size_bytes)
        return
    final.unlink(missing_ok=True)
    offset = partial.stat().st_size if partial.is_file() else 0
    if offset > file.size_bytes:
        partial.unlink()
        offset = 0
    if offset < file.size_bytes:
        base_url = DOWNLOAD_BASE_URL
        url = option.file_url(file, base_url)
        try:
            response = _open(url, offset, base_url)
        except urllib.error.HTTPError as error:
            if error.code == 416:
                partial.unlink(missing_ok=True)
                raise ModelDownloadError(
                    f"The server did not accept the partial file {file.name}, which was removed. "
                    "Start the download again."
                ) from error
            raise ModelDownloadError(
                f"Hugging Face answered with HTTP {error.code} for {file.name}."
            ) from error
        except (urllib.error.URLError, http.client.HTTPException, OSError) as error:
            raise ModelDownloadError(INTERRUPTED_MESSAGE) from error
        with response:
            written = _stream_response(response, file, partial, offset, control, report)
        if written != file.size_bytes:
            raise ModelDownloadError(INTERRUPTED_MESSAGE)
    partial.replace(final)
    report(file.size_bytes)


def _stream_response(
    response: http.client.HTTPResponse,
    file: ModelFile,
    partial: Path,
    offset: int,
    control: DownloadControl,
    report: Callable[[int], None],
) -> int:
    """Write the response body to `partial` and return the bytes the file holds afterwards.

    Appends after a `206` whose `Content-Range` starts at `offset`; restarts the file after a plain
    `200`. Raises `ModelDownloadError` for another status or range, or when the body would exceed
    the pinned size (the partial file is then removed).
    """
    status = response.status
    if offset and status == 206:
        if _range_start(response.headers.get("Content-Range")) != offset:
            partial.unlink(missing_ok=True)
            raise ModelDownloadError(
                f"The server answered with another part of {file.name}, which was removed. "
                "Start the download again."
            )
        mode, written = "ab", offset
    elif status == 200 or (status == 206 and not offset):
        mode, written = "wb", 0
    else:
        raise ModelDownloadError(f"Hugging Face answered with HTTP {status} for {file.name}.")
    try:
        with partial.open(mode) as handle:
            while chunk := _read_chunk(response):
                control.check()
                written += len(chunk)
                if written > file.size_bytes:
                    break
                handle.write(chunk)
                # Flushed per chunk so the file size always equals the reported progress.
                handle.flush()
                report(written)
    except OSError as error:
        raise ModelDownloadError(WRITE_FAILED_MESSAGE) from error
    if written > file.size_bytes:
        partial.unlink(missing_ok=True)
        raise ModelDownloadError(
            f"The file {file.name} is larger than expected and was removed. "
            "Start the download again."
        )
    return written


def _read_chunk(response: http.client.HTTPResponse) -> bytes:
    """Read the bytes available now, up to a chunk; a connection lost mid-body acts as the end.

    `read1` returns as soon as some bytes arrive, so progress is stored and a stop request is seen
    without waiting for a full chunk.
    """
    try:
        return response.read1(CHUNK_BYTES)
    except http.client.HTTPException, OSError:
        return b""


def _verify_file(file: ModelFile, directory: Path, control: DownloadControl) -> None:
    """Check one stored file against its pinned size and SHA-256; delete it when it differs."""
    path = directory / file.name
    if not path.is_file():
        raise ModelDownloadError(f"The file {file.name} is missing. Start the download again.")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK_BYTES):
            control.check()
            digest.update(chunk)
    if path.stat().st_size != file.size_bytes or digest.hexdigest() != file.sha256:
        path.unlink(missing_ok=True)
        raise ModelDownloadError(
            f"The file {file.name} did not match its published SHA-256 checksum and was "
            "deleted. Start the download again."
        )


def _download_files(settings: Settings, option: ModelOption, control: DownloadControl) -> None:
    """Fetch every pinned file, persisting the byte count about once a second and at each end.

    The count is stored in SQLite so a restart shows real progress; the files on disk decide where
    the next start resumes. The last count is stored even when the transfer is interrupted.
    """
    directory = model_directory(settings, option.model_id)
    completed = 0
    last_saved_at = time.monotonic()
    last_saved_bytes = bytes_on_disk(settings, option)
    current = last_saved_bytes

    with SessionLocal() as session:

        def save() -> None:
            nonlocal last_saved_at, last_saved_bytes
            record_download_progress(session, option.model_id, min(current, option.total_bytes))
            last_saved_at, last_saved_bytes = time.monotonic(), current

        try:
            for file in option.files:

                def report(file_bytes: int, offset_before: int = completed) -> None:
                    nonlocal current
                    current = offset_before + file_bytes
                    if (
                        current - last_saved_bytes >= PROGRESS_INTERVAL_BYTES
                        or time.monotonic() - last_saved_at >= PROGRESS_INTERVAL_SECONDS
                    ):
                        save()

                _fetch_file(option, file, directory, control, report)
                completed += file.size_bytes
                current = completed
                save()
        except BaseException:
            with suppress(Exception):
                save()
            raise


def _verify_files(settings: Settings, option: ModelOption, control: DownloadControl) -> None:
    """Hash every stored file; the first mismatch is deleted and raises `ModelDownloadError`."""
    directory = model_directory(settings, option.model_id)
    for file in option.files:
        _verify_file(file, directory, control)


def _fail(model_id: str, settings: Settings, message: str) -> None:
    """Move an active model to `failed` with `message`; any other state is left alone."""
    with SessionLocal() as session:
        if read_model_state(session, model_id) in ACTIVE_STATES:
            transition_model(session, settings, model_id, State.FAILED, error_message=message)


def _finish_cancel(model_id: str, settings: Settings) -> None:
    """Remove the model's files and return it to `not_installed` through `failed`.

    The state diagram has no direct edge from an active state to `not_installed`; the short
    `failed` step keeps the diagram as documented and records why.
    """
    remove_model_files(settings, get_model_option(model_id))
    with SessionLocal() as session:
        if read_model_state(session, model_id) in ACTIVE_STATES:
            transition_model(
                session, settings, model_id, State.FAILED, error_message=CANCELLED_MESSAGE
            )
            transition_model(session, settings, model_id, State.NOT_INSTALLED)


def begin_download(session: Session, settings: Settings, model_id: str) -> None:
    """Move a model to `downloading` and start its background download.

    Checks, before any network work and in this order: the model is in the catalog (404
    `MODEL_NOT_FOUND`), the kill switch allows it (403 `LOCAL_GENERATION_DISABLED`), the model is
    `not_installed` or `failed` (409 `MODEL_STATE_CONFLICT` otherwise) and the disk has room (507
    `INSUFFICIENT_DISK_SPACE`). A `failed` model keeps its partial files, so the retry resumes
    from them; the progress starts at the bytes already on disk.
    """
    option = require_known_model(model_id)
    require_generation_available(settings)
    state = read_model_state(session, model_id)
    if state not in (State.NOT_INSTALLED, State.FAILED):
        raise ApiError(
            "MODEL_STATE_CONFLICT",
            f"The model is {state.value.replace('_', ' ')}, so it cannot be downloaded now.",
            409,
        )
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
    start_download_worker(settings, model_id)


def request_cancel(session: Session, settings: Settings, model_id: str) -> None:
    """Cancel a `downloading` or `verifying` model: stop it, delete its files, reset its state.

    Raises 404 `MODEL_NOT_FOUND` for an unknown model and 409 `MODEL_STATE_CONFLICT` when nothing
    is in progress. Cancelling works whatever the kill switch says. Returns once the worker has
    cleaned up, or after `CANCEL_WAIT_SECONDS` while it is still finishing a read.
    """
    require_known_model(model_id)
    state = read_model_state(session, model_id)
    if state not in ACTIVE_STATES:
        raise ApiError(
            "MODEL_STATE_CONFLICT",
            f"The model is {state.value.replace('_', ' ')}, so there is no download to cancel.",
            409,
        )
    cancel_download(settings, model_id)
    session.expire_all()


def delete_model(session: Session, settings: Settings, model_id: str) -> None:
    """Delete an `installed` or `enabled` model's files and return it to `not_installed`.

    Raises 404 `MODEL_NOT_FOUND`, 409 `MODEL_STATE_CONFLICT` from any other state, and 409
    `MODEL_IN_USE` while a generation runs. Works under the kill switch. The files go first, then
    the state, so a failed removal never reports a model as gone while its files remain.
    """
    require_known_model(model_id)
    state = read_model_state(session, model_id)
    if state not in (State.INSTALLED, State.ENABLED):
        raise ApiError(
            "MODEL_STATE_CONFLICT",
            f"The model is {state.value.replace('_', ' ')}, so it cannot be deleted now.",
            409,
        )
    if generation_is_running(session):
        raise ApiError(
            "MODEL_IN_USE",
            "A generation is running, so the model cannot be deleted now.",
            409,
        )
    directory = model_directory(settings, model_id)
    shutil.rmtree(directory, ignore_errors=True)
    if directory.exists():
        raise ApiError("MODEL_DELETE_FAILED", "The model files could not be deleted.", 500)
    transition_model(session, settings, model_id, State.NOT_INSTALLED)


def run_download(settings: Settings, model_id: str, control: DownloadControl) -> None:
    """Carry a `downloading` or `verifying` model to `installed`, or to `failed` with a reason.

    Requires a catalog model. Downloads the missing bytes of each file with a range request,
    stores the license text beside them, then verifies each file's SHA-256 and moves to
    `installed`. A stop by `control` with `discard` removes the files and returns the model to
    `not_installed`; a stop without it leaves the state for the next start to resume. Never raises:
    every failure becomes the `failed` state with a message that has no path or file content.
    """
    option = get_model_option(model_id)
    try:
        write_license_file(settings, option)
        with SessionLocal() as session:
            state = read_model_state(session, model_id)
        if state is State.DOWNLOADING:
            _download_files(settings, option, control)
            with SessionLocal() as session:
                transition_model(session, settings, model_id, State.VERIFYING)
        elif state is not State.VERIFYING:
            return
        _verify_files(settings, option, control)
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
        logger.warning("Model %s changed state during its download.", model_id)
    except Exception as error:
        logger.error("Model download failed for %s (%s).", model_id, type(error).__name__)
        _fail(model_id, settings, UNEXPECTED_MESSAGE)


def _worker_main(settings: Settings, model_id: str, control: DownloadControl) -> None:
    """Run one download and forget the worker when it ends."""
    try:
        run_download(settings, model_id, control)
    finally:
        with _workers_lock:
            worker = _workers.get(model_id)
            if worker is not None and worker.control is control:
                del _workers[model_id]


def start_download_worker(settings: Settings, model_id: str) -> bool:
    """Start the background download of `model_id` unless one already runs; True when started.

    The thread belongs to the API process, the only service with a route to the Internet; the
    worker container has none and never downloads. It is a daemon so shutting down never waits for
    a multi-gigabyte transfer: the stored state and partial files let the next start resume.
    """
    with _workers_lock:
        existing = _workers.get(model_id)
        if existing is not None and existing.thread.is_alive():
            return False
        control = DownloadControl()
        thread = threading.Thread(
            target=_worker_main,
            args=(settings, model_id, control),
            name=f"model-download-{model_id}",
            daemon=True,
        )
        _workers[model_id] = _Worker(thread, control)
        thread.start()
        return True


def cancel_download(settings: Settings, model_id: str) -> None:
    """Stop the model's download or verification, delete its files and wait briefly for the end.

    With a running worker the worker does the clean-up and this call waits up to
    `CANCEL_WAIT_SECONDS` for it; without one (a stale state) the clean-up happens here.
    """
    with _workers_lock:
        worker = _workers.get(model_id)
    if worker is not None and worker.thread.is_alive():
        worker.control.request_stop(discard=True)
        worker.thread.join(CANCEL_WAIT_SECONDS)
        return
    _finish_cancel(model_id, settings)


def wait_for_download(model_id: str, timeout: float | None = None) -> bool:
    """Wait for the model's worker to end; True when none is left running."""
    with _workers_lock:
        worker = _workers.get(model_id)
    if worker is None:
        return True
    worker.thread.join(timeout)
    return not worker.thread.is_alive()


def stop_download_workers(timeout: float = 5.0) -> None:
    """Ask every worker to stop without discarding files, and wait briefly for each.

    Used at shutdown and by tests: the stored state stays `downloading` or `verifying`, which
    `resume_interrupted_downloads` continues at the next start.
    """
    with _workers_lock:
        workers = list(_workers.values())
    for worker in workers:
        worker.control.request_stop(discard=False)
    for worker in workers:
        worker.thread.join(timeout)


def resume_interrupted_downloads(settings: Settings) -> None:
    """Restart every download that a stop of the API left in `downloading` or `verifying`.

    Resuming sends a range request from the partial file's size. When the kill switch
    `NARRASTEP_LOCAL_GENERATION=disabled` is set nothing restarts: the model moves to `failed`
    with a readable message and keeps its partial files until it is deleted.
    """
    with SessionLocal() as session:
        interrupted = [
            row.model_id
            for row in session.scalars(
                select(GenerationModel).where(GenerationModel.state.in_(ACTIVE_STATES))
            )
            if row.model_id in MODEL_CATALOG
        ]
    for model_id in interrupted:
        if settings.local_generation_available:
            start_download_worker(settings, model_id)
        else:
            _fail(model_id, settings, DISABLED_MESSAGE)
