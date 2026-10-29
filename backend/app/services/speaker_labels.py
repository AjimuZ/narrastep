"""Speaker labels You / Others from the loudness of a recording's microphone and system audio."""

from __future__ import annotations

import math
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from app.models import MediaAsset, SpeakerLabel
from app.services.media import MediaServiceError, MediaToolConfig, extract_pcm_audio
from app.services.recording_completion import MICROPHONE_FILENAME, SYSTEM_AUDIO_FILENAME
from app.services.workspaces import ensure_within_root

# Both tracks are decoded to mono 16-bit PCM at 8 kHz: speech energy lies below 4 kHz, and the
# temporary WAV files stay at about 1 MB per minute.
SAMPLE_RATE_HZ = 8_000
# Loudness is kept per 50 ms frame, so an hour needs 72,000 numbers per track in memory.
FRAME_SECONDS = 0.05
# A segment is labelled only when one track is at least this much louder than the other: 10 dB
# is about three times the amplitude. Closer levels (people talking over each other, or the call
# heard again through speakers) leave the segment unlabelled instead of guessing.
LABEL_MARGIN_DB = 10.0
# A segment whose louder track stays below this level has no audible speech on either track
# (for example a transcription of background noise) and gets no label.
SILENCE_DBFS = -50.0
# Level reported for digital silence, so the comparison never works with minus infinity.
FLOOR_DBFS = -100.0


@dataclass(frozen=True, slots=True)
class SpeakerTracks:
    """Locate the aligned microphone and system audio of one recording on the source timeline."""

    microphone: Path
    system: Path


@dataclass(frozen=True, slots=True)
class TrackLoudness:
    """Hold the mean square level of one decoded track for every `FRAME_SECONDS` frame.

    Samples are scaled to [-1, 1), so a full-scale square wave has energy 1 (0 dBFS) and a
    full-scale sine 0.5 (about -3 dBFS).
    """

    frame_energies: npt.NDArray[np.float64]
    frame_seconds: float = FRAME_SECONDS

    def level_dbfs(self, start_seconds: float, end_seconds: float) -> float:
        """Return the RMS level in dBFS over `[start, end)`, at least `FLOOR_DBFS`.

        Every frame that overlaps the range counts. Time past the end of the track counts as
        silence, so a track that stops early is quieter over a late segment rather than missing.
        An empty or reversed range returns `FLOOR_DBFS`.
        """
        first = max(0, math.floor(start_seconds / self.frame_seconds))
        last = math.ceil(end_seconds / self.frame_seconds)
        if last <= first:
            return FLOOR_DBFS
        available = self.frame_energies[first:last]
        energy = float(np.sum(available)) / (last - first)
        return energy_to_dbfs(energy)


def energy_to_dbfs(energy: float) -> float:
    """Convert a mean square level to dBFS, clamped below at `FLOOR_DBFS`."""
    if energy <= 0:
        return FLOOR_DBFS
    return max(FLOOR_DBFS, 10.0 * math.log10(energy))


def classify_speaker(microphone_dbfs: float, system_dbfs: float) -> SpeakerLabel | None:
    """Return the speaker of a segment from the two tracks' levels over its time range.

    `you` when the microphone is at least `LABEL_MARGIN_DB` louder than the system audio,
    `others` when the system audio is at least that much louder, and None when the levels are
    closer or the louder track is below `SILENCE_DBFS`.
    """
    if max(microphone_dbfs, system_dbfs) < SILENCE_DBFS:
        return None
    difference = microphone_dbfs - system_dbfs
    if difference >= LABEL_MARGIN_DB:
        return SpeakerLabel.YOU
    if difference <= -LABEL_MARGIN_DB:
        return SpeakerLabel.OTHERS
    return None


def speaker_tracks_for(asset: MediaAsset, data_root: Path) -> SpeakerTracks | None:
    """Return the kept tracks of an in-app recording, or None when labels cannot be computed.

    Labels need both tracks: the asset's `metadataJson.recording` must report a kept microphone
    (`microphoneTrack`) and a kept system audio track (`systemTrack`, written since P05.5), and
    both files must exist next to the source video. Uploaded videos, recordings without system
    audio or without a microphone, and recordings saved before P05.5 return None. Raises
    `ApiError` (`INVALID_ASSET_PATH`) when the stored path leaves the data root.
    """
    recording: Any = (asset.metadata_json or {}).get("recording")
    if not isinstance(recording, dict):
        return None
    if recording.get("microphoneTrack") is not True or recording.get("systemTrack") is not True:
        return None
    source = ensure_within_root(data_root, asset.relative_path)
    tracks = SpeakerTracks(
        microphone=source.with_name(MICROPHONE_FILENAME),
        system=source.with_name(SYSTEM_AUDIO_FILENAME),
    )
    if not tracks.microphone.is_file() or not tracks.system.is_file():
        return None
    return tracks


def measure_track_loudness(wav_path: Path) -> TrackLoudness:
    """Read a mono 16-bit PCM WAV file and return its per-frame energy.

    The file is read in blocks, so memory does not grow with the recording beyond the frame
    array. A trailing partial frame is measured over the samples it has. Raises
    `MediaServiceError` (`SPEAKER_LABELS_FAILED`) for any other sample format.
    """
    with wave.open(str(wav_path), "rb") as reader:
        if reader.getnchannels() != 1 or reader.getsampwidth() != 2:
            raise MediaServiceError(
                "SPEAKER_LABELS_FAILED", "The recording tracks could not be measured."
            )
        frame_samples = max(1, round(reader.getframerate() * FRAME_SECONDS))
        energies: list[npt.NDArray[np.float64]] = []
        remainder = np.empty(0, dtype=np.float64)
        while True:
            data = reader.readframes(frame_samples * 200)
            if not data:
                break
            samples = np.frombuffer(data, dtype="<i2").astype(np.float64) / 32768.0
            samples = np.concatenate([remainder, samples])
            whole = samples.size // frame_samples * frame_samples
            frames = samples[:whole].reshape(-1, frame_samples)
            energies.append(np.mean(frames * frames, axis=1))
            remainder = samples[whole:]
        if remainder.size:
            energies.append(np.array([float(np.mean(remainder * remainder))]))
    frame_energies = np.concatenate(energies) if energies else np.empty(0, dtype=np.float64)
    return TrackLoudness(frame_energies=frame_energies)


def label_segments(
    tracks: SpeakerTracks,
    segments: Sequence[tuple[float, float]],
    work_dir: Path,
    *,
    config: MediaToolConfig,
) -> list[SpeakerLabel | None]:
    """Return one label (or None) per `(start_seconds, end_seconds)` segment, in input order.

    Decodes each track with FFmpeg into a temporary WAV file under `work_dir`, measures it and
    removes the file, then compares the two levels of every segment with `classify_speaker`.
    Both tracks share the source video's timeline, so no offset is applied. Raises
    `MediaServiceError` when FFmpeg cannot decode a track or the decoded audio cannot be read.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    loudness = []
    for name, track in (("microphone", tracks.microphone), ("system", tracks.system)):
        wav_path = work_dir / f"speaker-{name}.wav"
        try:
            extract_pcm_audio(track, wav_path, config=config, sample_rate_hz=SAMPLE_RATE_HZ)
            loudness.append(measure_track_loudness(wav_path))
        except (OSError, EOFError, wave.Error) as error:
            raise MediaServiceError(
                "SPEAKER_LABELS_FAILED", "The recording tracks could not be measured."
            ) from error
        finally:
            wav_path.unlink(missing_ok=True)
    microphone, system = loudness
    return [
        classify_speaker(microphone.level_dbfs(start, end), system.level_dbfs(start, end))
        for start, end in segments
    ]
