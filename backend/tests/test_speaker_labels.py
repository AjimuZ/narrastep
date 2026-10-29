"""Speaker-label tests: thresholds, loudness measurement, synthetic tracks and Review edits (P05.5).

The media tests synthesize Opus tracks with FFmpeg at test time and are skipped when FFmpeg or
FFprobe is not on `PATH`; no binary fixture is stored in the repository. FFmpeg's sine sources
have amplitude 1/8, about -21 dBFS RMS.
"""

from __future__ import annotations

import math
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    JobStatus,
    MediaAsset,
    OutputMode,
    ProcessingJob,
    Project,
    ProjectState,
    SpeakerLabel,
    SpeakerSource,
    TranscriptSegment,
)
from app.services import processing
from app.services.media import MediaServiceError
from app.services.pipeline import PersistedFrameCandidate
from app.services.processing import perform_processing
from app.services.speaker_labels import (
    FLOOR_DBFS,
    LABEL_MARGIN_DB,
    SILENCE_DBFS,
    SpeakerTracks,
    TrackLoudness,
    classify_speaker,
    energy_to_dbfs,
    label_segments,
    measure_track_loudness,
    speaker_tracks_for,
)
from app.services.workspaces import create_project_workspace

DATA_ROOT = get_settings().data_root
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg and FFprobe are not on PATH.")


def _write_wav(path: Path, samples: np.ndarray, rate: int = 8_000) -> Path:
    """Write float samples in [-1, 1) as a mono 16-bit PCM WAV file."""
    pcm = np.clip(np.round(samples * 32768.0), -32768, 32767).astype("<i2")
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(pcm.tobytes())
    return path


def _encode_audio(expression: str, seconds: float, destination: Path) -> Path:
    """Encode an `aevalsrc` expression as streamed WebM Opus, like a kept recording track."""
    destination.write_bytes(
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i"]
            + [f"aevalsrc='{expression}':sample_rate=48000:channel_layout=mono"]
            + ["-t", str(seconds), "-c:a", "libopus", "-b:a", "96k", "-f", "webm", "pipe:1"],
            check=True,
            capture_output=True,
            timeout=60,
        ).stdout
    )
    return destination


def _tone(frequency: int, amplitude: str, start: float, end: float) -> str:
    """Return an `aevalsrc` term: a sine of `amplitude` between `start` and `end` seconds."""
    return f"between(t,{start},{end})*sin(2*PI*{frequency}*t)*{amplitude}"


def test_classify_speaker_needs_a_clear_margin_and_audible_speech() -> None:
    """Label only when one track is at least 10 dB louder and the louder one is audible."""
    assert LABEL_MARGIN_DB == 10.0
    assert SILENCE_DBFS == -50.0
    assert classify_speaker(-20.0, -30.0) == SpeakerLabel.YOU
    assert classify_speaker(-30.0, -20.0) == SpeakerLabel.OTHERS
    assert classify_speaker(-20.0, -29.9) is None
    assert classify_speaker(-29.9, -20.0) is None
    assert classify_speaker(-20.0, -20.0) is None
    # A quiet but audible microphone over digital silence is still the user.
    assert classify_speaker(-45.0, FLOOR_DBFS) == SpeakerLabel.YOU
    # Below the silence level neither track holds speech, whatever the difference.
    assert classify_speaker(-50.1, FLOOR_DBFS) is None
    assert classify_speaker(FLOOR_DBFS, FLOOR_DBFS) is None
    assert classify_speaker(FLOOR_DBFS, -50.0) == SpeakerLabel.OTHERS


def test_levels_average_overlapping_frames_and_count_missing_time_as_silence() -> None:
    """Average energy over every overlapping frame; past the track end counts as silence."""
    loudness = TrackLoudness(frame_energies=np.array([0.0, 0.1, 0.1, 0.0]), frame_seconds=0.5)
    assert loudness.level_dbfs(0.5, 1.5) == pytest.approx(-10.0)
    # [0.25, 1.25) overlaps frames 0, 1 and 2.
    assert loudness.level_dbfs(0.25, 1.25) == pytest.approx(10 * math.log10(0.2 / 3))
    # Frames 2..5 are requested; 4 and 5 do not exist and count as silence.
    assert loudness.level_dbfs(1.0, 3.0) == pytest.approx(10 * math.log10(0.1 / 4))
    assert loudness.level_dbfs(5.0, 6.0) == FLOOR_DBFS
    assert loudness.level_dbfs(1.0, 1.0) == FLOOR_DBFS
    assert loudness.level_dbfs(2.0, 1.0) == FLOOR_DBFS
    assert energy_to_dbfs(0.0) == FLOOR_DBFS
    assert energy_to_dbfs(1e-20) == FLOOR_DBFS
    assert energy_to_dbfs(1.0) == 0.0


def test_measurement_reads_frames_in_blocks_including_a_partial_frame(tmp_path: Path) -> None:
    """Measure a WAV longer than one read block, with a trailing partial frame."""
    rate = 8_000
    seconds = 12.5125  # 250 full 50 ms frames and one of 5 ms
    times = np.arange(round(seconds * rate)) / rate
    tone = 0.5 * np.sin(2 * np.pi * 440 * times)
    tone[times >= 6.0] = 0.0
    loudness = measure_track_loudness(_write_wav(tmp_path / "tone.wav", tone, rate))
    assert loudness.frame_energies.size == 251
    # A sine of amplitude 0.5 has mean square 0.125, about -9.03 dBFS.
    assert loudness.level_dbfs(0.0, 6.0) == pytest.approx(10 * math.log10(0.125), abs=0.05)
    assert loudness.level_dbfs(6.0, 12.5125) == FLOOR_DBFS

    with wave.open(str(tmp_path / "stereo.wav"), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b"\x00\x00" * 200)
    with pytest.raises(MediaServiceError) as error:
        measure_track_loudness(tmp_path / "stereo.wav")
    assert error.value.code == "SPEAKER_LABELS_FAILED"


@needs_ffmpeg
def test_synthetic_tracks_label_microphone_system_both_echo_and_silence(tmp_path: Path) -> None:
    """Label decoded Opus tracks: microphone only, system only, both, echo, quiet and silence.

    Timeline (seconds): 0-2 microphone alone; 2-4 system alone, with the call heard 20 dB lower
    on the microphone (speakers); 4-6 both equally loud; 6-8 silence; 8-10 a quiet microphone
    (about -43 dBFS); 10-12 microphone noise below the silence level (about -63 dBFS).
    """
    microphone = _encode_audio(
        "+".join(
            [
                _tone(1000, "0.125", 0, 2),
                _tone(440, "0.0125", 2, 4),
                _tone(1000, "0.125", 4, 6),
                _tone(1000, "0.01", 8, 10),
                _tone(1000, "0.001", 10, 12),
            ]
        ),
        12.0,
        tmp_path / "microphone.webm",
    )
    system = _encode_audio(
        _tone(440, "0.125", 2, 4) + "+" + _tone(700, "0.125", 4, 6),
        12.0,
        tmp_path / "system.webm",
    )
    segments = [(0.1, 1.9), (2.1, 3.9), (4.1, 5.9), (6.1, 7.9), (8.1, 9.9), (10.1, 11.9)]

    labels = label_segments(
        SpeakerTracks(microphone=microphone, system=system),
        segments,
        tmp_path / "work",
        config=get_settings(),
    )

    assert labels == [SpeakerLabel.YOU, SpeakerLabel.OTHERS, None, None, SpeakerLabel.YOU, None]
    # The temporary WAV files are removed.
    assert list((tmp_path / "work").iterdir()) == []


@needs_ffmpeg
def test_an_undecodable_track_raises_a_media_error(tmp_path: Path) -> None:
    """Raise `MediaServiceError` for a track FFmpeg cannot decode and leave no WAV behind."""
    broken = tmp_path / "microphone.webm"
    broken.write_bytes(b"not a webm file")
    system = _encode_audio(_tone(440, "0.125", 0, 1), 1.0, tmp_path / "system.webm")
    with pytest.raises(MediaServiceError):
        label_segments(
            SpeakerTracks(microphone=broken, system=system),
            [(0.0, 1.0)],
            tmp_path / "work",
            config=get_settings(),
        )
    assert list((tmp_path / "work").iterdir()) == []


def test_tracks_are_found_only_for_recordings_that_kept_both(session: Session) -> None:
    """Return tracks only when the asset reports both kept tracks and both files exist."""
    project = Project(name="Call", state=ProjectState.UPLOADED)
    session.add(project)
    session.flush()
    source_dir = create_project_workspace(DATA_ROOT, project.id) / "source"
    source = source_dir / "source.webm"
    source.write_bytes(b"video")
    asset = MediaAsset(
        project_id=project.id,
        display_filename="Call.webm",
        stored_filename="source.webm",
        relative_path=source.relative_to(DATA_ROOT).as_posix(),
        content_type="video/webm",
        size_bytes=5,
        duration_seconds=1.0,
        width=320,
        height=240,
        container_format="matroska,webm",
        metadata_json={},
    )
    # An uploaded video has no recording metadata.
    assert speaker_tracks_for(asset, DATA_ROOT) is None
    # A recording saved before P05.5 kept only the microphone.
    asset.metadata_json = {"recording": {"microphoneTrack": True, "systemAudio": True}}
    (source_dir / "microphone.webm").write_bytes(b"mic")
    assert speaker_tracks_for(asset, DATA_ROOT) is None
    asset.metadata_json = {"recording": {"microphoneTrack": True, "systemTrack": True}}
    assert speaker_tracks_for(asset, DATA_ROOT) is None  # system.webm is missing
    (source_dir / "system.webm").write_bytes(b"sys")
    tracks = speaker_tracks_for(asset, DATA_ROOT)
    assert tracks == SpeakerTracks(
        microphone=(source_dir / "microphone.webm").resolve(),
        system=(source_dir / "system.webm").resolve(),
    )


def _fake_frames(
    _: Path, __: object, output_directory: Path, **___: object
) -> list[PersistedFrameCandidate]:
    """Stage one PNG candidate so processing completes without scanning the video."""
    from PIL import Image

    output_directory.mkdir(parents=True, exist_ok=True)
    path = output_directory / "candidate.png"
    Image.new("RGB", (32, 18), (120, 120, 120)).save(path)
    return [
        PersistedFrameCandidate(
            timestamp_seconds=2,
            image_path=path,
            stability=1,
            sharpness=0.5,
            exposure_penalty=0,
            quality_score=0.9,
            perceptual_hash="0123456789abcdef",
            source_index=0,
            width=32,
            height=18,
        )
    ]


def _record(client: TestClient, *, seconds: float = 14.5) -> str:
    """Record, upload and save a call through the API and return the project ID.

    The user speaks (1000 Hz on the microphone) for the first 5 s and the call (440 Hz in the
    system audio) for the next 5 s; the rest is silent. The mock transcription splits the 14.5 s
    into segments of 0-5, 5-10 and 10-14.5 s (14.5 s keeps the source under 15 s even when the
    last video frame adds a little).
    """
    created = client.post("/api/v1/recordings")
    assert created.status_code == 201, created.text
    recording_id = created.json()["id"]
    project_id = str(created.json()["projectId"])
    screen = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=5"]
        + ["-f", "lavfi", "-i"]
        + [f"aevalsrc='{_tone(440, '0.125', 5, 10)}':sample_rate=48000:channel_layout=mono"]
        + ["-t", str(seconds), "-c:v", "libvpx", "-b:v", "100k", "-c:a", "libopus"]
        + ["-f", "webm", "pipe:1"],
        check=True,
        capture_output=True,
        timeout=120,
    ).stdout
    microphone = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i"]
        + [f"aevalsrc='{_tone(1000, '0.125', 0, 5)}':sample_rate=48000:channel_layout=mono"]
        + ["-t", str(seconds), "-c:a", "libopus", "-f", "webm", "pipe:1"],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    for track, data in (("screen", screen), ("microphone", microphone)):
        response = client.put(
            f"/api/v1/recordings/{recording_id}/tracks/{track}/chunks/0",
            content=data,
            headers={"Content-Type": "video/webm"},
            params={"startOffsetMs": 0},
        )
        assert response.status_code == 201, response.text
    saved = client.post(f"/api/v1/recordings/{recording_id}/complete")
    assert saved.status_code == 200, saved.text
    return project_id


def _process(session: Session, project_id: str, monkeypatch: pytest.MonkeyPatch) -> ProcessingJob:
    """Run the worker pipeline on a saved recording with the mock transcription."""
    project = session.get(Project, project_id)
    assert project is not None
    project.output_mode = OutputMode.MEETING
    project.state = ProjectState.QUEUED
    job = ProcessingJob(project_id=project_id)
    session.add(job)
    session.commit()
    monkeypatch.setattr(
        "app.services.processing.extract_deduplicated_frames_to_directory", _fake_frames
    )
    monkeypatch.setattr("app.services.progress.publish_job_event", lambda _job: None)
    perform_processing(job.id, get_settings())
    session.expire_all()
    completed = session.get(ProcessingJob, job.id)
    assert completed is not None
    return completed


@needs_ffmpeg
def test_a_processed_recording_is_labelled_and_the_labels_are_editable_in_review(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Record, save, process and review: automatic You / Others labels, then user edits."""
    project_id = _record(client)
    job = _process(session, project_id, monkeypatch)
    assert job.status == JobStatus.COMPLETED, job.error_code
    assert job.metrics_summary["speakerLabelYouCount"] == 1
    assert job.metrics_summary["speakerLabelOthersCount"] == 1
    assert job.metrics_summary["speakerLabelUnlabelledCount"] == 1
    # The temporary decoded tracks are gone with the processing folder.
    assert not (DATA_ROOT / "projects" / project_id / ".processing" / job.id).exists()

    listed = client.get(f"/api/v1/projects/{project_id}/segments")
    assert listed.status_code == 200
    segments = listed.json()
    assert [(item["speakerLabel"], item["speakerSource"]) for item in segments] == [
        ("you", "automatic"),
        ("others", "automatic"),
        (None, None),
    ]

    first, _, last = segments
    changed = client.patch(f"/api/v1/segments/{last['id']}", json={"speakerLabel": "others"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["speakerLabel"] == "others"
    assert changed.json()["speakerSource"] == "user"
    assert changed.json()["reviewedText"] == last["reviewedText"]
    cleared = client.patch(f"/api/v1/segments/{first['id']}", json={"speakerLabel": None})
    assert cleared.json()["speakerLabel"] is None
    assert cleared.json()["speakerSource"] == "user"
    assert client.get(f"/api/v1/projects/{project_id}").json()["status"] == "review"


def test_processing_continues_unlabelled_when_the_tracks_cannot_be_measured(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A measurement failure leaves every segment unlabelled and is reported in the metrics."""
    project = Project(name="Call", state=ProjectState.QUEUED, output_mode=OutputMode.MEETING)
    session.add(project)
    session.flush()
    source_dir = create_project_workspace(DATA_ROOT, project.id) / "source"
    source = source_dir / "source.webm"
    source.write_bytes(b"video")
    (source_dir / "microphone.webm").write_bytes(b"mic")
    (source_dir / "system.webm").write_bytes(b"sys")
    session.add(
        MediaAsset(
            project_id=project.id,
            display_filename="Call.webm",
            stored_filename="source.webm",
            relative_path=source.relative_to(DATA_ROOT).as_posix(),
            content_type="video/webm",
            size_bytes=5,
            duration_seconds=10.0,
            width=320,
            height=240,
            container_format="matroska,webm",
            metadata_json={"recording": {"microphoneTrack": True, "systemTrack": True}},
        )
    )
    job = ProcessingJob(project_id=project.id)
    session.add(job)
    session.commit()

    from app.services.media import MediaMetadata

    metadata = MediaMetadata(
        format_name="matroska,webm",
        duration_seconds=10.0,
        size_bytes=5,
        width=320,
        height=240,
        video_codec="vp8",
        audio_codec="opus",
        frame_rate=5,
        content_type="video/webm",
    )

    def fake_audio(_: Path, destination: Path, **__: object) -> Path:
        """Write ten seconds of silent PCM for the mock transcription."""
        return _write_wav(destination, np.zeros(160_000), 16_000)

    def failing_labels(*_: object, **__: object) -> list[SpeakerLabel | None]:
        """Fail like a track FFmpeg cannot decode."""
        raise MediaServiceError("FFMPEG_FAILED", "The video audio could not be prepared.")

    monkeypatch.setattr("app.services.processing.validate_video", lambda *_a, **_k: metadata)
    monkeypatch.setattr("app.services.processing.extract_pcm_audio", fake_audio)
    monkeypatch.setattr(processing, "label_segments", failing_labels)
    monkeypatch.setattr(
        "app.services.processing.extract_deduplicated_frames_to_directory", _fake_frames
    )
    monkeypatch.setattr("app.services.progress.publish_job_event", lambda _job: None)

    perform_processing(job.id, get_settings())

    session.expire_all()
    completed = session.get(ProcessingJob, job.id)
    assert completed is not None and completed.status == JobStatus.COMPLETED
    assert completed.metrics_summary["speakerLabelFailed"] == 1
    speakers = session.execute(
        select(TranscriptSegment.speaker, TranscriptSegment.speaker_source).where(
            TranscriptSegment.project_id == project.id
        )
    ).all()
    assert [tuple(row) for row in speakers] == [(None, None), (None, None)]


def _segments(session: Session, project: Project, *specs: tuple[str | None, str | None]) -> list:
    """Add one active 5 s segment per `(speaker, source)` pair, in order."""
    rows = []
    for index, (speaker, source) in enumerate(specs):
        row = TranscriptSegment(
            project_id=project.id,
            sequence=index,
            start_seconds=index * 5.0,
            end_seconds=index * 5.0 + 5.0,
            raw_text=f"Part {index}",
            reviewed_text=f"Part {index}",
            raw_words=[],
            speaker=SpeakerLabel(speaker) if speaker else None,
            speaker_source=SpeakerSource(source) if source else None,
        )
        session.add(row)
        rows.append(row)
    session.commit()
    return rows


def test_review_edits_validate_the_label_and_keep_the_text(
    client: TestClient, session: Session
) -> None:
    """Change text and label independently; refuse unknown labels and an empty body."""
    project = Project(name="Call", state=ProjectState.COMPLETED)
    session.add(project)
    session.flush()
    (segment,) = _segments(session, project, ("you", "automatic"))

    text_only = client.patch(f"/api/v1/segments/{segment.id}", json={"reviewedText": "Edited"})
    assert text_only.status_code == 200
    assert (text_only.json()["speakerLabel"], text_only.json()["speakerSource"]) == (
        "you",
        "automatic",
    )
    # Completed work returns to review after an edit.
    assert client.get(f"/api/v1/projects/{project.id}").json()["status"] == "review"

    both = client.patch(
        f"/api/v1/segments/{segment.id}",
        json={"reviewedText": "Edited again", "speakerLabel": "others"},
    )
    assert both.status_code == 200
    assert both.json()["reviewedText"] == "Edited again"
    assert both.json()["rawText"] == "Part 0"
    assert (both.json()["speakerLabel"], both.json()["speakerSource"]) == ("others", "user")

    for body in ({}, {"speakerLabel": "me"}, {"speakerLabel": "You"}, {"reviewedText": None}):
        refused = client.patch(f"/api/v1/segments/{segment.id}", json=body)
        assert refused.status_code == 422, body
        assert refused.json()["error"]["code"] == "VALIDATION_ERROR"
    session.expire_all()
    stored = session.get(TranscriptSegment, segment.id)
    assert stored is not None
    assert (stored.reviewed_text, stored.speaker) == ("Edited again", SpeakerLabel.OTHERS)

    project.state = ProjectState.EXPORTING
    session.commit()
    busy = client.patch(f"/api/v1/segments/{segment.id}", json={"speakerLabel": "you"})
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "PROJECT_BUSY"


def test_split_copies_the_label_and_merge_keeps_only_a_shared_one(
    client: TestClient, session: Session
) -> None:
    """Split children inherit label and source; merges keep a label only all parts share."""
    project = Project(name="Call", state=ProjectState.REVIEW)
    session.add(project)
    session.flush()
    first, second, third, fourth = _segments(
        session,
        project,
        ("you", "automatic"),
        ("you", "user"),
        ("you", "automatic"),
        ("others", "automatic"),
    )

    split = client.post(
        f"/api/v1/segments/{first.id}/split",
        json={"leftReviewedText": "Pa", "rightReviewedText": "rt 0", "splitSeconds": 2.5},
    )
    assert split.status_code == 200, split.text
    left, right = split.json()
    assert [(item["speakerLabel"], item["speakerSource"]) for item in (left, right)] == [
        ("you", "automatic"),
        ("you", "automatic"),
    ]

    automatic = client.post(
        "/api/v1/segments/merge", json={"segmentIds": [left["id"], right["id"]]}
    )
    assert (automatic.json()["speakerLabel"], automatic.json()["speakerSource"]) == (
        "you",
        "automatic",
    )
    # A part the user labelled makes the merged label a user choice.
    with_user = client.post(
        "/api/v1/segments/merge", json={"segmentIds": [automatic.json()["id"], second.id]}
    )
    assert (with_user.json()["speakerLabel"], with_user.json()["speakerSource"]) == ("you", "user")
    mixed = client.post("/api/v1/segments/merge", json={"segmentIds": [third.id, fourth.id]})
    assert (mixed.json()["speakerLabel"], mixed.json()["speakerSource"]) == (None, None)
