"""Tests for `scripts/check_transcription_stack.py`, the image-build transcription check (P08.1).

These tests replace FFmpeg and Whisper with stand-ins, so they need neither a model nor media
tools. The real check runs in the image build, and `tests/integration/test_transcription_stack.py`
runs real media through the real model when opted in.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.media import SUPPORTED_VIDEO_EXTENSIONS
from app.services.pipeline import PipelineServiceError
from scripts import check_transcription_stack as stack


def test_build_clips_cover_every_container_with_varied_audio() -> None:
    clips = stack.BUILD_CLIPS

    assert {clip.extension for clip in clips} == SUPPORTED_VIDEO_EXTENSIONS
    assert {1, 2}.issubset({clip.channels for clip in clips})
    assert any(clip.channels > 2 for clip in clips)
    assert any(clip.sample_rate_hz not in (16_000, 44_100, 48_000) for clip in clips)
    assert any(not clip.speech for clip in clips)


def test_clip_command_writes_the_requested_container_codec_rate_and_layout(tmp_path: Path) -> None:
    spec = stack.ClipSpec("mkv-6ch", ".mkv", stack.H264, ("-c:a", "flac"), 22_050, 6)

    command = stack.clip_command(spec, tmp_path / "clip.mkv", "/usr/bin/ffmpeg")

    assert command[0] == "/usr/bin/ffmpeg" and command[-1] == str(tmp_path / "clip.mkv")
    assert any(part.startswith("flite=text='Open the settings page") for part in command)
    assert "aresample=22050,aformat=channel_layouts=5.1" in command[command.index("-filter:a") + 1]
    assert command[command.index("-ar") + 1] == "22050"
    assert command[command.index("-ac") + 1] == "6"
    assert command[command.index("-c:a") + 1] == "flac"


def test_silent_clips_use_digital_silence_and_odd_layouts_are_refused(tmp_path: Path) -> None:
    silent = stack.ClipSpec("silent", ".mp4", stack.H264, ("-c:a", "aac"), 48_000, 2, False)
    odd = stack.ClipSpec("odd", ".mp4", stack.H264, ("-c:a", "aac"), 48_000, 3)

    assert "anullsrc=r=48000:cl=stereo" in stack.clip_command(silent, tmp_path / "s.mp4")
    with pytest.raises(ValueError, match="channel count 3"):
        stack.clip_command(odd, tmp_path / "o.mp4")


def test_missing_words_ignores_case_and_punctuation() -> None:
    assert stack.missing_words("Open the Settings page, and SAVE.") == []
    assert stack.missing_words("Open the page.") == ["settings", "save"]
    assert stack.missing_words("") == ["settings", "save"]


def test_failure_description_names_the_package_error_behind_the_public_message() -> None:
    try:
        try:
            raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
        except TypeError as cause:
            raise PipelineServiceError("TRANSCRIPTION_MODEL_FAILED", "Not transcribed.") from cause
    except PipelineServiceError as error:
        description = stack.describe_failure(error)

    assert description == (
        "PipelineServiceError: Not transcribed. <- "
        "TypeError: open() got an unexpected keyword argument 'metadata_errors'"
    )


def test_package_versions_report_missing_distributions() -> None:
    versions = stack.package_versions(("numpy", "narrastep-package-that-does-not-exist"))

    assert versions["numpy"] != "missing"
    assert versions["narrastep-package-that-does-not-exist"] == "missing"


def test_main_fails_without_a_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = stack.main(["--model-path", str(tmp_path), "--ffmpeg", str(tmp_path / "no-ffmpeg")])

    output = capsys.readouterr()
    assert code == 1
    assert "FFmpeg: unavailable" in output.out
    assert f"No faster-whisper model was found in {tmp_path}" in output.err


@pytest.fixture
def stand_ins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Replace FFmpeg, FFprobe and Whisper in the check and return the transcription outcome.

    Set `outcome["text"]` to the text Whisper returns, or `outcome["error"]` to the exception it
    raises. A model file is created so the model-folder check passes.
    """

    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "model.bin").write_bytes(b"stand-in")
    outcome: dict[str, object] = {"text": "Open the settings page and save the form."}

    def generate(spec: stack.ClipSpec, directory: Path, ffmpeg: str = "ffmpeg") -> Path:
        clip = directory / f"{spec.name}{spec.extension}"
        clip.write_bytes(b"clip")
        return clip

    def transcribe(*_: object, **__: object) -> object:
        error = outcome.get("error")
        if isinstance(error, BaseException):
            raise error
        segments = [SimpleNamespace(text=outcome["text"])] if outcome["text"] else []
        return SimpleNamespace(language="en", segments=segments)

    monkeypatch.setattr(stack, "generate_clip", generate)
    metadata = SimpleNamespace(duration_seconds=4)
    monkeypatch.setattr(stack, "validate_video", lambda *_, **__: metadata)
    monkeypatch.setattr(stack, "extract_pcm_audio", lambda _, output, **__: output)
    monkeypatch.setattr(stack, "transcribe_audio", transcribe)
    return outcome


def test_check_passes_when_every_clip_transcribes(
    tmp_path: Path, stand_ins: dict[str, object]
) -> None:
    results = stack.check_transcription_stack(tmp_path / "model", tmp_path / "work")

    assert [result.name for result in results] == [clip.name for clip in stack.BUILD_CLIPS]
    assert all(result.segment_count == 1 for result in results)


def test_check_fails_with_the_cause_when_the_package_set_is_broken(
    tmp_path: Path, stand_ins: dict[str, object]
) -> None:
    cause = TypeError("open() got an unexpected keyword argument 'metadata_errors'")
    error = PipelineServiceError("TRANSCRIPTION_MODEL_FAILED", "Not transcribed.")
    error.__cause__ = cause
    stand_ins["error"] = error

    with pytest.raises(stack.StackCheckError) as captured:
        stack.check_transcription_stack(tmp_path / "model", tmp_path / "work")

    message = str(captured.value)
    assert message.startswith(f"{stack.BUILD_CLIPS[0].name}: TRANSCRIPTION_MODEL_FAILED")
    assert "TypeError: open() got an unexpected keyword argument 'metadata_errors'" in message


def test_check_fails_when_speech_is_not_heard_but_accepts_an_empty_silent_clip(
    tmp_path: Path, stand_ins: dict[str, object]
) -> None:
    silent = [clip for clip in stack.BUILD_CLIPS if not clip.speech]
    speech = [clip for clip in stack.BUILD_CLIPS if clip.speech][:1]
    stand_ins["text"] = ""

    results = stack.check_transcription_stack(tmp_path / "model", tmp_path / "w", clips=silent)
    assert [result.segment_count for result in results] == [0]
    with pytest.raises(stack.StackCheckError, match="lacks settings, save"):
        stack.check_transcription_stack(tmp_path / "model", tmp_path / "w", clips=speech)
