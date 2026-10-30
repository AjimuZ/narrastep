"""Deterministic transcription and visual ranking algorithm tests."""

from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from app.config import Settings
from app.services.pipeline import (
    FrameCandidate,
    PipelineServiceError,
    SampledFrame,
    compute_perceptual_hash,
    deduplicate_consecutive_candidates,
    extract_deduplicated_frames_to_directory,
    generate_frame_times,
    perceptual_hash_similarity,
    rank_candidates_for_segment,
    transcribe_audio,
)
from app.services.processing import ProcessingCancelled


def _candidate(timestamp: float, quality: float, perceptual_hash: str) -> FrameCandidate:
    """Create one minimal visual candidate for deterministic ranking assertions."""
    return FrameCandidate(
        timestamp_seconds=timestamp,
        frame=np.zeros((8, 8, 3), dtype=np.uint8),
        stability=1,
        sharpness=1,
        exposure_penalty=0,
        quality_score=quality,
        perceptual_hash=perceptual_hash,
        source_index=round(timestamp),
    )


def test_mock_transcription_requires_explicit_mode_and_keeps_words(tmp_path: Path) -> None:
    """Produce stable word timings only when the caller explicitly enables mock processing."""
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 16_000)

    result = transcribe_audio(
        audio,
        config=Settings(data_root=tmp_path, environment="test", mock_processing=True),
        source_language="auto",
    )

    assert result.mock is True
    assert result.language == "en"
    assert result.segments[0].words
    assert result.segments[0].words[0].start_seconds == 0


def test_phash_deduplication_and_temporal_ranking_are_deterministic() -> None:
    """Collapse only consecutive duplicates and favor midpoint evidence by documented weights."""
    black_hash = compute_perceptual_hash(np.zeros((32, 32, 3), dtype=np.uint8))
    assert len(black_hash) == 16
    assert perceptual_hash_similarity(black_hash, black_hash) == 1
    candidates = [
        _candidate(1, 0.5, "0000000000000000"),
        _candidate(2, 0.9, "0000000000000000"),
        _candidate(5, 0.7, "ffffffffffffffff"),
        _candidate(8, 0.8, "0000000000000000"),
    ]

    deduplicated = deduplicate_consecutive_candidates(candidates, similarity_threshold=0.92)
    assert [item.timestamp_seconds for item in deduplicated] == [2, 5, 8]
    selection = rank_candidates_for_segment(
        {"id": "segment", "startSeconds": 0, "endSeconds": 10}, deduplicated
    )
    assert selection.primary is not None
    assert selection.primary.candidate.timestamp_seconds == 5


def test_frame_times_include_interval_and_segment_anchors() -> None:
    """Cover broad video intervals and transcript-specific 10/50/90 percent anchors."""
    times = generate_frame_times(
        10,
        [{"id": "s1", "startSeconds": 2, "endSeconds": 6}],
        interval_seconds=3,
    )

    assert {0, 3, 6, 9, 2.4, 4.0, 5.6}.issubset(set(times))


def test_real_transcription_generator_preserves_cooperative_cancellation(tmp_path: Path) -> None:
    """Let a worker cancellation signal escape model wrapping between Whisper segments."""
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 16_000)

    class FakeModel:
        """Expose one lazy Whisper-like segment for cancellation boundary testing."""

        def transcribe(self, *_: object, **kwargs: object) -> tuple[object, object]:
            """Return a lazy segment iterator and record required VAD configuration."""
            assert kwargs["vad_filter"] is True

            def segments() -> object:
                """Yield one minimal segment only after the callback has a chance to stop."""
                yield SimpleNamespace(start=0, end=1, text="hello", words=[], avg_logprob=-0.1)

            return segments(), SimpleNamespace(language="en", language_probability=1, duration=1)

    def factory(*_: object, **__: object) -> FakeModel:
        """Return the local fake without accessing a model repository."""
        return FakeModel()

    def cancel() -> None:
        """Raise the same typed signal used by durable worker cancellation."""
        raise ProcessingCancelled()

    with pytest.raises(ProcessingCancelled):
        transcribe_audio(
            audio,
            model_path=tmp_path / "model",
            model_factory=factory,
            cancellation_check=cancel,
        )


def _write_wav(path: Path, samples: bytes, *, channels: int = 1, rate: int = 16_000) -> Path:
    """Write little-endian 16-bit PCM `samples` as a WAV file and return its path."""
    with wave.open(str(path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples)
    return path


def test_whisper_receives_the_extracted_samples_instead_of_decoding_the_file(
    tmp_path: Path,
) -> None:
    """Keep PyAV out of transcription: faster-whisper 1.2.1 fails on every path with PyAV 19.

    The fake model behaves like faster-whisper 1.2.1 with PyAV 19.0.0 or newer, which raises
    this `TypeError` for any file path (P08.1, risk K15) but works on a sample array.
    """
    audio = _write_wav(
        tmp_path / "audio.wav", np.array([0, 16_384, -32_768, 32_767], "<i2").tobytes()
    )
    received: list[object] = []

    class PyAv19Model:
        """Reject file paths the way faster-whisper's PyAV decoder does after PyAV 19."""

        def transcribe(self, audio: object, **_: object) -> tuple[object, object]:
            """Record the input and return one segment, or fail for a path like the real stack."""
            if isinstance(audio, str):
                raise TypeError("open() got an unexpected keyword argument 'metadata_errors'")
            received.append(audio)
            segment = SimpleNamespace(start=0, end=0.1, text="ok", words=[], avg_logprob=-0.1)
            return iter([segment]), SimpleNamespace(
                language="en", language_probability=1, duration=0.1
            )

    result = transcribe_audio(
        audio, model_path=tmp_path / "model", model_factory=lambda *_, **__: PyAv19Model()
    )

    assert [segment.text for segment in result.segments] == ["ok"]
    (samples,) = received
    assert isinstance(samples, np.ndarray) and samples.dtype == np.float32
    assert samples.tolist() == [0.0, 0.5, -1.0, 32_767 / 32_768]


@pytest.mark.parametrize(
    ("channels", "rate", "frames"),
    [(2, 16_000, b"\x00\x00" * 8), (1, 44_100, b"\x00\x00" * 8), (1, 16_000, b"")],
)
def test_transcription_rejects_audio_that_is_not_mono_16_khz_pcm(
    tmp_path: Path, channels: int, rate: int, frames: bytes
) -> None:
    """Fail with the invalid-input code before loading a model for any other WAV shape."""
    audio = _write_wav(tmp_path / "audio.wav", frames, channels=channels, rate=rate)

    def factory(*_: object, **__: object) -> object:
        """Fail the test if the model is loaded for audio that was rejected."""
        raise AssertionError("The model must not load for invalid audio.")

    with pytest.raises(PipelineServiceError) as captured:
        transcribe_audio(audio, model_path=tmp_path / "model", model_factory=factory)
    assert captured.value.code == "TRANSCRIPTION_INPUT_INVALID"


def _factory_kwargs(tmp_path: Path, config: object | None) -> dict[str, object]:
    """Run a transcription with a recording factory and return the keywords it received."""
    audio = _write_wav(tmp_path / "audio.wav", b"\x00\x00" * 16_000)
    seen: dict[str, object] = {}

    class FakeModel:
        """Return no segments so only the model construction is observed."""

        def transcribe(self, *_: object, **__: object) -> tuple[object, object]:
            """Return an empty segment list with minimal metadata."""
            return iter(()), SimpleNamespace(language="en", language_probability=1, duration=1)

    def factory(*_: object, **kwargs: object) -> FakeModel:
        """Record the model keywords."""
        seen.update(kwargs)
        return FakeModel()

    transcribe_audio(audio, config=config, model_path=tmp_path / "model", model_factory=factory)
    return seen


def test_whisper_cpu_threads_reach_the_model_only_when_set(tmp_path: Path) -> None:
    """Pass cpu_threads to the model when configured and leave the library default otherwise."""
    assert "cpu_threads" not in _factory_kwargs(tmp_path, None)
    unset = Settings(data_root=tmp_path, mock_processing=False)
    assert "cpu_threads" not in _factory_kwargs(tmp_path, unset)
    configured = Settings(data_root=tmp_path, mock_processing=False, whisper_cpu_threads=4)
    assert _factory_kwargs(tmp_path, configured)["cpu_threads"] == 4


def test_whisper_cpu_threads_setting_is_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept a positive integer, treat an empty value as unset and reject everything else."""
    monkeypatch.delenv("NARRASTEP_WHISPER_CPU_THREADS", raising=False)
    assert Settings(data_root=tmp_path).whisper_cpu_threads is None
    for raw, expected in (("6", 6), ("", None), ("  ", None)):
        monkeypatch.setenv("NARRASTEP_WHISPER_CPU_THREADS", raw)
        assert Settings(data_root=tmp_path).whisper_cpu_threads == expected
    for bad in ("0", "-2", "abc", "2.5", "65"):
        monkeypatch.setenv("NARRASTEP_WHISPER_CPU_THREADS", bad)
        with pytest.raises(ValueError):
            Settings(data_root=tmp_path)


def test_mock_transcription_cannot_bypass_test_environment(tmp_path: Path) -> None:
    """Reject an ad-hoc mock config outside automated tests at the service boundary."""
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 100)
    unsafe_config = SimpleNamespace(mock_processing=True, environment="production")
    with pytest.raises(Exception) as captured:
        transcribe_audio(audio, config=unsafe_config)
    assert getattr(captured.value, "code", None) == "MOCK_PROCESSING_FORBIDDEN"


def test_frame_extraction_stages_metadata_without_retaining_decoded_arrays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persist retained clusters incrementally and return only path-backed candidates."""
    from app.services import pipeline

    video = tmp_path / "video.mp4"
    video.write_bytes(b"fixture")

    class FakeCapture:
        """Provide an opened capture shell while frame samples are supplied directly."""

        def isOpened(self) -> bool:
            """Report a usable test capture."""
            return True

        def release(self) -> None:
            """Release no external resources in the deterministic test."""

    monkeypatch.setattr(pipeline.cv2, "VideoCapture", lambda _: FakeCapture())

    def samples(_: object, timestamp: float, __: float) -> list[SampledFrame]:
        """Create one short-lived decoded array for each requested timestamp."""
        return [SampledFrame(timestamp, np.full((4, 4, 3), int(timestamp) % 255, np.uint8))]

    def candidate(index: int, sampled: list[SampledFrame]) -> FrameCandidate:
        """Create alternating clusters so each representative is staged immediately."""
        return FrameCandidate(
            timestamp_seconds=sampled[0].timestamp_seconds,
            frame=sampled[0].frame,
            stability=1,
            sharpness=1,
            exposure_penalty=0,
            quality_score=0.5,
            perceptual_hash="0" * 16 if index % 2 == 0 else "f" * 16,
            source_index=index,
        )

    monkeypatch.setattr(pipeline, "_sample_frames_with_capture", samples)
    monkeypatch.setattr(pipeline, "_build_frame_candidate", candidate)
    progress: list[int] = []
    retained = extract_deduplicated_frames_to_directory(
        video,
        range(25),
        tmp_path / "staged",
        progress_callback=lambda completed, _total: progress.append(completed),
    )
    assert progress == list(range(1, 26))
    assert len(retained) == 25
    assert all(item.image_path.is_file() for item in retained)
    assert all(not hasattr(item, "frame") for item in retained)
