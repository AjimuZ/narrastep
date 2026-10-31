"""Time frame extraction on generated 2560x1440 recordings, for the figures in docs/STATUS.md."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from app.services.adaptive_selection import scan_interest_windows
from app.services.pipeline import (
    PipelineServiceError,
    TranscriptSegment,
    extract_deduplicated_frames_to_directory,
    generate_frame_times,
)
from app.services.screenshot_materialization import decode_exact_frame

WIDTH = 2560
HEIGHT = 1440
DURATION_SECONDS = 60
# Bursts of movement at about 30 fps between still moments average about 12 fps, as a screen
# recording does, and the rare keyframes make every wrong seek pay for a long decode.
BURST_FRAMES = 45
BURST_INTERVAL_SECONDS = 0.03
BURST_GAP_SECONDS = 2.2
CONSTANT_RATE_FPS = 30
SEGMENT_SECONDS = 5


def _write_frames(process: subprocess.Popen[bytes], frame_count: int) -> None:
    """Send one synthetic screen per frame: a still page with a moving block, then close the pipe.

    Requires a process reading raw BGR frames on stdin.
    """
    base = np.full((HEIGHT, WIDTH, 3), 235, np.uint8)
    for row in range(120, HEIGHT - 80, 48):
        base[row : row + 18, 160 : WIDTH - 400 - (row % 700)] = 70
    stdin = process.stdin
    assert stdin is not None
    for index in range(frame_count):
        frame = base.copy()
        left = (index * 37) % (WIDTH - 400)
        top = (index * 23) % (HEIGHT - 300)
        frame[top : top + 300, left : left + 400] = (index % 200 + 30, 90, 200 - index % 150)
        stdin.write(frame.tobytes())
    stdin.close()


def _encode(
    destination: Path, frame_count: int, input_rate: int, codec_arguments: list[str]
) -> None:
    """Encode synthetic frames with FFmpeg, or raise CalledProcessError if FFmpeg fails."""
    command = [
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
        *codec_arguments,
        str(destination),
    ]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    _write_frames(process, frame_count)
    if process.wait() != 0:
        raise subprocess.CalledProcessError(process.returncode, command)


def generate_variable_rate_recording(destination: Path) -> None:
    """Write a variable-rate VP9 WebM of at least 60 seconds with one keyframe at the start."""
    cycle = (BURST_FRAMES - 1) * BURST_INTERVAL_SECONDS + BURST_GAP_SECONDS
    frame_count = (math.ceil(DURATION_SECONDS / cycle) + 1) * BURST_FRAMES
    # A declared input rate of 1000 and this exact millisecond jitter make FFprobe report
    # r_frame_rate 1000/1 and no avg_frame_rate, as for a real recording: OpenCV then seeks by 1000
    # fps. Smaller changes to the jitter report rates such as 57/1, which do not show the fault.
    timestamps = (
        f"(floor(N/{BURST_FRAMES})*{cycle}+mod(N,{BURST_FRAMES})*{BURST_INTERVAL_SECONDS}"
        "+mod(N*7,23)*0.001)/TB"
    )
    _encode(
        destination,
        frame_count,
        1000,
        [
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
            "-row-mt",
            "1",
            "-b:v",
            "4M",
            "-g",
            "100000",
            "-pix_fmt",
            "yuv420p",
        ],
    )


def generate_constant_rate_video(destination: Path) -> None:
    """Write a constant-rate H.264 MP4 of 60 seconds with the encoder's default keyframes."""
    _encode(
        destination,
        DURATION_SECONDS * CONSTANT_RATE_FPS,
        CONSTANT_RATE_FPS,
        ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"],
    )


def probe_video(path: Path) -> dict[str, object]:
    """Return the rates, duration and frame count FFprobe reports for the first video stream."""
    output = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_packets",
            "-show_entries",
            "stream=r_frame_rate,avg_frame_rate,nb_read_packets:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=300,
    ).stdout
    payload = json.loads(output)
    stream = payload["streams"][0]
    return {
        "rFrameRate": stream["r_frame_rate"],
        "avgFrameRate": stream["avg_frame_rate"],
        "frames": int(stream["nb_read_packets"]),
        "durationSeconds": float(payload["format"]["duration"]),
    }


def _segments(duration_seconds: float) -> Sequence[TranscriptSegment]:
    """Build evenly spaced transcript segments, which add anchors as a real transcript does."""
    return [
        TranscriptSegment(
            source_index=index,
            start_seconds=float(start),
            end_seconds=float(min(start + SEGMENT_SECONDS, duration_seconds)),
            text="",
            confidence=None,
            words=(),
        )
        for index, start in enumerate(range(0, int(duration_seconds), SEGMENT_SECONDS))
    ]


def measure(video: Path) -> dict[str, object]:
    """Time extraction, the adaptive scan and one late exact read of one video."""
    report = probe_video(video)
    duration = float(str(report["durationSeconds"]))
    times = generate_frame_times(duration, _segments(duration))
    decoded: list[int] = []
    with tempfile.TemporaryDirectory(prefix="narrastep-frame-benchmark-") as staging:
        started_at = time.perf_counter()
        retained = extract_deduplicated_frames_to_directory(
            video, times, Path(staging), decoded_frame_callback=decoded.append
        )
        report["extractionSeconds"] = round(time.perf_counter() - started_at, 2)
    report["anchors"] = len(times)
    report["decodedSamples"] = sum(decoded)
    report["anchorsWithoutFrames"] = sum(count == 0 for count in decoded)
    report["retainedCandidates"] = len(retained)

    started_at = time.perf_counter()
    _, scanned = scan_interest_windows(video, duration, 1.0)
    report["scanSeconds"] = round(time.perf_counter() - started_at, 2)
    report["scanDecoded"] = scanned

    started_at = time.perf_counter()
    try:
        decode_exact_frame(video, duration * 0.9)
        report["exactFrameDecoded"] = True
    except PipelineServiceError:
        report["exactFrameDecoded"] = False
    report["exactFrameSeconds"] = round(time.perf_counter() - started_at, 2)
    return report


def main() -> int:
    """Generate the two videos when missing, measure both, and print a JSON report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media_directory", type=Path, help="Keeps the generated videos.")
    arguments = parser.parse_args()
    media: Path = arguments.media_directory
    media.mkdir(parents=True, exist_ok=True)
    recording = media / "variable-rate.webm"
    constant = media / "constant-rate.mp4"
    if not recording.is_file():
        generate_variable_rate_recording(recording)
    if not constant.is_file():
        generate_constant_rate_video(constant)
    report = {"variableRateWebm": measure(recording), "constantRateMp4": measure(constant)}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
