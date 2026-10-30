"""Tests for `scripts/full_job_check.py`, the end-to-end check of a running stack (P08.3).

A stand-in API on a local port answers as the real one does, and FFmpeg is replaced, so these tests
need neither a stack nor media tools. The real run is recorded under P08.3 in `docs/ROADMAP.md`.
"""

from __future__ import annotations

import ast
import io
import json
import sys
import threading
import zipfile
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from scripts import full_job_check as check

ARCHIVE = "2026-10-30_task_briefing_full-job-check_export-01"
TRANSCRIPT = "Open the settings page and save the form."
PNG = check.PNG_SIGNATURE + b"pixels"
SCREENSHOT = "02-screenshots/step-001_00m00s.png"


def package_entries() -> dict[str, bytes]:
    """Return a valid package as paths below the root folder, for tests to damage."""
    entries = {path: b"content" for path in check.REQUIRED_PATHS}
    entries[SCREENSHOT] = PNG
    entries["04-chatgpt-batches/batch-01-of-01/batch-input.md"] = b"content"
    entries["04-chatgpt-batches/batch-01-of-01/step-001_00m00s.png"] = PNG
    entries["metadata/manifest.json"] = json.dumps(manifest()).encode()
    return entries


def manifest(**changes: Any) -> dict[str, Any]:
    """Return a manifest matching `package_entries` with one step, plus `changes`."""
    value = {
        "schemaVersion": "0.3.0",
        "archiveName": ARCHIVE,
        "segments": [{"reviewedText": TRANSCRIPT}],
        "screenshots": [{"path": SCREENSHOT}],
        "paths": {"readme": "README.md", "chatgptBatches": "04-chatgpt-batches", "video": None},
    }
    return value | changes


def zip_bytes(entries: dict[str, bytes], root: str = ARCHIVE) -> bytes:
    """Return a ZIP holding `entries` below the folder `root`."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in entries.items():
            archive.writestr(f"{root}/{path}", content)
    return buffer.getvalue()


class StandInApi:
    """Answer the check's requests like the real API; tests change the attributes to break it."""

    def __init__(self) -> None:
        self.health = {"status": "ok", "database": "ok", "redis": "ok", "worker": "ok"}
        self.job: dict[str, Any] = {"id": "job", "status": "completed", "errorCode": None}
        self.segments = [{"reviewedText": TRANSCRIPT}]
        self.package = zip_bytes(package_entries())
        self.upload_status = 201
        self.requests: list[tuple[str, str]] = []
        self.upload_body = b""
        self.upload_type = ""
        self.project_state = "review"

    def answer(self, method: str, path: str, body: bytes, content_type: str) -> tuple[int, Any]:
        """Return the status and JSON or bytes answer of one request."""
        self.requests.append((method, path))
        match (method, path):
            case ("GET", "/api/v1/health"):
                return 200, self.health
            case ("POST", "/api/v1/projects"):
                return 201, {"id": "p"}
            case ("POST", "/api/v1/projects/p/video"):
                self.upload_body, self.upload_type = body, content_type
                error = {"error": {"code": "VIDEO_VALIDATION_FAILED"}}
                return self.upload_status, {} if self.upload_status == 201 else error
            case ("POST", "/api/v1/projects/p/process"):
                return 202, {"id": "job", "status": "queued"}
            case ("GET", "/api/v1/jobs/job"):
                return 200, self.job
            case ("GET", "/api/v1/projects/p/segments"):
                return 200, self.segments
            case ("GET", "/api/v1/projects/p"):
                return 200, {"id": "p", "status": self.project_state, "screenshotCount": 1}
            case ("POST", "/api/v1/projects/p/exports"):
                self.project_state = "completed"
                return 202, {"id": "e", "status": "queued"}
            case ("GET", "/api/v1/exports/e"):
                return 200, {
                    "id": "e",
                    "status": "completed",
                    "sizeBytes": len(self.package),
                    "downloadName": f"{ARCHIVE}.zip",
                }
            case ("GET", "/api/v1/exports/e/download"):
                return 200, self.package
            case ("DELETE", "/api/v1/projects/p"):
                return 204, b""
        return 404, {"error": {"code": "NOT_FOUND"}}


@pytest.fixture
def api() -> Iterator[tuple[StandInApi, str]]:
    """Serve a `StandInApi` on a free local port and return it with its base URL."""
    stand_in = StandInApi()

    class Handler(BaseHTTPRequestHandler):
        def handle_any(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            status, answer = stand_in.answer(
                self.command,
                self.path,
                self.rfile.read(length),
                self.headers.get("Content-Type", ""),
            )
            payload = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Content-Disposition", f'attachment; filename="{ARCHIVE}.zip"')
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_DELETE = handle_any

        def log_message(self, format: str, *args: Any) -> None:
            """Keep the test output free of request lines."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stand_in, f"http://127.0.0.1:{server.server_address[1]}/api/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.fixture(autouse=True)
def fake_video(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace FFmpeg with a file write, so that no media tool is needed."""
    monkeypatch.setattr(
        check,
        "generate_video",
        lambda destination, ffmpeg="ffmpeg": destination.write_bytes(b"generated video"),
    )


def test_a_full_job_passes_prints_numbers_and_never_the_transcript(
    api: tuple[StandInApi, str], capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in, url = api

    assert check.main(["--api", url]) == 0

    captured = capsys.readouterr()
    assert "1 segment(s), 8 word(s), 1 screenshot(s)" in captured.out
    assert "1 screenshot(s), 1 batch(es), 1 manifest step(s)" in captured.out
    assert "Full job check passed." in captured.out
    for word in TRANSCRIPT.split():
        if word not in {"the", "and"}:
            assert word not in captured.out + captured.err
    assert ("DELETE", "/api/v1/projects/p") == stand_in.requests[-1]
    assert b"generated video" in stand_in.upload_body
    assert b'filename="full-job-check.mp4"' in stand_in.upload_body
    assert stand_in.upload_type.startswith("multipart/form-data; boundary=narrastep-")


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"health": {"status": "degraded"}}, "status as degraded"),
        ({"health": {"status": "ok", "database": "ok", "redis": "ok"}}, "worker as None"),
        (
            {"job": {"id": "job", "status": "failed", "errorCode": "TRANSCRIPTION_FAILED"}},
            "Processing ended as failed: TRANSCRIPTION_FAILED",
        ),
        ({"segments": []}, "no transcript segment"),
        ({"upload_status": 422}, "answered 422 VIDEO_VALIDATION_FAILED"),
    ],
)
def test_a_stack_that_fails_a_step_fails_the_check(
    api: tuple[StandInApi, str],
    capsys: pytest.CaptureFixture[str],
    change: dict[str, Any],
    expected: str,
) -> None:
    stand_in, url = api
    for name, value in change.items():
        setattr(stand_in, name, value)

    assert check.main(["--api", url]) == 1

    assert expected in capsys.readouterr().err


def test_a_mock_transcript_fails_without_being_printed(
    api: tuple[StandInApi, str], capsys: pytest.CaptureFixture[str]
) -> None:
    stand_in, url = api
    stand_in.segments = [{"reviewedText": "Deterministic mock transcript segment 1."}]

    assert check.main(["--api", url]) == 1

    captured = capsys.readouterr()
    assert "lacks the expected words: settings, save" in captured.err
    assert "Deterministic" not in captured.out + captured.err
    assert ("DELETE", "/api/v1/projects/p") == stand_in.requests[-1]


def test_a_project_that_cannot_be_deleted_is_reported_without_failing_the_job(
    api: tuple[StandInApi, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stand_in, url = api
    answer = stand_in.answer

    def refuse_delete(method: str, path: str, body: bytes, content_type: str) -> tuple[int, Any]:
        if method == "DELETE":
            return 409, {"error": {"code": "PROJECT_BUSY"}}
        return answer(method, path, body, content_type)

    monkeypatch.setattr(stand_in, "answer", refuse_delete)

    assert check.main(["--api", url]) == 0
    assert "left behind" in capsys.readouterr().err


def test_an_unreachable_or_invalid_api_fails_in_one_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert check.main(["--api", "http://127.0.0.1:9/api/v1"]) == 1
    assert "could not reach the API" in capsys.readouterr().err

    assert check.main(["--api", "file:///etc/passwd"]) == 1
    assert "must be an http or https URL" in capsys.readouterr().err


def test_a_job_that_never_finishes_times_out() -> None:
    with pytest.raises(check.FullJobError, match="still processing after 0 s"):
        check.wait_for_completion(lambda: {"status": "processing"}, "Processing", 0)


def write_archive(tmp_path: Path, content: bytes) -> Path:
    """Write ZIP bytes to a file and return its path."""
    path = tmp_path / "package.zip"
    path.write_bytes(content)
    return path


def test_a_complete_archive_reports_its_counts(tmp_path: Path) -> None:
    report = check.check_archive(write_archive(tmp_path, zip_bytes(package_entries())), ARCHIVE, 1)

    assert report == check.ArchiveReport(
        entry_count=len(package_entries()), screenshot_count=1, batch_count=1, segment_count=1
    )


def without(path: str) -> dict[str, bytes]:
    """Return the package without one entry."""
    return {key: value for key, value in package_entries().items() if key != path}


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        (without("README.md"), "lacks README.md"),
        (without("03-transcript/subtitles.srt"), "lacks 03-transcript/subtitles.srt"),
        (package_entries() | {"README.md": b""}, "empty README.md"),
        (without(SCREENSHOT), "no screenshot"),
        (package_entries() | {SCREENSHOT: b"not an image"}, "is not a PNG"),
        (
            without("04-chatgpt-batches/batch-01-of-01/batch-input.md"),
            "0 batch totals",
        ),
        (
            package_entries() | {"04-chatgpt-batches/batch-03-of-03/batch-input.md": b"content"},
            "2 batch totals",
        ),
        (package_entries() | {"source/original-video.mp4": b"video"}, "original video"),
    ],
)
def test_a_damaged_archive_is_refused(
    tmp_path: Path, entries: dict[str, bytes], expected: str
) -> None:
    archive = write_archive(tmp_path, zip_bytes(entries))

    with pytest.raises(check.FullJobError, match=expected):
        check.check_archive(archive, ARCHIVE, 1)


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"schemaVersion": "0.2.0"}, "manifest schema is 0.2.0"),
        ({"archiveName": "other"}, "another archive"),
        ({"segments": []}, "0 steps; the API has 1"),
        ({"screenshots": [{"path": "02-screenshots/gone.png"}]}, "gone.png, which is not"),
        ({"paths": {"llmInput": "01-ai-input/gone.md"}}, "gone.md, which is not"),
    ],
)
def test_a_manifest_that_disagrees_is_refused(
    tmp_path: Path, changes: dict[str, Any], expected: str
) -> None:
    entries = package_entries() | {
        "metadata/manifest.json": json.dumps(manifest(**changes)).encode()
    }
    archive = write_archive(tmp_path, zip_bytes(entries))

    with pytest.raises(check.FullJobError, match=expected):
        check.check_archive(archive, ARCHIVE, 1)


def test_a_wrong_root_or_a_file_that_is_no_zip_is_refused(tmp_path: Path) -> None:
    other_root = write_archive(tmp_path, zip_bytes(package_entries(), root="other"))
    with pytest.raises(check.FullJobError, match="root"):
        check.check_archive(other_root, ARCHIVE, 1)

    not_a_zip = write_archive(tmp_path, b"an HTML error page")
    with pytest.raises(check.FullJobError, match="not a readable ZIP"):
        check.check_archive(not_a_zip, ARCHIVE, 1)


def test_the_video_command_speaks_the_sentence_with_a_filter_safe_text(tmp_path: Path) -> None:
    command = check.video_command(tmp_path / "v.mp4", "/usr/bin/ffmpeg")

    assert command[0] == "/usr/bin/ffmpeg" and command[-1] == str(tmp_path / "v.mp4")
    assert f"flite=text='{check.SPEECH_TEXT}':voice=slt" in command
    assert not set(",':;[]\\") & set(check.SPEECH_TEXT)
    assert check.missing_words(check.SPEECH_TEXT) == []


def test_a_failing_ffmpeg_is_reported_in_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()

    with pytest.raises(check.FullJobError, match="FFmpeg could not run"):
        check.generate_video(tmp_path / "v.mp4", str(tmp_path / "no-such-ffmpeg"))


def test_the_script_imports_only_the_standard_library() -> None:
    tree = ast.parse(Path(check.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])

    assert imported <= sys.stdlib_module_names
