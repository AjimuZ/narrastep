"""Time-based frame reading on generated videos, where each frame shows its number as a barcode.

The videos are synthesized with FFmpeg at test time and identified by what their pixels show, never
by the position a decoder reports. They are skipped when FFmpeg or FFprobe is not on `PATH`; no
binary fixture is stored in the repository.
"""

from __future__ import annotations

import bisect
import json
import shutil
import subprocess
import tracemalloc
import weakref
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

from app.services import adaptive_selection, frame_reader
from app.services.adaptive_selection import (
    InterestWindow,
    extract_fine_candidates,
    scan_interest_windows,
)
from app.services.frame_reader import ForwardFrameReader, read_frame_at, read_frame_bursts
from app.services.pipeline import (
    PipelineServiceError,
    extract_deduplicated_frames_to_directory,
    extract_frame_candidates,
    generate_frame_times,
    sample_neighboring_frames,
)
from app.services.screenshot_materialization import decode_exact_frame

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg and FFprobe are not on PATH.")

WIDTH = 320
HEIGHT = 180
BARCODE_BITS = 12
FRAME_BYTES = WIDTH * HEIGHT * 3
# Same timestamp shape as scripts/benchmark_frame_reading.py: bursts of about 30 fps with a
# two-second still moment after each, with the jitter that makes FFprobe report 1000/1.
RECORDING_TIMESTAMPS = "(floor(N/45)*3.52+mod(N,45)*0.03+mod(N*7,23)*0.001)/TB"
# Bursts at exactly the nominal 30 fps: the first frames look constant-rate, the file is not.
REGULAR_BURST_TIMESTAMPS = "(floor(N/45)*3.5+mod(N,45)/30)/TB"


@dataclass(frozen=True)
class GeneratedVideo:
    """Hold a generated file with the timestamp FFprobe reports for each numbered frame."""

    path: Path
    timestamps: list[float]
    duration_seconds: float

    def expected_frame(self, time_seconds: float) -> int:
        """Return the number of the frame on screen at a time inside the video."""
        return max(0, bisect.bisect_right(self.timestamps, time_seconds) - 1)


def _barcode_frame(number: int) -> np.ndarray:
    """Draw a frame number as black and white cells in Gray code on a still gray picture.

    Gray code flips one cell per frame: a large change would make the encoder add keyframes.
    """
    frame = np.full((HEIGHT, WIDTH, 3), 128, np.uint8)
    cell_width = WIDTH // BARCODE_BITS
    gray_code = number ^ (number >> 1)
    for bit in range(BARCODE_BITS):
        value = 255 if gray_code >> bit & 1 else 0
        frame[60:120, bit * cell_width : (bit + 1) * cell_width] = value
    return frame


def frame_number(frame: np.ndarray) -> int:
    """Read the number a frame shows, from the center of each barcode cell."""
    cell_width = WIDTH // BARCODE_BITS
    gray_code = 0
    for bit in range(BARCODE_BITS):
        center = frame[80:100, bit * cell_width + cell_width // 2]
        if center.mean() > 128:
            gray_code |= 1 << bit
    number = 0
    while gray_code:
        number ^= gray_code
        gray_code >>= 1
    return number


def _generate(destination: Path, frame_count: int, input_rate: int, output: list[str]) -> None:
    """Pipe numbered raw frames into FFmpeg, or fail the calling fixture."""
    frames = b"".join(_barcode_frame(number).tobytes() for number in range(frame_count))
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{WIDTH}x{HEIGHT}",
            "-r",
            str(input_rate),
            "-i",
            "pipe:0",
            *output,
            str(destination),
        ],
        input=frames,
        check=True,
        timeout=180,
    )


def _probe(path: Path) -> dict[str, Any]:
    """Return FFprobe's stream and format fields with every packet timestamp."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=r_frame_rate,avg_frame_rate:format=duration:packet=pts_time,flags",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


def _described(path: Path, frame_count: int) -> GeneratedVideo:
    """Build the expectation for a generated file; every piped frame must have reached it."""
    probe = _probe(path)
    timestamps = sorted(float(packet["pts_time"]) for packet in probe["packets"])
    assert len(timestamps) == frame_count
    return GeneratedVideo(path, timestamps, float(probe["format"]["duration"]))


def _vp9_output(timestamps: str) -> list[str]:
    """Return FFmpeg output options for a variable-rate VP9 WebM with one keyframe at the start."""
    return [
        "-vf",
        f"settb=1/1000,setpts='{timestamps}'",
        "-fps_mode",
        "vfr",
        "-c:v",
        "libvpx-vp9",
        "-deadline",
        "realtime",
        "-cpu-used",
        "8",
        "-b:v",
        "1M",
        "-g",
        "100000",
        "-pix_fmt",
        "yuv420p",
    ]


@pytest.fixture(scope="module")
def recording(tmp_path_factory: pytest.TempPathFactory) -> GeneratedVideo:
    """Generate a 1000/1 variable-rate WebM of about 26 seconds, proved sparse and variable."""
    path = tmp_path_factory.mktemp("recording") / "recording.webm"
    frame_count = 360
    _generate(path, frame_count, 1000, _vp9_output(RECORDING_TIMESTAMPS))
    probe = _probe(path)
    stream = probe["streams"][0]
    assert stream["r_frame_rate"] == "1000/1"
    assert stream["r_frame_rate"] != stream["avg_frame_rate"]
    keyframes = sum(packet["flags"].startswith("K") for packet in probe["packets"])
    assert keyframes == 1
    return _described(path, frame_count)


@pytest.fixture(scope="module")
def constant_rate(tmp_path_factory: pytest.TempPathFactory) -> GeneratedVideo:
    """Generate a 30 fps H.264 MP4 of 20 seconds with B-frames and the encoder's keyframes."""
    path = tmp_path_factory.mktemp("constant") / "constant.mp4"
    frame_count = 600
    _generate(
        path,
        frame_count,
        30,
        ["-c:v", "libx264", "-preset", "veryfast", "-qp", "18", "-pix_fmt", "yuv420p"],
    )
    return _described(path, frame_count)


@pytest.fixture(params=["recording", "constant_rate"])
def video(request: pytest.FixtureRequest) -> GeneratedVideo:
    """Run a test on the variable-rate recording and on the constant-rate video."""
    generated: GeneratedVideo = request.getfixturevalue(request.param)
    return generated


def _times_inside(video: GeneratedVideo, step: float) -> list[float]:
    """Return evenly spaced times inside a video, away from frame timestamp edges."""
    count = int(video.duration_seconds / step)
    return [round(index * step + 0.0137, 4) for index in range(count)]


class _CountingCapture:
    """Wrap a real capture, counting seeks and tracking which decoded frames are still alive."""

    seeks: list[float] = []
    alive: list[weakref.ref[np.ndarray]] = []

    def __init__(self, path: str) -> None:
        """Open the real decoder through the original class."""
        self._capture = _CountingCapture.original(path)

    original: Callable[[str], Any] = cv2.VideoCapture

    def set(self, prop: int, value: float) -> bool:
        """Record a seek and forward it."""
        _CountingCapture.seeks.append(value)
        return bool(self._capture.set(prop, value))

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        """Forward a retrieve and remember the frame without keeping it alive."""
        success, image = self._capture.retrieve()
        if image is not None:
            _CountingCapture.alive.append(weakref.ref(image))
        return success, image

    def __getattr__(self, name: str) -> Any:
        """Forward everything else to the real capture."""
        return getattr(self._capture, name)


@pytest.fixture
def counted(monkeypatch: pytest.MonkeyPatch) -> Iterator[type[_CountingCapture]]:
    """Make every capture opened by the code under test a counting one."""
    _CountingCapture.seeks = []
    _CountingCapture.alive = []
    monkeypatch.setattr(cv2, "VideoCapture", _CountingCapture)
    yield _CountingCapture


@needs_ffmpeg
def test_forward_reader_returns_the_frame_on_screen_at_each_time(video: GeneratedVideo) -> None:
    """Find the right frame in bursts and in still moments, identified by its own pixels."""
    capture = cv2.VideoCapture(str(video.path))
    try:
        reader = ForwardFrameReader(capture)
        for time in _times_inside(video, 0.21):
            frame = reader.frame_at(time)
            assert frame is not None, f"no frame at {time}"
            assert frame_number(frame) == video.expected_frame(time), f"wrong frame at {time}"
    finally:
        capture.release()


@needs_ffmpeg
def test_legacy_extraction_reads_every_sample_at_its_time(
    video: GeneratedVideo, tmp_path: Path
) -> None:
    """Decode all samples inside the duration and return the frame of each candidate's time."""
    times = generate_frame_times(video.duration_seconds, interval_seconds=1.5)
    decoded: list[int] = []
    retained = extract_deduplicated_frames_to_directory(
        video.path,
        times,
        tmp_path / "staged",
        decoded_frame_callback=decoded.append,
        # Every frame must come through: this test checks reading and not the deduplication.
        neighbor_offset_seconds=0.2,
    )
    expected_samples = sum(
        len({max(0.0, time - 0.2), time, time + 0.2}) - (time + 0.2 > video.duration_seconds)
        for time in times
    )
    assert sum(decoded) == expected_samples
    assert retained
    for candidate in retained:
        image = cv2.imread(str(candidate.image_path), cv2.IMREAD_COLOR)
        assert frame_number(image) == video.expected_frame(candidate.timestamp_seconds)

    candidates = extract_frame_candidates(video.path, times, neighbor_offset_seconds=0.2)
    assert len(candidates) == len(times)
    for candidate in candidates:
        assert frame_number(candidate.frame) == video.expected_frame(candidate.timestamp_seconds)


@needs_ffmpeg
def test_neighboring_frames_are_the_frames_of_their_times(video: GeneratedVideo) -> None:
    """Return the minus, exact and plus samples of one anchor in the middle of the video."""
    anchor = video.duration_seconds / 2 + 0.0137
    samples = sample_neighboring_frames(video.path, anchor, neighbor_offset_seconds=0.2)
    assert len(samples) == 3
    for sample in samples:
        assert frame_number(sample.frame) == video.expected_frame(sample.timestamp_seconds)


@needs_ffmpeg
def test_adaptive_scan_reads_every_sample_at_its_time(
    video: GeneratedVideo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """See each scanned picture before it is reduced, and require one per scan time."""
    seen: list[int] = []
    resize = adaptive_selection._resize_grayscale

    def spy(frame: np.ndarray, long_edge: int) -> np.ndarray:
        """Identify the full frame and continue with the real reduction."""
        seen.append(frame_number(frame))
        return resize(frame, long_edge)

    monkeypatch.setattr(adaptive_selection, "_resize_grayscale", spy)
    _, decoded = scan_interest_windows(video.path, video.duration_seconds, 1.0)
    expected_count = int(video.duration_seconds) + 1
    assert decoded == expected_count
    assert seen == [video.expected_frame(float(index)) for index in range(expected_count)]


@needs_ffmpeg
def test_adaptive_fine_candidates_read_overlapping_bursts(video: GeneratedVideo) -> None:
    """Read anchors 0.1 seconds apart, whose bursts overlap, with no sample lost or out of place."""
    anchors = [round(5.0137 + index * 0.1, 4) for index in range(8)]
    window = InterestWindow(5.0, 5.3, 6.0, 0.5)
    candidates = extract_fine_candidates(video.path, anchors, [window])
    assert len(candidates) == len(anchors)
    assert all(candidate.decoded_frame_count == 3 for candidate in candidates)
    for candidate in candidates:
        chosen = candidate.frame_candidate
        assert frame_number(chosen.frame) == video.expected_frame(chosen.timestamp_seconds)


@needs_ffmpeg
def test_overlapping_bursts_each_get_all_their_right_frames(video: GeneratedVideo) -> None:
    """Give every burst its three frames although its times interleave with its neighbors'."""
    anchors = [0.0137, 0.1137, 0.2137, 3.0137, 3.0737, 12.5137]
    capture = cv2.VideoCapture(str(video.path))
    try:
        bursts = list(read_frame_bursts(capture, anchors, 0.2))
    finally:
        capture.release()
    assert [len(burst) for burst in bursts] == [3, 3, 3, 3, 3, 3]
    for anchor, burst in zip(anchors, bursts, strict=True):
        assert anchor in {sample.timestamp_seconds for sample in burst}
        for sample in burst:
            assert frame_number(sample.frame) == video.expected_frame(sample.timestamp_seconds)


@needs_ffmpeg
def test_exact_frame_is_the_frame_on_screen(video: GeneratedVideo) -> None:
    """Read single times, early, late and past the seek distance, in any order."""
    times = [12.0137, 0.0137, 1.5137, video.duration_seconds - 0.5, 5.2137, 15.7137]
    for time in times:
        assert frame_number(decode_exact_frame(video.path, time)) == video.expected_frame(time)


@needs_ffmpeg
def test_exact_frame_never_seeks_on_a_recording(
    recording: GeneratedVideo, counted: type[_CountingCapture]
) -> None:
    """Keep the 1000/1 file away from the seek that decodes it and lands in the wrong place."""
    time = recording.duration_seconds - 0.5
    frame = decode_exact_frame(recording.path, time)
    assert frame_number(frame) == recording.expected_frame(time)
    assert counted.seeks == []


@needs_ffmpeg
def test_exact_frame_seeks_on_a_constant_rate_video(
    constant_rate: GeneratedVideo, counted: type[_CountingCapture]
) -> None:
    """Keep a late read of a constant-rate video from decoding it from the start."""
    time = constant_rate.duration_seconds - 0.5
    frame = decode_exact_frame(constant_rate.path, time)
    assert frame_number(frame) == constant_rate.expected_frame(time)
    assert len(counted.seeks) == 1
    assert len(counted.alive) <= 40


@needs_ffmpeg
def test_exact_frame_recovers_when_a_seek_passes_the_time(
    tmp_path: Path, counted: type[_CountingCapture]
) -> None:
    """Notice by the timestamp that a seek on regular bursts overshot, and walk from the start."""
    path = tmp_path / "regular-bursts.webm"
    frame_count = 225
    _generate(path, frame_count, 30, _vp9_output(REGULAR_BURST_TIMESTAMPS))
    video = _described(path, frame_count)
    time = 12.0137
    frame = decode_exact_frame(video.path, time)
    assert len(counted.seeks) == 1
    assert frame_number(frame) == video.expected_frame(time)


@needs_ffmpeg
def test_times_past_the_end_are_absent_and_the_last_frame_lasts_until_it(
    recording: GeneratedVideo, constant_rate: GeneratedVideo
) -> None:
    """Hold the last frame to the end of the video and refuse a time past it."""
    for video in (recording, constant_rate):
        last = len(video.timestamps) - 1
        end = decode_exact_frame(video.path, video.duration_seconds)
        assert frame_number(end) == last
        assert read_frame_at(video.path, video.duration_seconds + 0.2) is None
        with pytest.raises(PipelineServiceError) as captured:
            decode_exact_frame(video.path, video.duration_seconds + 0.2)
        assert captured.value.code == "FRAME_EXTRACTION_FAILED"


@needs_ffmpeg
def test_image_memory_stays_bounded_during_extraction(
    constant_rate: GeneratedVideo, tmp_path: Path, counted: type[_CountingCapture]
) -> None:
    """Keep a few frames in memory while every frame of the video is decoded and dropped."""
    times = generate_frame_times(constant_rate.duration_seconds, interval_seconds=0.5)
    tracemalloc.start()
    try:
        retained = extract_deduplicated_frames_to_directory(constant_rate.path, times, tmp_path)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert retained
    assert len(counted.alive) > 500
    assert peak < 15 * FRAME_BYTES, f"peak {peak} bytes"


@needs_ffmpeg
def test_dense_bursts_release_frames_they_no_longer_need(
    constant_rate: GeneratedVideo, counted: type[_CountingCapture]
) -> None:
    """Bound live frames by the burst window, even with anchors closer together than the window."""
    anchors = [round(index * 0.1, 3) for index in range(60)]
    capture = cv2.VideoCapture(str(constant_rate.path))
    highest = 0
    try:
        for burst in read_frame_bursts(capture, anchors, 0.2):
            assert burst
            highest = max(highest, sum(ref() is not None for ref in counted.alive))
            del burst
    finally:
        capture.release()
    # Sample times fall in a window of 0.4 s around each anchor, every 0.1 s, plus the reader's own.
    assert len(counted.alive) > 150
    assert highest <= 8


def test_unopenable_video_raises_a_stable_error(tmp_path: Path) -> None:
    """Report a file that is not a video with the error callers already translate."""
    broken = tmp_path / "broken.webm"
    broken.write_bytes(b"not a video")
    with pytest.raises(frame_reader.VideoOpenError):
        read_frame_at(broken, 1.0)
    with pytest.raises(PipelineServiceError) as captured:
        decode_exact_frame(broken, 1.0)
    assert captured.value.code == "FRAME_EXTRACTION_FAILED"


class _ScriptedCapture:
    """Serve frames from a list of reported timestamps in milliseconds, numbering each picture."""

    def __init__(self, reported: list[float], rate: float, frame_count: float) -> None:
        """Keep the script and the nominal values OpenCV would report."""
        self._reported = reported
        self._index = -1
        self._rate = rate
        self._frame_count = frame_count
        self.retrieved = 0

    def grab(self) -> bool:
        """Advance to the next scripted frame while one is left."""
        self._index += 1
        return self._index < len(self._reported)

    def retrieve(self) -> tuple[bool, np.ndarray]:
        """Return a small picture whose first value is the frame's position."""
        self.retrieved += 1
        return True, np.full((2, 2, 3), self._index, np.uint8)

    def get(self, prop: int) -> float:
        """Report the scripted timestamp, like OpenCV after a grab, or the nominal values."""
        if prop == cv2.CAP_PROP_POS_MSEC:
            return self._reported[self._index] if self._index < len(self._reported) else 0.0
        return self._rate if prop == cv2.CAP_PROP_FPS else self._frame_count


def _position(frame: np.ndarray | None) -> int:
    """Return the position a scripted picture carries."""
    assert frame is not None
    return int(frame[0, 0, 0])


def test_going_backward_is_refused() -> None:
    """Reject a lower time and a non-numeric time instead of returning a later frame."""
    reader = ForwardFrameReader(_ScriptedCapture([0.0, 100.0, 200.0], 10.0, 3.0))
    reader.frame_at(0.15)
    with pytest.raises(ValueError):
        reader.frame_at(0.05)
    with pytest.raises(ValueError):
        reader.frame_at(float("nan"))


def test_descending_anchors_are_refused() -> None:
    """Reject a burst plan the forward pass could not serve."""
    capture = _ScriptedCapture([0.0, 100.0], 10.0, 2.0)
    with pytest.raises(ValueError):
        list(read_frame_bursts(capture, [1.0, 0.5], 0.2))


def test_a_repeated_time_returns_the_same_frame_without_decoding_again() -> None:
    """Serve requests inside one frame's time from the frame already held."""
    capture = _ScriptedCapture([0.0, 100.0, 5_000.0], 10.0, 50.0)
    reader = ForwardFrameReader(capture)
    first = reader.frame_at(1.0)
    second = reader.frame_at(2.0)
    assert first is second
    assert _position(first) == 1
    assert capture.retrieved == 2


def test_a_file_without_timestamps_advances_by_the_nominal_rate() -> None:
    """Keep time moving when the decoder reports zero for every frame."""
    reader = ForwardFrameReader(_ScriptedCapture([0.0, 0.0, 0.0, 0.0], 10.0, 4.0))
    assert _position(reader.frame_at(0.0)) == 0
    assert _position(reader.frame_at(0.25)) == 2
    assert _position(reader.frame_at(0.39)) == 3


def test_an_empty_video_has_no_frame() -> None:
    """Return nothing at any time when the video has no frame at all."""
    reader = ForwardFrameReader(_ScriptedCapture([], 10.0, 0.0))
    assert reader.frame_at(0.0) is None


def test_a_time_before_the_first_frame_gets_the_first_frame() -> None:
    """Show the first picture for a moment before its timestamp."""
    reader = ForwardFrameReader(_ScriptedCapture([500.0, 900.0], 10.0, 10.0))
    assert _position(reader.frame_at(0.1)) == 0
    assert _position(reader.frame_at(0.6)) == 0
    assert _position(reader.frame_at(0.95)) == 1


def test_an_unknown_duration_ends_at_the_last_frame() -> None:
    """Refuse times past the last frame when the file reports no duration."""
    reader = ForwardFrameReader(_ScriptedCapture([0.0, 100.0], 0.0, -1.0))
    assert _position(reader.frame_at(0.1)) == 1
    assert reader.frame_at(0.5) is None
