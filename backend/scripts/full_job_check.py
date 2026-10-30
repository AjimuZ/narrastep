"""Prove a running stack end to end: upload, process and export a generated video (P08.3).

Uses only the standard library and FFmpeg, so it can run in the API container or against any
published API. It prints numbers and never transcript text.
"""

from __future__ import annotations

import argparse
import http.client
import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_API = "http://127.0.0.1:8000/api/v1"
DEFAULT_TIMEOUT_SECONDS = 900.0
REQUEST_TIMEOUT_SECONDS = 120.0
POLL_SECONDS = 1.0
MEDIA_COMMAND_TIMEOUT_SECONDS = 300.0

# Spoken by FFmpeg's `flite` source (built into Debian's FFmpeg), so no file or network is needed.
# The commas of normal prose would end the FFmpeg filter argument, so the sentences have none.
SPEECH_TEXT = "Open the settings page and save the form. Then click the blue button to confirm."
# Words every model from `tiny` up hears in SPEECH_TEXT. A stack in mock processing fails on them.
EXPECTED_WORDS = ("settings", "save")
VIDEO_SECONDS = 10
VIDEO_NAME = "full-job-check.mp4"
PROJECT_NAME = "Full job check"
PROJECT_MODE = "task_briefing"
MANIFEST_SCHEMA = "0.3.0"

# The numbered layout of docs/DATA_MODEL.md, relative to the archive root folder.
REQUIRED_PATHS = (
    "README.md",
    "01-ai-input/llm-input.md",
    "01-ai-input/generation-prompt.md",
    "03-transcript/reviewed-transcript.md",
    "03-transcript/subtitles.srt",
    "03-transcript/raw-transcript.json",
    "04-chatgpt-batches/README.md",
    "metadata/manifest.json",
    "metadata/source.json",
)
SCREENSHOT_PATH = re.compile(r"^02-screenshots/step-\d{3}_\d{2}m\d{2}s\.png$")
BATCH_INPUT_PATH = re.compile(r"^04-chatgpt-batches/batch-(\d{2,})-of-(\d{2,})/batch-input\.md$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class FullJobError(RuntimeError):
    """Explain, in one line without a stack trace or transcript text, why the check failed."""


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    """Count what a downloaded package holds: entries, screenshots, batches and manifest steps."""

    entry_count: int
    screenshot_count: int
    batch_count: int
    segment_count: int


class ApiClient:
    """Call one Narrastep API directly, ignoring proxy settings, and report failures as one line."""

    def __init__(self, base_url: str) -> None:
        """Keep `base_url`; raise `FullJobError` unless it is an http or https URL with a host."""
        parts = urlsplit(base_url)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise FullJobError(f"--api must be an http or https URL, not {base_url!r}.")
        self.base_url = base_url.rstrip("/")
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def send(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[bytes, http.client.HTTPMessage]:
        """Return the body and headers of a 2xx answer.

        Raises `FullJobError` with the status and the API's stable error code for any other answer,
        and with the cause when the API cannot be reached.
        """
        request = urllib.request.Request(
            f"{self.base_url}{path}", data=body, headers=dict(headers or {}), method=method
        )
        try:
            with self._opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                return response.read(), response.headers
        except urllib.error.HTTPError as error:
            raise FullJobError(
                f"{method} {path} answered {error.code} {_error_code(error.read())}."
            ) from error
        except (OSError, http.client.HTTPException) as error:
            raise FullJobError(f"{method} {path} could not reach the API ({error}).") from error

    def get_json(self, path: str) -> Any:
        """Return the decoded JSON of a GET; raise `FullJobError` on failure or invalid JSON."""
        return _decode_json(self.send("GET", path)[0], path)

    def post_json(self, path: str, value: Mapping[str, Any]) -> Any:
        """Return the decoded JSON of a POST with a JSON body; raise `FullJobError` on failure."""
        body = json.dumps(value).encode("utf-8")
        answer, _ = self.send("POST", path, body, {"Content-Type": "application/json"})
        return _decode_json(answer, path)


def _decode_json(body: bytes, path: str) -> Any:
    """Return `body` parsed as JSON; raise `FullJobError` naming `path` when it is not JSON."""
    try:
        return json.loads(body)
    except ValueError as error:
        raise FullJobError(f"{path} did not answer with JSON.") from error


def _error_code(body: bytes) -> str:
    """Return the stable code of an API error envelope, or `without an error code`."""
    try:
        return str(json.loads(body)["error"]["code"])
    except ValueError, KeyError, TypeError:
        return "without an error code"


def video_command(destination: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    """Return the FFmpeg argument list that writes the spoken test video to `destination`."""
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=s=640x360:r=15:d={VIDEO_SECONDS}",
        "-f",
        "lavfi",
        "-i",
        f"flite=text='{SPEECH_TEXT}':voice=slt",
        "-filter:a",
        f"apad,atrim=0:{VIDEO_SECONDS},aresample=44100",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        str(destination),
    ]


def generate_video(destination: Path, ffmpeg: str = "ffmpeg") -> None:
    """Write the test video to `destination`; raise `FullJobError` when FFmpeg fails."""
    try:
        completed = subprocess.run(
            video_command(destination, ffmpeg),
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=MEDIA_COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FullJobError(f"FFmpeg could not run ({error}).") from error
    if completed.returncode != 0 or not destination.is_file():
        reason = (completed.stderr.strip().splitlines() or ["no message"])[-1]
        raise FullJobError(f"FFmpeg could not generate the video ({reason}).")


def multipart_body(field: str, filename: str, content: bytes) -> tuple[bytes, str]:
    """Return a one-file multipart body and its `Content-Type` header value."""
    boundary = f"narrastep-{uuid.uuid4().hex}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: video/mp4\r\n\r\n"
    ).encode()
    return head + content + f"\r\n--{boundary}--\r\n".encode(), (
        f"multipart/form-data; boundary={boundary}"
    )


def missing_words(text: str, expected: Sequence[str] = EXPECTED_WORDS) -> list[str]:
    """Return the expected words absent from `text`, ignoring case and punctuation."""
    heard = {"".join(ch for ch in word if ch.isalnum()) for word in text.casefold().split()}
    return [word for word in expected if word.casefold() not in heard]


def wait_for_completion(
    fetch: Callable[[], Mapping[str, Any]],
    label: str,
    timeout_seconds: float,
    *,
    poll_seconds: float = POLL_SECONDS,
) -> Mapping[str, Any]:
    """Poll `fetch` until its `status` is `completed` and return that answer.

    Raises `FullJobError` with the stable error code on another final state (a state the API lists
    as still running is `queued`, `processing` or `cancelling`), and when `timeout_seconds` pass.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        answer = fetch()
        status = answer.get("status")
        if status == "completed":
            return answer
        if status not in {"queued", "processing", "cancelling"}:
            raise FullJobError(f"{label} ended as {status}: {answer.get('errorCode')}.")
        if time.monotonic() >= deadline:
            raise FullJobError(f"{label} was still {status} after {timeout_seconds:g} s.")
        time.sleep(poll_seconds)


def check_health(client: ApiClient) -> Mapping[str, Any]:
    """Return the health answer; raise `FullJobError` unless every part of it reports `ok`."""
    health = client.get_json("/health")
    for part in ("status", "database", "redis", "worker"):
        if health.get(part) != "ok":
            raise FullJobError(f"Health reports {part} as {health.get(part)}.")
    return health


def check_transcript(segments: Sequence[Mapping[str, Any]]) -> int:
    """Return the number of words in the segments; raise `FullJobError` when the speech is absent.

    The failure names only the expected words of the synthetic sentence, never what was heard.
    """
    if not segments:
        raise FullJobError("Processing produced no transcript segment.")
    text = " ".join(str(segment.get("reviewedText", "")) for segment in segments)
    absent = missing_words(text)
    if absent:
        raise FullJobError(f"The transcript lacks the expected words: {', '.join(absent)}.")
    return len(text.split())


def check_archive(archive_path: Path, archive_name: str, segment_count: int) -> ArchiveReport:
    """Check a downloaded ZIP against the documented layout and return its counts.

    Requires one root folder called `archive_name`, every `REQUIRED_PATHS` entry, real PNG
    screenshots, complete batch folders, a schema 0.3.0 manifest whose steps match `segment_count`
    and whose screenshot paths exist, and no original video. Raises `FullJobError` naming the first
    problem; nothing from the archive is extracted to disk and no transcript text is reported.
    """
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if archive.testzip() is not None:
                raise FullJobError("The ZIP holds a damaged entry.")
            names = [name for name in archive.namelist() if not name.endswith("/")]
            roots = {name.split("/", 1)[0] for name in names}
            if roots != {archive_name}:
                raise FullJobError(f"The ZIP has {len(roots)} root(s), not one named as expected.")
            prefix = f"{archive_name}/"
            paths = {name.removeprefix(prefix) for name in names}
            for required in REQUIRED_PATHS:
                if required not in paths:
                    raise FullJobError(f"The ZIP lacks {required}.")
                if archive.getinfo(prefix + required).file_size == 0:
                    raise FullJobError(f"The ZIP holds an empty {required}.")
            screenshots = sorted(path for path in paths if SCREENSHOT_PATH.match(path))
            if not screenshots:
                raise FullJobError("The ZIP holds no screenshot named as documented.")
            for path in screenshots:
                if not archive.read(prefix + path).startswith(PNG_SIGNATURE):
                    raise FullJobError(f"{path} is not a PNG.")
            batch_count = _check_batches(paths)
            if any(path.startswith("source/") for path in paths):
                raise FullJobError("The ZIP holds the original video, which was not requested.")
            manifest = _decode_json(archive.read(f"{prefix}metadata/manifest.json"), "manifest")
    except (OSError, zipfile.BadZipFile) as error:
        raise FullJobError("The download is not a readable ZIP.") from error
    _check_manifest(manifest, paths, archive_name, segment_count)
    return ArchiveReport(len(names), len(screenshots), batch_count, segment_count)


def _check_batches(paths: set[str]) -> int:
    """Return the number of batch folders; raise `FullJobError` unless `1..total` are all there."""
    matches = [match for match in map(BATCH_INPUT_PATH.match, paths) if match]
    totals = {int(match.group(2)) for match in matches}
    if len(totals) != 1:
        raise FullJobError(f"The ZIP has {len(totals)} batch totals, not one.")
    total = totals.pop()
    if sorted(int(match.group(1)) for match in matches) != list(range(1, total + 1)):
        raise FullJobError(f"The ZIP does not hold all {total} batch folders.")
    return total


def _check_manifest(
    manifest: Mapping[str, Any], paths: set[str], archive_name: str, segment_count: int
) -> None:
    """Raise `FullJobError` when the manifest disagrees with the archive or the API."""
    if manifest.get("schemaVersion") != MANIFEST_SCHEMA:
        raise FullJobError(f"The manifest schema is {manifest.get('schemaVersion')}.")
    if manifest.get("archiveName") != archive_name:
        raise FullJobError("The manifest names another archive.")
    steps = len(manifest.get("segments", []))
    if steps != segment_count:
        raise FullJobError(f"The manifest has {steps} steps; the API has {segment_count}.")
    listed = [entry.get("path") for entry in manifest.get("screenshots", [])]
    listed += [path for path in manifest.get("paths", {}).values() if path is not None]
    for path in listed:
        # `paths.chatgptBatches` names a folder; every other entry names a file.
        if path not in paths and not any(item.startswith(f"{path}/") for item in paths):
            raise FullJobError(f"The manifest lists {path}, which is not in the ZIP.")


def run_full_job(
    client: ApiClient,
    work_dir: Path,
    *,
    ffmpeg: str = "ffmpeg",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
) -> None:
    """Run the whole job through `client` and print the numbers of each step.

    Requires a stack whose health is ok. The project is deleted afterwards, also after a failure,
    so that the check can run repeatedly; a project that cannot be deleted is only reported. Raises
    `FullJobError` at the first step that fails.
    """
    started = time.monotonic()

    def report(step: str, details: str) -> None:
        """Print one step with the seconds since the start of the check."""
        print(f"{step}: {details} ({time.monotonic() - started:.1f} s)", flush=True)

    health = check_health(client)
    report("health", ", ".join(f"{key} {health[key]}" for key in sorted(health)))

    video_path = work_dir / VIDEO_NAME
    generate_video(video_path, ffmpeg)
    video = video_path.read_bytes()
    report("video", f"{len(video)} bytes, {VIDEO_SECONDS} s")

    project = client.post_json(
        "/projects", {"name": PROJECT_NAME, "mode": PROJECT_MODE, "sourceLanguage": "en"}
    )
    project_id = project["id"]
    try:
        body, content_type = multipart_body("file", VIDEO_NAME, video)
        client.send("POST", f"/projects/{project_id}/video", body, {"Content-Type": content_type})
        report("upload", f"{len(video)} bytes accepted")

        job = client.post_json(f"/projects/{project_id}/process", {})
        finished = wait_for_completion(
            lambda: client.get_json(f"/jobs/{job['id']}"),
            "Processing",
            timeout_seconds,
            poll_seconds=poll_seconds,
        )
        segments = client.get_json(f"/projects/{project_id}/segments")
        word_count = check_transcript(segments)
        processed = client.get_json(f"/projects/{project_id}")
        if processed.get("status") != "review":
            raise FullJobError(f"The processed project is {processed.get('status')}, not review.")
        report(
            "process",
            f"job {finished['status']}, {len(segments)} segment(s), {word_count} word(s), "
            f"{processed.get('screenshotCount')} screenshot(s)",
        )

        export = client.post_json(
            f"/projects/{project_id}/exports", {"includeOriginalVideo": False}
        )
        done = wait_for_completion(
            lambda: client.get_json(f"/exports/{export['id']}"),
            "Export",
            timeout_seconds,
            poll_seconds=poll_seconds,
        )
        report("export", f"{done['status']}, {done.get('sizeBytes')} bytes")

        package, headers = client.send("GET", f"/exports/{export['id']}/download")
        download_name = str(done.get("downloadName", ""))
        if download_name not in headers.get("Content-Disposition", ""):
            raise FullJobError("The download does not carry the stored ZIP name.")
        if not download_name.endswith("_export-01.zip"):
            raise FullJobError("The stored ZIP name does not end with its export number.")
        archive_path = work_dir / "package.zip"
        archive_path.write_bytes(package)
        result = check_archive(archive_path, download_name.removesuffix(".zip"), len(segments))
        report(
            "archive",
            f"{len(package)} bytes, {result.entry_count} entries, "
            f"{result.screenshot_count} screenshot(s), {result.batch_count} batch(es), "
            f"{result.segment_count} manifest step(s)",
        )

        completed = client.get_json(f"/projects/{project_id}")
        if completed.get("status") != "completed":
            raise FullJobError(f"The exported project is {completed.get('status')}.")
    finally:
        try:
            client.send("DELETE", f"/projects/{project_id}")
            report("cleanup", "project deleted")
        except FullJobError as error:
            print(f"cleanup: the project was left behind: {error}", file=sys.stderr)


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse the API address, the FFmpeg path and the time limit of each background step."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default=DEFAULT_API, help=f"API base URL (default {DEFAULT_API})")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Return 0 and print `Full job check passed`, or print the reason on stderr and return 1."""
    options = parse_arguments(arguments)
    try:
        with tempfile.TemporaryDirectory(prefix="narrastep-full-job-") as work_dir:
            run_full_job(
                ApiClient(options.api),
                Path(work_dir),
                ffmpeg=options.ffmpeg,
                timeout_seconds=options.timeout,
            )
    except FullJobError as error:
        print(f"Full job check failed: {error}", file=sys.stderr)
        return 1
    print("Full job check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
