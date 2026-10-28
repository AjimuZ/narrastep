"""Opt-in end-to-end test for the actual local media and Whisper pipeline."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.config import get_settings
from app.services.media import extract_pcm_audio, validate_video
from app.services.pipeline import (
    extract_deduplicated_frames_to_directory,
    generate_frame_times,
    transcribe_audio,
)


def _required_command(name: str) -> str:
    """Resolve one local integration prerequisite or skip with an actionable reason."""
    command = shutil.which(name)
    if command is None:
        pytest.skip(f"Real pipeline test requires '{name}' on PATH.")
    return command


@pytest.mark.integration
def test_real_local_transcription_and_frame_pipeline(tmp_path: Path) -> None:
    """Generate legal speech/video locally and exercise FFmpeg, Whisper, and frame staging."""
    if os.environ.get("NARRASTEP_RUN_REAL_PIPELINE_TESTS") != "1":
        pytest.skip("Set NARRASTEP_RUN_REAL_PIPELINE_TESTS=1 to run local model integration.")
    ffmpeg = _required_command("ffmpeg")
    synthesizer = shutil.which("espeak-ng") or shutil.which("espeak")
    if synthesizer is None:
        pytest.skip("Real pipeline test requires espeak-ng or espeak on PATH.")
    configured_model = os.environ.get("NARRASTEP_REAL_WHISPER_MODEL_PATH")
    model_path = Path(configured_model) if configured_model else get_settings().whisper_model_path
    if not model_path.is_dir():
        pytest.skip(
            "Download a local model and set NARRASTEP_REAL_WHISPER_MODEL_PATH before this test."
        )

    speech = tmp_path / "speech.wav"
    video = tmp_path / "spoken-fixture.mp4"
    subprocess.run(
        [synthesizer, "-w", str(speech), "Open settings and save the form."],
        check=True,
        capture_output=True,
        timeout=30,
    )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x180:d=4",
            "-i",
            str(speech),
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(video),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    settings = get_settings().model_copy(
        update={
            "mock_processing": False,
            "whisper_model_path": model_path,
            "ffmpeg_path": ffmpeg,
            "ffprobe_path": _required_command("ffprobe"),
        }
    )
    metadata = validate_video(video, config=settings)
    audio = extract_pcm_audio(video, tmp_path / "normalized.wav", config=settings)
    transcription = transcribe_audio(
        audio,
        config=settings,
        source_language="en",
        duration_seconds=metadata.duration_seconds,
    )
    assert transcription.segments
    assert any(segment.text.strip() for segment in transcription.segments)
    assert any(segment.words for segment in transcription.segments)
    assert all(
        word.start_seconds <= word.end_seconds
        for segment in transcription.segments
        for word in segment.words
    )

    frame_times = generate_frame_times(metadata.duration_seconds, transcription.segments)
    screenshots = extract_deduplicated_frames_to_directory(
        video, frame_times, tmp_path / "screenshots", config=settings
    )
    assert screenshots and all(item.image_path.is_file() for item in screenshots)
