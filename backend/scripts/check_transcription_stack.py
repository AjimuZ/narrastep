"""Fail the backend image build when FFmpeg and the Whisper packages cannot transcribe (P08.1)."""

from __future__ import annotations

import argparse
import importlib.metadata
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.services.media import MediaServiceError, extract_pcm_audio, validate_video
from app.services.pipeline import PipelineServiceError, transcribe_audio

# Spoken by FFmpeg's `flite` source (built into Debian's FFmpeg), so no file or network is needed.
SPEECH_TEXT = "Open the settings page and save the form."
# Words every model from `tiny` up must hear in SPEECH_TEXT; casing and punctuation are ignored.
EXPECTED_WORDS = ("settings", "save")
# The packages whose versions decide whether audio reaches Whisper; reported with each result.
STACK_PACKAGES = (
    "faster-whisper",
    "ctranslate2",
    "av",
    "numpy",
    "onnxruntime",
    "tokenizers",
    "huggingface-hub",
)
CHANNEL_LAYOUTS = {1: "mono", 2: "stereo", 6: "5.1", 8: "7.1"}
MEDIA_COMMAND_TIMEOUT_SECONDS = 300.0

H264 = ("-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast")
VP9 = ("-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "200k")


class StackCheckError(RuntimeError):
    """Explain, in one line without a stack trace, why the transcription stack failed."""


@dataclass(frozen=True, slots=True)
class ClipSpec:
    """Describe one generated test video: container, codecs, audio shape and content.

    `speech` clips carry SPEECH_TEXT padded with silence to `duration_seconds`; other clips carry
    digital silence. The video is FFmpeg's `testsrc2` pattern at 320 x 180 and 15 frames/s.
    """

    name: str
    extension: str
    video_codec: tuple[str, ...]
    audio_codec: tuple[str, ...]
    sample_rate_hz: int
    channels: int
    speech: bool = True
    duration_seconds: float = 4.0


# The image-build set: every accepted container once, with mono, stereo and multichannel audio,
# rates other than Whisper's 16 kHz, and one silent video that must transcribe to nothing.
BUILD_CLIPS = (
    ClipSpec("mp4-aac-stereo-44100", ".mp4", H264, ("-c:a", "aac"), 44_100, 2),
    ClipSpec("mov-pcm-mono-8000", ".mov", H264, ("-c:a", "pcm_s16le"), 8_000, 1),
    ClipSpec("mkv-flac-5.1-22050", ".mkv", H264, ("-c:a", "flac"), 22_050, 6),
    ClipSpec("webm-opus-stereo-48000", ".webm", VP9, ("-c:a", "libopus"), 48_000, 2),
    ClipSpec("mp4-aac-silent-48000", ".mp4", H264, ("-c:a", "aac"), 48_000, 2, speech=False),
)


@dataclass(frozen=True, slots=True)
class ClipResult:
    """Record what Whisper returned for one clip: detected language, segments and joined text."""

    name: str
    language: str
    segment_count: int
    text: str


def clip_command(spec: ClipSpec, destination: Path, ffmpeg: str = "ffmpeg") -> list[str]:
    """Return the FFmpeg argument list that writes `spec` to `destination`.

    The audio is padded or cut to the clip length, resampled and remixed to the requested layout,
    so the container, codec, rate and channel count are exactly those of `spec`. Raises
    `ValueError` for a channel count without a named layout.
    """

    layout = CHANNEL_LAYOUTS.get(spec.channels)
    if layout is None:
        raise ValueError(f"Unsupported channel count {spec.channels}.")
    duration = f"{spec.duration_seconds:g}"
    if spec.speech:
        audio_source = f"flite=text='{SPEECH_TEXT}':voice=slt"
    else:
        audio_source = f"anullsrc=r={spec.sample_rate_hz}:cl={layout}"
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
        f"testsrc2=s=320x180:r=15:d={duration}",
        "-f",
        "lavfi",
        "-i",
        audio_source,
        "-filter:a",
        f"apad,atrim=0:{duration},aresample={spec.sample_rate_hz},aformat=channel_layouts={layout}",
        "-map",
        "0:v",
        "-map",
        "1:a",
        *spec.video_codec,
        *spec.audio_codec,
        "-ar",
        str(spec.sample_rate_hz),
        "-ac",
        str(spec.channels),
        str(destination),
    ]


def generate_clip(spec: ClipSpec, directory: Path, ffmpeg: str = "ffmpeg") -> Path:
    """Write `spec` into `directory` and return its path; raise `StackCheckError` on failure."""

    destination = directory / f"{spec.name}{spec.extension}"
    try:
        completed = subprocess.run(
            clip_command(spec, destination, ffmpeg),
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=MEDIA_COMMAND_TIMEOUT_SECONDS,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise StackCheckError(f"{spec.name}: FFmpeg could not run ({error}).") from error
    if completed.returncode != 0 or not destination.is_file():
        reason = (completed.stderr.strip().splitlines() or ["no message"])[-1]
        raise StackCheckError(f"{spec.name}: FFmpeg could not generate the clip ({reason}).")
    return destination


def package_versions(names: Sequence[str] = STACK_PACKAGES) -> dict[str, str]:
    """Return the installed version of each named distribution, or `missing`."""

    versions: dict[str, str] = {}
    for name in names:
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "missing"
    return versions


def ffmpeg_version(ffmpeg: str = "ffmpeg") -> str:
    """Return the first line of `ffmpeg -version`, or why it could not be read."""

    try:
        completed = subprocess.run(
            [ffmpeg, "-hide_banner", "-version"],
            check=False,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"unavailable ({error})"
    lines = completed.stdout.strip().splitlines()
    return lines[0] if completed.returncode == 0 and lines else "unavailable"


def missing_words(text: str, expected: Sequence[str] = EXPECTED_WORDS) -> list[str]:
    """Return the expected words absent from `text`, ignoring case and punctuation."""

    heard = {"".join(ch for ch in word if ch.isalnum()) for word in text.casefold().split()}
    return [word for word in expected if word.casefold() not in heard]


def describe_failure(error: BaseException) -> str:
    """Return the error and its causes as `Type: message` parts, outermost first.

    Narrastep's services replace the cause with a stable public message, so the chain is the only
    place where the package that failed (for example PyAV's `TypeError`) is named.
    """

    parts: list[str] = []
    current: BaseException | None = error
    while current is not None and len(parts) < 4:
        parts.append(f"{type(current).__name__}: {current}")
        current = current.__cause__ or current.__context__
    return " <- ".join(parts)


def check_clip(
    spec: ClipSpec,
    directory: Path,
    model_path: Path,
    *,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> ClipResult:
    """Generate, validate, extract and transcribe one clip the way a processing job does.

    Speech clips must contain EXPECTED_WORDS; silent clips must finish without an error. Raises
    `StackCheckError` with the failing step and the cause chain otherwise.
    """

    clip = generate_clip(spec, directory, ffmpeg)
    try:
        metadata = validate_video(clip, ffprobe_binary=ffprobe)
        audio = extract_pcm_audio(clip, directory / f"{spec.name}.wav", ffmpeg_binary=ffmpeg)
        result = transcribe_audio(
            audio,
            model_path=model_path,
            source_language="en",
            device="cpu",
            compute_type="int8",
            duration_seconds=metadata.duration_seconds,
        )
    except (MediaServiceError, PipelineServiceError) as error:
        code = getattr(error, "code", "UNKNOWN")
        raise StackCheckError(f"{spec.name}: {code} ({describe_failure(error)}).") from error
    text = " ".join(segment.text.strip() for segment in result.segments).strip()
    if spec.speech:
        absent = missing_words(text)
        if absent:
            raise StackCheckError(
                f"{spec.name}: the transcript {text!r} lacks {', '.join(absent)}."
            )
    return ClipResult(spec.name, result.language, len(result.segments), text)


def check_transcription_stack(
    model_path: Path,
    work_dir: Path,
    *,
    clips: Sequence[ClipSpec] = BUILD_CLIPS,
    ffmpeg: str = "ffmpeg",
    ffprobe: str = "ffprobe",
) -> list[ClipResult]:
    """Check every clip in order and return their results; stop at the first failure.

    Requires a local faster-whisper model folder at `model_path` and FFmpeg with the `flite`,
    `testsrc2` and encoder support the clips name. Raises `StackCheckError` when the model is
    missing or any clip fails.
    """

    if not (model_path / "model.bin").is_file():
        raise StackCheckError(f"No faster-whisper model was found in {model_path}.")
    work_dir.mkdir(parents=True, exist_ok=True)
    return [
        check_clip(spec, work_dir, model_path, ffmpeg=ffmpeg, ffprobe=ffprobe) for spec in clips
    ]


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Parse the model folder and tool paths; defaults are the configured Narrastep settings."""

    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, default=settings.whisper_model_path)
    parser.add_argument("--ffmpeg", default=settings.ffmpeg_path)
    parser.add_argument("--ffprobe", default=settings.ffprobe_path)
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> int:
    """Print the versions and each clip's result and return 0, or print the reason and return 1.

    Generated clips live in a temporary folder that is removed afterwards. Only the synthetic
    test sentence is printed, never user media.
    """

    options = parse_arguments(arguments)
    print(f"FFmpeg: {ffmpeg_version(options.ffmpeg)}")
    print(
        "Packages: "
        + ", ".join(f"{name} {version}" for name, version in package_versions().items())
    )
    work_dir = Path(tempfile.mkdtemp(prefix="narrastep-stack-check-"))
    try:
        results = check_transcription_stack(
            options.model_path, work_dir, ffmpeg=options.ffmpeg, ffprobe=options.ffprobe
        )
    except StackCheckError as error:
        print(f"Transcription stack check failed: {error}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
    for result in results:
        print(
            f"{result.name}: {result.segment_count} segment(s), language {result.language}, "
            f"text {result.text!r}"
        )
    print(f"Transcription stack check passed with the model in {options.model_path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
