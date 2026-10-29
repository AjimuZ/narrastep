"""Opt-in media matrix: real FFmpeg extraction and real Whisper transcription (P08.1).

Every accepted container is generated with silent, mono, multichannel and unusual-rate audio and
goes through `validate_video`, `extract_pcm_audio` and `transcribe_audio` with a local model, the
path a processing job takes. Like the real pipeline test, it runs only with
`NARRASTEP_RUN_REAL_PIPELINE_TESTS=1` and a model folder in `NARRASTEP_REAL_WHISPER_MODEL_PATH`
(or the configured model path); FFmpeg must have the `flite` source, as Debian's FFmpeg does.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from app.config import get_settings
from app.services.media import SUPPORTED_VIDEO_EXTENSIONS
from scripts.check_transcription_stack import H264, VP9, ClipSpec, check_clip

AAC, ALAC, FLAC = ("-c:a", "aac"), ("-c:a", "alac"), ("-c:a", "flac")
PCM16, PCM24, PCMF32 = ("-c:a", "pcm_s16le"), ("-c:a", "pcm_s24le"), ("-c:a", "pcm_f32le")
OPUS, VORBIS = ("-c:a", "libopus"), ("-c:a", "libvorbis")

# Four clips per container: unusual-rate mono, 5.1, 7.1 at 96 kHz and silence. Codecs are ones a
# screen recorder or editor writes into that container; FFmpeg's Opus encoder takes only 8, 12,
# 16, 24 or 48 kHz, so the Opus clips use 48 kHz.
MATRIX = (
    ClipSpec("mp4-aac-mono-11025", ".mp4", H264, AAC, 11_025, 1),
    ClipSpec("mp4-aac-5.1-48000", ".mp4", H264, AAC, 48_000, 6),
    ClipSpec("mp4-alac-7.1-96000", ".mp4", H264, ALAC, 96_000, 8),
    ClipSpec("mp4-aac-silent-44100", ".mp4", H264, AAC, 44_100, 2, speech=False),
    ClipSpec("mov-pcm16-mono-8000", ".mov", H264, PCM16, 8_000, 1),
    ClipSpec("mov-aac-5.1-44100", ".mov", H264, AAC, 44_100, 6),
    ClipSpec("mov-pcm24-7.1-96000", ".mov", H264, PCM24, 96_000, 8),
    ClipSpec("mov-pcm16-silent-48000", ".mov", H264, PCM16, 48_000, 2, speech=False),
    ClipSpec("mkv-vorbis-mono-11025", ".mkv", H264, VORBIS, 11_025, 1),
    ClipSpec("mkv-flac-5.1-22050", ".mkv", H264, FLAC, 22_050, 6),
    ClipSpec("mkv-pcmf32-7.1-96000", ".mkv", H264, PCMF32, 96_000, 8),
    ClipSpec("mkv-opus-silent-48000", ".mkv", H264, OPUS, 48_000, 2, speech=False),
    ClipSpec("webm-vorbis-mono-8000", ".webm", VP9, VORBIS, 8_000, 1),
    ClipSpec("webm-opus-5.1-48000", ".webm", VP9, OPUS, 48_000, 6),
    ClipSpec("webm-vorbis-7.1-96000", ".webm", VP9, VORBIS, 96_000, 8),
    ClipSpec("webm-opus-silent-48000", ".webm", VP9, OPUS, 48_000, 1, speech=False),
)


def test_matrix_covers_every_accepted_container_and_audio_shape() -> None:
    """Keep the matrix complete when a container is added; runs without the opt-in."""
    for extension in SUPPORTED_VIDEO_EXTENSIONS:
        clips = [clip for clip in MATRIX if clip.extension == extension]
        assert any(not clip.speech for clip in clips), extension
        assert any(clip.speech and clip.channels == 1 for clip in clips), extension
        assert any(clip.speech and clip.channels > 2 for clip in clips), extension
        assert any(clip.sample_rate_hz not in (16_000, 44_100, 48_000) for clip in clips)


@pytest.fixture(scope="module")
def real_stack() -> tuple[Path, str, str]:
    """Return the model folder, FFmpeg and FFprobe, or skip with the missing prerequisite."""
    if os.environ.get("NARRASTEP_RUN_REAL_PIPELINE_TESTS") != "1":
        pytest.skip("Set NARRASTEP_RUN_REAL_PIPELINE_TESTS=1 to run the real media matrix.")
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.skip("The real media matrix requires ffmpeg and ffprobe on PATH.")
    configured = os.environ.get("NARRASTEP_REAL_WHISPER_MODEL_PATH")
    model_path = Path(configured) if configured else get_settings().whisper_model_path
    if not (model_path / "model.bin").is_file():
        pytest.skip("Set NARRASTEP_REAL_WHISPER_MODEL_PATH to a local faster-whisper model.")
    return model_path, ffmpeg, ffprobe


@pytest.mark.integration
@pytest.mark.parametrize("spec", MATRIX, ids=[clip.name for clip in MATRIX])
def test_real_media_is_extracted_and_transcribed(
    spec: ClipSpec, real_stack: tuple[Path, str, str], tmp_path: Path
) -> None:
    """Hear the test sentence in every speech clip and finish silent clips without an error."""
    model_path, ffmpeg, ffprobe = real_stack

    result = check_clip(spec, tmp_path, model_path, ffmpeg=ffmpeg, ffprobe=ffprobe)

    if spec.speech:
        assert result.segment_count >= 1
    else:
        assert result.text == ""
