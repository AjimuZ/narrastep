"""Measure local document generation time and peak memory through the API, for the owner (P06.5a).

Installs the chosen model, generates synthetic 10-minute and 60-minute projects from FFmpeg's
speech, times only the generation and applies the fixed thresholds of the default model rule
(docs/LOCAL_GENERATION.md). It prints numbers, never transcript or document text.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.full_job_check import (
        DEFAULT_API,
        POLL_SECONDS,
        VIDEO_SECONDS,
        ApiClient,
        FullJobError,
        check_health,
        check_transcript,
        multipart_body,
        video_command,
        wait_for_completion,
    )
except ModuleNotFoundError:  # Started as `python scripts/benchmark_generation.py`.
    from full_job_check import (  # type: ignore[no-redef]
        DEFAULT_API,
        POLL_SECONDS,
        VIDEO_SECONDS,
        ApiClient,
        FullJobError,
        check_health,
        check_transcript,
        multipart_body,
        video_command,
        wait_for_completion,
    )

# The models the owner may measure, by the short name of `--model`; the identifiers are those of the
# catalog in app/services/model_catalog.py.
MODEL_IDS = {"e2b": "gemma-4-e2b", "e4b": "gemma-4-e4b"}
E4B_ID = MODEL_IDS["e4b"]
DEFAULT_MINUTES = (10, 60)

# The "Default model rule" of docs/LOCAL_GENERATION.md, fixed before the benchmark. A "GB" there is
# read as GiB, like Docker's own memory limit of 8 GB.
E4B_RULE_MINUTES = 60
E4B_MAX_SECONDS = 30 * 60
E4B_MAX_PEAK_BYTES = 6 * 1024**3

# A run that is not finished after this long is cancelled and reported as cut.
GENERATION_LIMIT_SECONDS = 45 * 60
GENERATION_POLL_SECONDS = 2.0
CANCEL_WAIT_SECONDS = 120.0
INSTALL_TIMEOUT_SECONDS = 2 * 3600.0
PROCESSING_TIMEOUT_SECONDS = 4 * 3600.0
PROJECT_MODE = "meeting"
CLIP_NAME = "benchmark-clip.mp4"
CLIP_CRF = "32"
MEDIA_COMMAND_TIMEOUT_SECONDS = 1800.0
MEMINFO_PATH = Path("/proc/meminfo")
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
INSTALL_WAITING_STATES = frozenset({"downloading", "verifying"})


@dataclass(frozen=True, slots=True)
class RunResult:
    """Hold the numbers of one generation run.

    `cut` is true when the run was cancelled at the time limit; `seconds` is then the limit that was
    reached, not a generation time. Memory is the memory in use in the whole container VM, in bytes.
    """

    model_id: str
    minutes: int
    cut: bool
    seconds: float
    baseline_bytes: int
    peak_bytes: int
    parts_total: int

    @property
    def increase_bytes(self) -> int:
        """Return the peak above the baseline taken just before the run: the job's own memory."""
        return max(0, self.peak_bytes - self.baseline_bytes)


@dataclass(frozen=True, slots=True)
class Verdict:
    """Say whether the default model rule applies to a model and whether it passed.

    `reasons` lists, with numbers only, every threshold that was not met; it is empty on a pass.
    """

    applicable: bool
    passed: bool
    reasons: tuple[str, ...]


def _gib(value: int) -> str:
    """Return `value` bytes as GiB with two decimals."""
    return f"{value / 1024**3:.2f}"


def format_run(result: RunResult) -> str:
    """Return the one-line report of a run: key=value pairs holding only numbers and fixed words."""
    return (
        f"run model={result.model_id} minutes={result.minutes} "
        f"outcome={'cut' if result.cut else 'completed'} seconds={result.seconds:.1f} "
        f"peak_memory_bytes={result.increase_bytes} peak_memory_gib={_gib(result.increase_bytes)} "
        f"baseline_memory_bytes={result.baseline_bytes} parts={result.parts_total}"
    )


def evaluate_default_model_rule(model_id: str, results: Sequence[RunResult]) -> Verdict:
    """Apply the fixed thresholds to the runs of `model_id`.

    Only E4B can become the default, so any other model is not applicable. E4B passes only when its
    60-minute run completed in at most `E4B_MAX_SECONDS` and no run of it had a memory peak of
    `E4B_MAX_PEAK_BYTES` or more. A missing or cut 60-minute run is a failure, never a pass.
    """
    if model_id != E4B_ID:
        return Verdict(False, False, ("only E4B can become the default model",))
    runs = [result for result in results if result.model_id == model_id]
    reasons: list[str] = []
    long_runs = [result for result in runs if result.minutes == E4B_RULE_MINUTES]
    if not long_runs:
        reasons.append(f"no {E4B_RULE_MINUTES}-minute run was made")
    for result in long_runs:
        if result.cut:
            reasons.append(
                f"the {E4B_RULE_MINUTES}-minute run was cut at {result.seconds:.1f} s "
                f"(limit {E4B_MAX_SECONDS} s)"
            )
        elif result.seconds > E4B_MAX_SECONDS:
            reasons.append(
                f"the {E4B_RULE_MINUTES}-minute run took {result.seconds:.1f} s "
                f"(limit {E4B_MAX_SECONDS} s)"
            )
    peak = max((result.increase_bytes for result in runs), default=0)
    if peak >= E4B_MAX_PEAK_BYTES:
        reasons.append(f"peak memory was {peak} bytes (limit under {E4B_MAX_PEAK_BYTES} bytes)")
    return Verdict(True, not reasons, tuple(reasons))


def format_verdict(model_id: str, verdict: Verdict) -> list[str]:
    """Return the report lines for the default model rule."""
    lines = [
        f"rule max_seconds={E4B_MAX_SECONDS} minutes={E4B_RULE_MINUTES} "
        f"max_peak_memory_bytes={E4B_MAX_PEAK_BYTES}"
    ]
    if not verdict.applicable:
        lines.append(f"verdict model={model_id} e4b_passes=not_applicable")
        return lines
    lines.append(f"verdict model={model_id} e4b_passes={'yes' if verdict.passed else 'no'}")
    lines.extend(f"reason {reason}" for reason in verdict.reasons)
    return lines


def read_memory_in_use_bytes(meminfo: Path = MEMINFO_PATH) -> int:
    """Return the memory in use in the whole VM, counting mapped model files, in bytes.

    The container sees the memory of the VM that Docker runs in, so the sample includes the worker's
    `llama-server`. `MemAvailable` is not used because `llama-server` maps its weights as file pages
    and the kernel counts those as available. The result is the memory that is neither free nor
    reclaimable cache, plus the mapped pages. Raises `FullJobError` when the file lacks a field.
    """
    try:
        text = meminfo.read_text(encoding="utf-8")
    except OSError as error:
        raise FullJobError(f"Memory use cannot be read from {meminfo} ({error}).") from error
    fields: dict[str, int] = {}
    for line in text.splitlines():
        name, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            fields[name] = int(parts[0]) * (1024 if parts[1:] == ["kB"] else 1)
    needed = ("MemTotal", "MemFree", "Buffers", "Cached", "SReclaimable", "Mapped")
    missing = [name for name in needed if name not in fields]
    if missing:
        raise FullJobError(f"Memory use cannot be computed: {meminfo} lacks {', '.join(missing)}.")
    return (
        fields["MemTotal"]
        - fields["MemFree"]
        - fields["Buffers"]
        - fields["Cached"]
        - fields["SReclaimable"]
        + fields["Mapped"]
    )


def ensure_model_enabled(
    client: ApiClient,
    model_id: str,
    *,
    timeout_seconds: float = INSTALL_TIMEOUT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Install and enable `model_id` through the API, resuming a failed download.

    Requires `localGeneration` to be `available`. Returns when the model is `enabled`. Raises
    `FullJobError` when generation is disabled, the install fails (with the API's readable reason)
    or `timeout_seconds` pass.
    """
    listing = client.get_json("/models")
    if listing.get("localGeneration") != "available":
        raise FullJobError("Local generation is disabled (NARRASTEP_LOCAL_GENERATION).")
    path = f"/models/{model_id}"
    state = client.get_json(path).get("state")
    if state in {"not_installed", "failed"}:
        state = client.post_json(f"{path}/download", {}).get("state")
    deadline = clock() + timeout_seconds
    while state != "enabled":
        if state == "installed":
            state = client.post_json(f"{path}/enable", {}).get("state")
        elif state in INSTALL_WAITING_STATES:
            if clock() >= deadline:
                raise FullJobError(f"{model_id} was still {state} after {timeout_seconds:g} s.")
            sleep(poll_seconds)
            state = client.get_json(path).get("state")
        elif state == "failed":
            reason = client.get_json(path).get("errorMessage")
            raise FullJobError(f"{model_id} failed to install: {reason}")
        else:
            raise FullJobError(f"{model_id} is in the unexpected state {state}.")


def clip_command(destination: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    """Return the FFmpeg argument list for the 10-second spoken clip, at a low bit rate.

    The clip of `full_job_check` is reused; only `-crf` is added so that an hour of it stays small.
    """
    command = video_command(destination, ffmpeg)
    return [*command[:-1], "-crf", CLIP_CRF, command[-1]]


def loop_command(clip: Path, destination: Path, minutes: int, ffmpeg: str = "ffmpeg") -> list[str]:
    """Return the FFmpeg argument list that repeats `clip` to fill `minutes`, copying streams."""
    repeats = minutes * 60 // VIDEO_SECONDS
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-y",
        "-stream_loop",
        str(repeats - 1),
        "-i",
        str(clip),
        "-c",
        "copy",
        str(destination),
    ]


def run_ffmpeg(command: list[str]) -> None:
    """Run an FFmpeg argument array; raise `FullJobError` with its last error line on failure."""
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=MEDIA_COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise FullJobError(f"FFmpeg could not run ({error}).") from error
    if completed.returncode != 0:
        reason = (completed.stderr.strip().splitlines() or ["no message"])[-1]
        raise FullJobError(f"FFmpeg could not build the video ({reason}).")


def make_synthetic_video(
    work_dir: Path,
    minutes: int,
    *,
    ffmpeg: str = "ffmpeg",
    run: Callable[[list[str]], None] = run_ffmpeg,
) -> Path:
    """Write a spoken video of `minutes` minutes below `work_dir` and return its path.

    The 10-second clip is spoken by FFmpeg's `flite` source and repeated, so no real recording is
    used; the clip is built once per `work_dir`. `run` executes each argument array and raises
    `FullJobError` on failure.
    """
    clip = work_dir / CLIP_NAME
    if not clip.is_file():
        run(clip_command(clip, ffmpeg))
    destination = work_dir / f"benchmark-{minutes}-minutes.mp4"
    run(loop_command(clip, destination, minutes, ffmpeg))
    return destination


def _cancel_and_wait(
    client: ApiClient,
    generation_id: str,
    poll_seconds: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> None:
    """Cancel a generation and wait until it is final, so its project can be deleted.

    Raises `FullJobError` when it is not final after `CANCEL_WAIT_SECONDS`.
    """
    client.post_json(f"/generations/{generation_id}/cancel", {})
    deadline = clock() + CANCEL_WAIT_SECONDS
    while client.get_json(f"/generations/{generation_id}").get("status") not in TERMINAL_STATUSES:
        if clock() >= deadline:
            raise FullJobError(f"The generation did not stop within {CANCEL_WAIT_SECONDS:g} s.")
        sleep(poll_seconds)


def run_generation(
    client: ApiClient,
    project_id: str,
    model_id: str,
    minutes: int,
    *,
    limit_seconds: float = GENERATION_LIMIT_SECONDS,
    poll_seconds: float = GENERATION_POLL_SECONDS,
    read_memory: Callable[[], int] = read_memory_in_use_bytes,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RunResult:
    """Queue one generation for `project_id`, time it and sample memory while it runs.

    Memory is sampled before queueing (the baseline) and at every poll. A run still going after
    `limit_seconds` is cancelled and returned with `cut` set. Raises `FullJobError` when the API
    generates with another model than `model_id` (set `NARRASTEP_GENERATION_MODEL` and restart the
    api and worker services), or when the generation fails or is cancelled by someone else.
    """
    baseline = peak = read_memory()
    started = clock()
    generation = client.post_json(f"/projects/{project_id}/generations", {})
    generation_id = str(generation["id"])
    if generation.get("modelId") != model_id:
        _cancel_and_wait(client, generation_id, poll_seconds, clock, sleep)
        raise FullJobError(
            f"The API generates with {generation.get('modelId')}, not {model_id}: set "
            "NARRASTEP_GENERATION_MODEL and recreate the api and worker services."
        )
    while True:
        peak = max(peak, read_memory())
        answer = client.get_json(f"/generations/{generation_id}")
        elapsed = clock() - started
        status = answer.get("status")
        parts = int(answer.get("partsTotal") or 0)
        if status == "completed":
            return RunResult(model_id, minutes, False, elapsed, baseline, peak, parts)
        if status in TERMINAL_STATUSES:
            raise FullJobError(f"Generation ended as {status}: {answer.get('errorCode')}.")
        if elapsed >= limit_seconds:
            _cancel_and_wait(client, generation_id, poll_seconds, clock, sleep)
            return RunResult(model_id, minutes, True, elapsed, baseline, peak, parts)
        sleep(poll_seconds)


def run_benchmark(
    client: ApiClient,
    model_id: str,
    work_dir: Path,
    *,
    durations: Sequence[int] = DEFAULT_MINUTES,
    ffmpeg: str = "ffmpeg",
    processing_timeout_seconds: float = PROCESSING_TIMEOUT_SECONDS,
    limit_seconds: float = GENERATION_LIMIT_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    emit: Callable[[str], None] = print,
    read_memory: Callable[[], int] = read_memory_in_use_bytes,
    make_video: Callable[..., Path] = make_synthetic_video,
) -> list[RunResult]:
    """Generate one synthetic project per duration and return the timed runs, in order.

    Each project is uploaded, processed (not timed) and deleted afterwards, also after a failure.
    Requires a healthy stack with `model_id` enabled. `make_video(work_dir, minutes, ffmpeg=...)`
    writes the video to upload. Raises `FullJobError` at the first step that fails.
    """
    results: list[RunResult] = []
    for minutes in durations:
        video = make_video(work_dir, minutes, ffmpeg=ffmpeg)
        size = video.stat().st_size
        emit(f"video minutes={minutes} bytes={size}")
        project = client.post_json(
            "/projects",
            {"name": f"Generation benchmark {minutes} min", "mode": PROJECT_MODE},
        )
        project_id = str(project["id"])
        try:
            body, content_type = multipart_body("file", video.name, video.read_bytes())
            video.unlink()
            client.send(
                "POST", f"/projects/{project_id}/video", body, {"Content-Type": content_type}
            )
            job = client.post_json(f"/projects/{project_id}/process", {})
            wait_for_completion(
                lambda job_id=job["id"]: client.get_json(f"/jobs/{job_id}"),
                "Processing",
                processing_timeout_seconds,
                poll_seconds=poll_seconds,
            )
            segments = client.get_json(f"/projects/{project_id}/segments")
            words = check_transcript(segments)
            emit(f"processed minutes={minutes} segments={len(segments)} words={words}")
            result = run_generation(
                client,
                project_id,
                model_id,
                minutes,
                limit_seconds=limit_seconds,
                read_memory=read_memory,
            )
            emit(format_run(result))
            results.append(result)
        finally:
            try:
                client.send("DELETE", f"/projects/{project_id}")
            except FullJobError as error:
                print(f"cleanup: the project was left behind: {error}", file=sys.stderr)
    return results


def _limit_minutes(value: str) -> float:
    """Parse `--limit-minutes`: a number above 0 and at most 45, so a cut cannot be postponed."""
    minutes = float(value)
    if not 0 < minutes <= GENERATION_LIMIT_SECONDS / 60:
        raise argparse.ArgumentTypeError("must be above 0 and at most 45")
    return minutes


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse the model, the API address, the durations and the time limits."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", choices=sorted(MODEL_IDS), required=True)
    parser.add_argument("--api", default=DEFAULT_API, help=f"API base URL (default {DEFAULT_API})")
    parser.add_argument("--minutes", type=int, nargs="+", default=list(DEFAULT_MINUTES))
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--limit-minutes", type=_limit_minutes, default=45.0)
    parser.add_argument("--install-timeout", type=float, default=INSTALL_TIMEOUT_SECONDS)
    parser.add_argument("--processing-timeout", type=float, default=PROCESSING_TIMEOUT_SECONDS)
    options = parser.parse_args(arguments)
    if any(minutes < 1 or minutes * 60 % VIDEO_SECONDS for minutes in options.minutes):
        parser.error("--minutes must be whole minutes of at least 1")
    return options


def main(arguments: list[str] | None = None) -> int:
    """Run the benchmark and print the report; return 0, or 1 with one line on stderr on failure.

    A cut run or an E4B that misses the thresholds is a result, not a failure, so it returns 0.
    """
    options = parse_arguments(arguments)
    model_id = MODEL_IDS[options.model]
    try:
        client = ApiClient(options.api)
        check_health(client)
        read_memory_in_use_bytes()
        ensure_model_enabled(client, model_id, timeout_seconds=options.install_timeout)
        print(f"model {model_id} enabled", flush=True)
        with tempfile.TemporaryDirectory(prefix="narrastep-benchmark-") as work_dir:
            results = run_benchmark(
                client,
                model_id,
                Path(work_dir),
                durations=options.minutes,
                ffmpeg=options.ffmpeg,
                processing_timeout_seconds=options.processing_timeout,
                limit_seconds=options.limit_minutes * 60,
            )
    except FullJobError as error:
        print(f"Generation benchmark failed: {error}", file=sys.stderr)
        return 1
    verdict = evaluate_default_model_rule(model_id, results)
    print("\n".join(format_verdict(model_id, verdict)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
