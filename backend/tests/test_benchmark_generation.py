"""Tests for `scripts/benchmark_generation.py`, the owner's generation benchmark (P06.5a).

A stand-in client, a virtual clock and a generated `meminfo` file replace the API, time and the
kernel, so these tests need neither a stack, a model nor FFmpeg. The real benchmark is run by the
owner and its figures are recorded in `docs/STATUS.md`, never here.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from scripts import benchmark_generation as bench
from scripts.full_job_check import FullJobError

GIB = 1024**3
E2B = bench.MODEL_IDS["e2b"]
E4B = bench.MODEL_IDS["e4b"]
SECRET_TEXT = "private words of the meeting"
# What a report line may hold: lowercase words, digits and the separators of key=value pairs.
REPORT_LINE = re.compile(r"^[a-z0-9_=. \-()]+$")


class VirtualTime:
    """Replace the clock and sleep: sleeping moves the clock, so nothing really waits."""

    def __init__(self) -> None:
        self.now = 1000.0

    def clock(self) -> float:
        """Return the virtual time in seconds."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Advance the virtual time."""
        self.now += seconds


def result(
    model: str = E4B,
    minutes: int = 60,
    *,
    cut: bool = False,
    seconds: float = 1200.0,
    peak: int = 4 * GIB,
    baseline: int = 1 * GIB,
) -> bench.RunResult:
    """Return a run result; the memory increase is `peak - baseline`."""
    return bench.RunResult(model, minutes, cut, seconds, baseline, peak, 7)


class StandInClient:
    """Answer the benchmark's API calls; tests change the attributes to break it."""

    def __init__(self, time: VirtualTime | None = None) -> None:
        self.time = time or VirtualTime()
        self.calls: list[tuple[str, str]] = []
        self.models_available = "available"
        self.model_states = ["not_installed"]
        self.model_error = "The file is not a pinned model file."
        self.generation_model = E4B
        self.generation_seconds = 100.0
        self.generation_status = "completed"
        self.cancel_stops = True
        self.started_at = 0.0
        self.cancelled = False
        self.segments: list[dict[str, str]] = [
            {"reviewedText": "Open the settings page and save the form."}
        ]
        self.delete_fails = False

    def _record(self, method: str, path: str) -> None:
        self.calls.append((method, path))

    def paths(self, method: str) -> list[str]:
        """Return the paths called with `method`, in order."""
        return [path for name, path in self.calls if name == method]

    def get_json(self, path: str) -> Any:
        """Answer a GET."""
        self._record("GET", path)
        if path == "/models":
            return {"localGeneration": self.models_available, "items": []}
        if path.startswith("/models/"):
            state = (
                self.model_states[0] if len(self.model_states) == 1 else self.model_states.pop(0)
            )
            return {"state": state, "errorMessage": self.model_error}
        if path.startswith("/jobs/"):
            return {"status": "completed", "errorCode": None}
        if path.endswith("/segments"):
            return self.segments
        if path.startswith("/generations/"):
            return self._generation()
        raise AssertionError(f"unexpected GET {path}")

    def _generation(self) -> dict[str, Any]:
        """Return the generation: running until `generation_seconds` pass or a cancel."""
        if self.cancelled and self.cancel_stops:
            return {"status": "cancelled", "partsTotal": 3}
        if self.cancelled:
            return {"status": "cancelling", "partsTotal": 3}
        if self.time.now - self.started_at >= self.generation_seconds:
            status = self.generation_status
            return {"status": status, "errorCode": "GENERATION_FAILED", "partsTotal": 3}
        return {"status": "processing", "partsTotal": 3}

    def post_json(self, path: str, value: Mapping[str, Any]) -> Any:
        """Answer a POST with a JSON body."""
        self._record("POST", path)
        if path.endswith("/download"):
            return {"state": self.model_states[0]}
        if path.endswith("/enable"):
            self.model_states = ["enabled"]
            return {"state": "enabled"}
        if path == "/projects":
            return {"id": "project"}
        if path.endswith("/process"):
            return {"id": "job"}
        if path.endswith("/generations"):
            self.started_at = self.time.now
            return {"id": "generation", "modelId": self.generation_model, "status": "queued"}
        if path.endswith("/cancel"):
            self.cancelled = True
            return {"id": "generation"}
        raise AssertionError(f"unexpected POST {path}")

    def send(
        self,
        method: str,
        path: str,
        body: bytes | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[bytes, Any]:
        """Answer an upload or a delete."""
        self._record(method, path)
        if method == "DELETE" and self.delete_fails:
            raise FullJobError("DELETE answered 409 PROJECT_BUSY.")
        return b"", {}


def memory_reader(*values: int) -> Callable[[], int]:
    """Return a memory reader that gives `values` in order and then repeats the last one."""
    iterator: Iterator[int] = iter(values)
    last = values[-1]

    def read() -> int:
        nonlocal last
        last = next(iterator, last)
        return last

    return read


def write_video(work_dir: Path, minutes: int, *, ffmpeg: str = "ffmpeg") -> Path:
    """Write a stand-in video file, as `make_synthetic_video` would."""
    path = work_dir / f"benchmark-{minutes}-minutes.mp4"
    path.write_bytes(b"video")
    return path


# --- report ---------------------------------------------------------------------------------------


def test_run_line_holds_numbers_and_fixed_words_only() -> None:
    line = bench.format_run(result(minutes=10, seconds=123.456, peak=3 * GIB, baseline=1 * GIB))

    assert line == (
        "run model=gemma-4-e4b minutes=10 outcome=completed seconds=123.5 "
        f"peak_memory_bytes={2 * GIB} peak_memory_gib=2.00 baseline_memory_bytes={GIB} parts=7"
    )
    assert REPORT_LINE.match(line)


def test_a_cut_run_is_flagged() -> None:
    line = bench.format_run(result(cut=True, seconds=2700.0))

    assert "outcome=cut" in line
    assert "seconds=2700.0" in line
    assert "completed" not in line


def test_memory_increase_is_never_negative() -> None:
    assert result(peak=1 * GIB, baseline=2 * GIB).increase_bytes == 0


def test_verdict_lines_state_the_thresholds_and_stay_numeric() -> None:
    lines = bench.format_verdict(E4B, bench.evaluate_default_model_rule(E4B, [result()]))

    assert lines[0] == f"rule max_seconds=1800 minutes=60 max_peak_memory_bytes={6 * GIB}"
    assert lines[1] == "verdict model=gemma-4-e4b e4b_passes=yes"
    assert all(REPORT_LINE.match(line) for line in lines)


# --- thresholds -----------------------------------------------------------------------------------


def test_the_thresholds_are_those_of_the_documented_rule() -> None:
    assert bench.E4B_MAX_SECONDS == 30 * 60
    assert bench.E4B_MAX_PEAK_BYTES == 6 * GIB
    assert bench.E4B_RULE_MINUTES == 60
    assert bench.GENERATION_LIMIT_SECONDS == 45 * 60


def test_e4b_passes_inside_both_thresholds() -> None:
    runs = [result(minutes=10, seconds=200.0), result(minutes=60, seconds=1500.0)]

    verdict = bench.evaluate_default_model_rule(E4B, runs)

    assert verdict == bench.Verdict(applicable=True, passed=True, reasons=())


def test_e4b_passes_at_exactly_thirty_minutes() -> None:
    assert bench.evaluate_default_model_rule(E4B, [result(seconds=1800.0)]).passed


def test_e4b_fails_on_time() -> None:
    verdict = bench.evaluate_default_model_rule(E4B, [result(seconds=1800.1)])

    assert not verdict.passed
    assert verdict.reasons == ("the 60-minute run took 1800.1 s (limit 1800 s)",)


def test_e4b_fails_on_memory_at_the_limit() -> None:
    verdict = bench.evaluate_default_model_rule(E4B, [result(baseline=0, peak=6 * GIB)])

    assert not verdict.passed
    assert len(verdict.reasons) == 1
    assert "peak memory" in verdict.reasons[0]


def test_e4b_passes_one_byte_under_the_memory_limit() -> None:
    assert bench.evaluate_default_model_rule(E4B, [result(baseline=0, peak=6 * GIB - 1)]).passed


def test_the_memory_of_the_short_run_counts_too() -> None:
    runs = [result(minutes=10, baseline=0, peak=7 * GIB), result(minutes=60)]

    assert not bench.evaluate_default_model_rule(E4B, runs).passed


def test_a_cut_e4b_run_never_passes() -> None:
    verdict = bench.evaluate_default_model_rule(E4B, [result(cut=True, seconds=2700.0)])

    assert not verdict.passed
    assert verdict.reasons == ("the 60-minute run was cut at 2700.0 s (limit 1800 s)",)


def test_a_missing_60_minute_run_never_passes() -> None:
    verdict = bench.evaluate_default_model_rule(E4B, [result(minutes=10)])

    assert not verdict.passed
    assert verdict.reasons == ("no 60-minute run was made",)


def test_all_failures_are_listed() -> None:
    runs = [result(cut=True, seconds=2700.0, baseline=0, peak=8 * GIB)]

    assert len(bench.evaluate_default_model_rule(E4B, runs).reasons) == 2


def test_e2b_is_not_applicable_and_never_changes_the_default() -> None:
    verdict = bench.evaluate_default_model_rule(E2B, [result(model=E2B)])

    assert verdict.applicable is False
    assert verdict.passed is False
    assert (
        bench.format_verdict(E2B, verdict)[-1]
        == "verdict model=gemma-4-e2b e4b_passes=not_applicable"
    )


def test_runs_of_another_model_are_ignored() -> None:
    runs = [result(model=E2B, minutes=60), result(minutes=10)]

    assert bench.evaluate_default_model_rule(E4B, runs).reasons == ("no 60-minute run was made",)


# --- memory ---------------------------------------------------------------------------------------


def meminfo(tmp_path: Path, **kib: int) -> Path:
    fields = {
        "MemTotal": 8_000_000,
        "MemFree": 1_000_000,
        "Buffers": 100_000,
        "Cached": 3_000_000,
        "SReclaimable": 200_000,
        "Mapped": 2_500_000,
    } | kib
    path = tmp_path / "meminfo"
    path.write_text("".join(f"{name}:  {value} kB\n" for name, value in fields.items()))
    return path


def test_memory_in_use_counts_mapped_files_but_not_reclaimable_cache(tmp_path: Path) -> None:
    used = bench.read_memory_in_use_bytes(meminfo(tmp_path))

    assert used == (8_000_000 - 1_000_000 - 100_000 - 3_000_000 - 200_000 + 2_500_000) * 1024


def test_memory_in_use_names_a_missing_field(tmp_path: Path) -> None:
    path = tmp_path / "meminfo"
    path.write_text("MemTotal:  8000000 kB\n")

    with pytest.raises(FullJobError, match="lacks MemFree, Buffers, Cached, SReclaimable, Mapped"):
        bench.read_memory_in_use_bytes(path)


def test_memory_in_use_fails_clearly_without_the_file(tmp_path: Path) -> None:
    with pytest.raises(FullJobError, match="cannot be read"):
        bench.read_memory_in_use_bytes(tmp_path / "absent")


# --- model install --------------------------------------------------------------------------------


def test_a_missing_model_is_downloaded_then_enabled() -> None:
    times = VirtualTime()
    client = StandInClient(times)
    client.model_states = ["not_installed", "downloading", "downloading", "verifying", "installed"]

    bench.ensure_model_enabled(client, E4B, clock=times.clock, sleep=times.sleep, poll_seconds=5)

    assert client.paths("POST") == [f"/models/{E4B}/download", f"/models/{E4B}/enable"]


def test_an_enabled_model_is_left_alone() -> None:
    client = StandInClient()
    client.model_states = ["enabled"]

    bench.ensure_model_enabled(client, E2B)

    assert client.paths("POST") == []


def test_an_installed_model_is_only_enabled() -> None:
    client = StandInClient()
    client.model_states = ["installed"]

    bench.ensure_model_enabled(client, E2B)

    assert client.paths("POST") == [f"/models/{E2B}/enable"]


def test_a_failed_model_is_downloaded_again() -> None:
    client = StandInClient()
    client.model_states = ["failed", "installed"]

    bench.ensure_model_enabled(client, E2B)

    assert f"/models/{E2B}/download" in client.paths("POST")


def test_a_download_that_fails_reports_the_api_reason() -> None:
    times = VirtualTime()
    client = StandInClient(times)
    client.model_states = ["not_installed", "downloading", "failed"]

    with pytest.raises(FullJobError, match="not a pinned model file"):
        bench.ensure_model_enabled(client, E2B, clock=times.clock, sleep=times.sleep)


def test_a_download_that_never_ends_times_out() -> None:
    times = VirtualTime()
    client = StandInClient(times)
    client.model_states = ["downloading"]

    with pytest.raises(FullJobError, match="still downloading after 60 s"):
        bench.ensure_model_enabled(
            client, E2B, timeout_seconds=60, poll_seconds=10, clock=times.clock, sleep=times.sleep
        )


def test_the_kill_switch_stops_the_install() -> None:
    client = StandInClient()
    client.models_available = "disabled"

    with pytest.raises(FullJobError, match="disabled"):
        bench.ensure_model_enabled(client, E2B)
    assert client.paths("POST") == []


# --- one generation -------------------------------------------------------------------------------


def run_one(
    client: StandInClient, read: Callable[[], int], limit: float = 2700.0
) -> bench.RunResult:
    return bench.run_generation(
        client,
        "project",
        E4B,
        60,
        limit_seconds=limit,
        poll_seconds=2.0,
        read_memory=read,
        clock=client.time.clock,
        sleep=client.time.sleep,
    )


def test_a_completed_run_reports_time_and_peak_memory() -> None:
    client = StandInClient()
    client.generation_seconds = 100.0

    outcome = run_one(client, memory_reader(GIB, 2 * GIB, 5 * GIB, 3 * GIB))

    assert outcome.cut is False
    assert outcome.seconds == pytest.approx(100.0, abs=2.0)
    assert outcome.baseline_bytes == GIB
    assert outcome.peak_bytes == 5 * GIB
    assert outcome.increase_bytes == 4 * GIB
    assert outcome.parts_total == 3
    assert "/generations/generation/cancel" not in client.paths("POST")


def test_a_run_still_going_at_the_limit_is_cancelled_and_reported_cut() -> None:
    client = StandInClient()
    client.generation_seconds = 10_000.0

    outcome = run_one(client, memory_reader(GIB, 2 * GIB), limit=2700.0)

    assert outcome.cut is True
    assert 2700.0 <= outcome.seconds < 2710.0
    assert client.paths("POST")[-1] == "/generations/generation/cancel"
    assert not bench.evaluate_default_model_rule(E4B, [outcome]).passed


def test_a_cut_waits_until_the_generation_has_stopped() -> None:
    client = StandInClient()
    client.generation_seconds = 10_000.0
    client.cancel_stops = False

    with pytest.raises(FullJobError, match="did not stop within 120 s"):
        run_one(client, memory_reader(GIB), limit=10.0)


def test_a_failed_generation_is_an_error_with_its_code_only() -> None:
    client = StandInClient()
    client.generation_status = "failed"

    with pytest.raises(FullJobError, match="ended as failed: GENERATION_FAILED"):
        run_one(client, memory_reader(GIB))


def test_another_configured_model_is_refused_and_cancelled() -> None:
    client = StandInClient()
    client.generation_model = E2B

    with pytest.raises(FullJobError, match="NARRASTEP_GENERATION_MODEL"):
        run_one(client, memory_reader(GIB))
    assert client.paths("POST")[-1] == "/generations/generation/cancel"


# --- whole benchmark ------------------------------------------------------------------------------


def run_all(
    client: StandInClient, tmp_path: Path, **options: Any
) -> tuple[list[bench.RunResult], list[str]]:
    lines: list[str] = []
    results = bench.run_benchmark(
        client,  # type: ignore[arg-type]
        E4B,
        tmp_path,
        emit=lines.append,
        read_memory=memory_reader(GIB, 3 * GIB),
        make_video=write_video,
        poll_seconds=0.0,
        **options,
    )
    return results, lines


def test_each_duration_gets_its_own_project_that_is_deleted(tmp_path: Path) -> None:
    client = StandInClient()
    client.generation_seconds = 0.0

    results, lines = run_all(client, tmp_path)

    assert [item.minutes for item in results] == [10, 60]
    assert client.paths("POST").count("/projects") == 2
    assert client.paths("DELETE") == ["/projects/project", "/projects/project"]
    assert client.paths("POST").count("/projects/project/generations") == 2
    assert not list(tmp_path.glob("*.mp4"))
    assert sum(line.startswith("run ") for line in lines) == 2


def test_progress_lines_hold_no_transcript_text(tmp_path: Path) -> None:
    client = StandInClient()
    client.generation_seconds = 0.0
    client.segments = [{"reviewedText": f"Open the settings page and save. {SECRET_TEXT}"}]

    _, lines = run_all(client, tmp_path, durations=[10])

    assert lines
    assert all(REPORT_LINE.match(line) for line in lines)
    assert SECRET_TEXT not in "\n".join(lines)


def test_a_cut_run_is_returned_not_raised(tmp_path: Path) -> None:
    client = StandInClient()
    client.generation_seconds = 10_000.0

    results, lines = run_all(client, tmp_path, durations=[60], limit_seconds=0.0)

    assert [item.cut for item in results] == [True]
    assert any("outcome=cut" in line for line in lines)


def test_the_project_is_deleted_after_a_failure(tmp_path: Path) -> None:
    client = StandInClient()
    client.generation_seconds = 0.0
    client.generation_status = "failed"

    with pytest.raises(FullJobError):
        run_all(client, tmp_path, durations=[10])

    assert client.paths("DELETE") == ["/projects/project"]


def test_a_project_that_cannot_be_deleted_is_reported_on_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    client = StandInClient()
    client.generation_seconds = 0.0
    client.delete_fails = True

    run_all(client, tmp_path, durations=[10])

    assert "left behind" in capsys.readouterr().err


def test_a_transcript_without_the_spoken_words_stops_the_benchmark(tmp_path: Path) -> None:
    client = StandInClient()
    client.segments = []

    with pytest.raises(FullJobError, match="no transcript segment"):
        run_all(client, tmp_path, durations=[10])
    assert "/projects/project/generations" not in client.paths("POST")


# --- media commands -------------------------------------------------------------------------------


def test_the_loop_command_repeats_the_clip_without_re_encoding(tmp_path: Path) -> None:
    command = bench.loop_command(tmp_path / "clip.mp4", tmp_path / "out.mp4", 60)

    assert command[command.index("-stream_loop") + 1] == "359"
    assert command[command.index("-c") + 1] == "copy"
    assert command[-1] == str(tmp_path / "out.mp4")
    assert "shell" not in command


def test_the_clip_command_adds_only_a_quality_setting(tmp_path: Path) -> None:
    command = bench.clip_command(tmp_path / "clip.mp4")

    assert command[-3:] == ["-crf", bench.CLIP_CRF, str(tmp_path / "clip.mp4")]
    assert any(item.startswith("flite=") for item in command)


def test_the_clip_is_built_once_and_looped_per_duration(tmp_path: Path) -> None:
    commands: list[list[str]] = []

    def run(command: list[str]) -> None:
        commands.append(command)
        (tmp_path / bench.CLIP_NAME).write_bytes(b"clip")

    first = bench.make_synthetic_video(tmp_path, 10, run=run)
    second = bench.make_synthetic_video(tmp_path, 60, run=run)

    assert [first.name, second.name] == ["benchmark-10-minutes.mp4", "benchmark-60-minutes.mp4"]
    assert sum("flite" in " ".join(command) for command in commands) == 1
    assert sum("-stream_loop" in command for command in commands) == 2


def test_a_missing_ffmpeg_is_one_line() -> None:
    with pytest.raises(FullJobError, match="FFmpeg could not run"):
        bench.run_ffmpeg(["narrastep-no-such-ffmpeg", "-version"])


# --- command line ---------------------------------------------------------------------------------


def test_the_model_is_required_and_limited_to_two_names() -> None:
    with pytest.raises(SystemExit):
        bench.parse_arguments([])
    with pytest.raises(SystemExit):
        bench.parse_arguments(["--model", "e9b"])

    assert bench.parse_arguments(["--model", "e4b"]).model == "e4b"


def test_the_time_limit_can_be_lowered_but_not_raised() -> None:
    assert bench.parse_arguments(["--model", "e2b", "--limit-minutes", "5"]).limit_minutes == 5.0
    for value in ("46", "0", "-1"):
        with pytest.raises(SystemExit):
            bench.parse_arguments(["--model", "e2b", "--limit-minutes", value])


def test_durations_default_to_ten_and_sixty_minutes() -> None:
    assert bench.parse_arguments(["--model", "e2b"]).minutes == [10, 60]
    with pytest.raises(SystemExit):
        bench.parse_arguments(["--model", "e2b", "--minutes", "0"])


def test_main_prints_the_verdict_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: dict[str, Any] = {}

    def fake_benchmark(*args: Any, **kwargs: Any) -> Sequence[bench.RunResult]:
        seen.update(kwargs)
        return [result(minutes=60, seconds=900.0)]

    monkeypatch.setattr(bench, "check_health", lambda client: {})
    monkeypatch.setattr(bench, "read_memory_in_use_bytes", lambda: GIB)
    monkeypatch.setattr(bench, "ensure_model_enabled", lambda *a, **k: None)
    monkeypatch.setattr(bench, "run_benchmark", fake_benchmark)

    code = bench.main(["--model", "e4b", "--api", "http://127.0.0.1:8000/api/v1"])

    out = capsys.readouterr().out
    assert code == 0
    assert "verdict model=gemma-4-e4b e4b_passes=yes" in out
    assert seen["limit_seconds"] == 2700.0


def test_main_reports_a_failure_on_stderr_and_returns_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(bench, "check_health", lambda client: {})

    def unreadable() -> int:
        raise FullJobError("Memory use cannot be read.")

    monkeypatch.setattr(bench, "read_memory_in_use_bytes", unreadable)

    code = bench.main(["--model", "e2b"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.err == "Generation benchmark failed: Memory use cannot be read.\n"
    assert captured.out == ""


def test_main_rejects_an_api_address_that_is_not_http(capsys: pytest.CaptureFixture[str]) -> None:
    assert bench.main(["--model", "e2b", "--api", "ftp://example"]) == 1
    assert "--api must be an http or https URL" in capsys.readouterr().err
