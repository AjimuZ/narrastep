"""Recording completion tests: track planning, FFmpeg commands, alignment, mix and failures (P05.2).

The media tests synthesize WebM tracks with FFmpeg at test time, written to a pipe so they carry
no duration or cues, exactly like `MediaRecorder` output, and split them into chunks at arbitrary
byte offsets. They are skipped when FFmpeg or FFprobe is not on `PATH`; no binary fixture is
stored in the repository.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal
from app.errors import ApiError
from app.main import app
from app.models import ProcessingJob, RecordingChunk, RecordingTrack
from app.services import recording_completion
from app.services.recording_completion import (
    build_microphone_command,
    build_source_command,
    microphone_alignment_filter,
    plan_track,
)
from app.services.workspaces import acquire_project_operation, release_project_operation

DATA_ROOT = get_settings().data_root
HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
needs_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="FFmpeg and FFprobe are not on PATH.")
ANALYSIS_RATE = 8_000
# FFmpeg's `sine` source has amplitude 1/8; a tone counts as present well above the noise of a
# lossy Opus round trip and as absent well below the tone.
PRESENT = 0.05
ABSENT = 0.01


def _create_recording(client: TestClient) -> tuple[str, str]:
    """Open a recording session and return its ID and its project ID."""
    response = client.post("/api/v1/recordings")
    assert response.status_code == 201, response.text
    payload = response.json()
    return str(payload["id"]), str(payload["projectId"])


def _put(
    client: TestClient,
    recording_id: str,
    track: str,
    sequence: int,
    content: bytes,
    offset: int | None = None,
) -> None:
    """Upload one chunk and require that it is stored."""
    response = client.put(
        f"/api/v1/recordings/{recording_id}/tracks/{track}/chunks/{sequence}",
        content=content,
        headers={"Content-Type": "video/webm"},
        params={"startOffsetMs": offset} if offset is not None else None,
    )
    assert response.status_code in (200, 201), response.text


def _upload_track(
    client: TestClient,
    recording_id: str,
    track: str,
    data: bytes,
    *,
    chunks: int = 5,
    offset: int | None = None,
    skip: frozenset[int] = frozenset(),
) -> None:
    """Split a whole WebM stream into `chunks` byte ranges and upload them out of order."""
    size = len(data) // chunks + 1
    pieces = [data[index * size : (index + 1) * size] for index in range(chunks)]
    for sequence in reversed(range(chunks)):
        if sequence not in skip:
            _put(client, recording_id, track, sequence, pieces[sequence], offset)


def _complete(client: TestClient, recording_id: str, **body: object):  # type: ignore[no-untyped-def]
    """Ask the API to save the recording."""
    return client.post(f"/api/v1/recordings/{recording_id}/complete", json=body or None)


def _encode(inputs: list[str], codecs: list[str], seconds: float, container: str = "webm") -> bytes:
    """Encode lavfi inputs to a streamed file (no cues, no duration), as a browser writes it."""
    command = ["ffmpeg", "-nostdin", "-v", "error"]
    for source in inputs:
        command += ["-f", "lavfi", "-i", source]
    command += ["-t", str(seconds), *codecs, "-f", container, "pipe:1"]
    return subprocess.run(command, check=True, capture_output=True, timeout=60).stdout


def _screen(seconds: float = 4.0, *, audio: bool = True, video_codec: str = "libvpx") -> bytes:
    """Return a screen track: test pattern video plus, optionally, a 440 Hz system tone."""
    inputs = ["testsrc=size=320x240:rate=15"]
    codecs = ["-c:v", video_codec, "-b:v", "300k"]
    if audio:
        inputs.append("sine=frequency=440:sample_rate=48000")
        codecs += ["-c:a", "libopus"]
    return _encode(inputs, codecs, seconds)


def _microphone(seconds: float = 3.0, *, tone_from: float = 0.0) -> bytes:
    """Return a microphone track: silence until `tone_from`, then a 1000 Hz tone."""
    expression = f"if(gte(t,{tone_from}),sin(2*PI*1000*t)/8,0)"
    return _encode(
        [f"aevalsrc='{expression}':sample_rate=48000:channel_layout=mono"],
        ["-c:a", "libopus"],
        seconds,
    )


def _audio(path: Path) -> np.ndarray:
    """Decode the first audio stream of `path` to mono float samples at `ANALYSIS_RATE`."""
    raw = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0"]
        + ["-ac", "1", "-ar", str(ANALYSIS_RATE), "-f", "f32le", "pipe:1"],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout
    return np.frombuffer(raw, dtype="<f4")


def _tone(samples: np.ndarray, frequency: float, start: float, end: float) -> float:
    """Return the amplitude of one frequency between `start` and `end` seconds."""
    window = samples[int(start * ANALYSIS_RATE) : int(end * ANALYSIS_RATE)]
    times = np.arange(len(window)) / ANALYSIS_RATE
    return float(2 * abs(np.dot(window, np.exp(-2j * np.pi * frequency * times))) / len(window))


def _duration(path: Path) -> float:
    """Return the container duration FFprobe reports for `path`."""
    output = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    ).stdout
    return float(output.strip())


def _source_dir(project_id: str) -> Path:
    """Return the project's source folder."""
    return DATA_ROOT / "projects" / project_id / "source"


def _chunk_rows(session: Session, recording_id: str) -> int:
    """Count the chunk rows a session still has."""
    session.expire_all()
    return int(
        session.scalar(
            select(func.count())
            .select_from(RecordingChunk)
            .where(RecordingChunk.recording_id == recording_id)
        )
        or 0
    )


def test_plan_track_keeps_a_contiguous_run_and_reports_gaps() -> None:
    """Accept chunks 0..n-1 in any order; refuse a gap unless asked to stop at it."""
    plan = plan_track(RecordingTrack.SCREEN, [(2, 30), (0, 10), (1, 20)], None, stop_at_gap=False)
    assert plan.sequences == (0, 1, 2)
    assert plan.stored_bytes == 60
    assert plan.offset_ms == 0

    with pytest.raises(ApiError) as refused:
        plan_track(RecordingTrack.MICROPHONE, [(0, 1), (3, 1), (5, 1)], 40, stop_at_gap=False)
    assert refused.value.code == "RECORDING_INCOMPLETE"
    assert refused.value.status_code == 409
    assert refused.value.details == {
        "track": "microphone",
        "firstMissingSequence": 1,
        "missingCount": 3,
    }

    stopped = plan_track(RecordingTrack.SCREEN, [(0, 5), (1, 5), (4, 5)], 7, stop_at_gap=True)
    assert stopped.sequences == (0, 1)
    assert stopped.stored_bytes == 10
    assert stopped.offset_ms == 7

    headless = plan_track(RecordingTrack.SCREEN, [(1, 5)], None, stop_at_gap=True)
    assert not headless.used
    assert not plan_track(RecordingTrack.MICROPHONE, [], None, stop_at_gap=False).used


def test_alignment_delays_a_later_microphone_and_trims_an_earlier_one() -> None:
    """Shift the microphone by the difference of the start offsets, in milliseconds."""
    assert microphone_alignment_filter(100, 600) == (
        "asetpts=PTS-STARTPTS,adelay=delays=500:all=1,aresample=async=1"
    )
    assert microphone_alignment_filter(800, 50) == (
        "asetpts=PTS-STARTPTS,atrim=start=0.750,asetpts=PTS-STARTPTS,aresample=async=1"
    )
    assert microphone_alignment_filter(0, 0) == "asetpts=PTS-STARTPTS,aresample=async=1"


def test_source_command_copies_video_and_mixes_without_halving_levels(tmp_path: Path) -> None:
    """Build argument arrays (no shell) for each audio combination."""
    output = tmp_path / "out.webm"
    mixed = build_source_command(
        "ffmpeg",
        "concatf:/s.txt",
        "concatf:/m.txt",
        screen_has_audio=True,
        alignment="ALIGN",
        container="webm",
        output=output,
    )
    graph = mixed[mixed.index("-filter_complex") + 1]
    assert "[1:a:0]ALIGN[microphone]" in graph
    assert "amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,apad" in graph
    assert mixed[mixed.index("-c:v") + 1] == "copy"
    assert "-shortest" in mixed
    assert mixed.count("-protocol_whitelist") == 2
    assert mixed[-3:] == ["-f", "webm", str(output)]

    microphone_only = build_source_command(
        "ffmpeg",
        "concatf:/s.txt",
        "concatf:/m.txt",
        screen_has_audio=False,
        alignment="ALIGN",
        container="matroska",
        output=output,
    )
    assert microphone_only[microphone_only.index("-filter_complex") + 1] == (
        "[1:a:0]ALIGN,apad[audio]"
    )

    system_only = build_source_command(
        "ffmpeg",
        "concatf:/s.txt",
        None,
        screen_has_audio=True,
        alignment="ALIGN",
        container="webm",
        output=output,
    )
    assert "-filter_complex" not in system_only
    assert system_only[system_only.index("-c:a") + 1] == "copy"

    with pytest.raises(ValueError):
        build_source_command(
            "ffmpeg",
            "concatf:/s.txt",
            None,
            screen_has_audio=False,
            alignment="ALIGN",
            container="webm",
            output=output,
        )

    kept = build_microphone_command("ffmpeg", "concatf:/m.txt", alignment="ALIGN", output=output)
    assert kept[kept.index("-af") + 1] == "ALIGN"
    assert kept[-3:] == ["-f", "webm", str(output)]


def test_missing_chunks_refuse_completion_and_keep_the_session_open(
    client: TestClient, session: Session
) -> None:
    """Refuse a gap or a missing first screen chunk with `RECORDING_INCOMPLETE`; change nothing."""
    recording_id, project_id = _create_recording(client)
    _put(client, recording_id, "screen", 1, b"later")

    headless = _complete(client, recording_id, stopAtGap=True)
    assert headless.status_code == 409
    assert headless.json()["error"]["code"] == "RECORDING_INCOMPLETE"
    assert headless.json()["error"]["details"] == {"track": "screen", "firstMissingSequence": 0}

    _put(client, recording_id, "screen", 0, b"\x1a\x45\xdf\xa3first")
    _put(client, recording_id, "microphone", 0, b"\x1a\x45\xdf\xa3first")
    _put(client, recording_id, "microphone", 2, b"third")
    gap = _complete(client, recording_id)
    assert gap.status_code == 409
    assert gap.json()["error"]["details"] == {
        "track": "microphone",
        "firstMissingSequence": 1,
        "missingCount": 1,
    }

    state = client.get(f"/api/v1/recordings/{recording_id}").json()
    assert state["state"] == "recording"
    assert _chunk_rows(session, recording_id) == 4
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["status"] == "draft"
    assert project["videoAsset"] is None


def test_unreadable_media_fails_cleanly_and_the_recording_can_be_retried(
    client: TestClient, session: Session
) -> None:
    """Turn an FFprobe failure into 422 without output files, rows or state changes."""
    recording_id, project_id = _create_recording(client)
    _put(client, recording_id, "screen", 0, b"\x1a\x45\xdf\xa3" + b"\x00" * 64)
    broken = get_settings().model_copy(update={"ffprobe_path": "/nonexistent/ffprobe"})
    app.dependency_overrides[get_settings] = lambda: broken
    try:
        response = _complete(client, recording_id, name="Never saved")
    finally:
        app.dependency_overrides.pop(get_settings, None)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VIDEO_VALIDATION_FAILED"
    assert list(_source_dir(project_id).iterdir()) == []
    assert _chunk_rows(session, recording_id) == 1
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["name"] != "Never saved"
    assert project["recording"]["state"] == "recording"


def test_completion_refuses_a_busy_project_a_saved_session_and_an_unknown_id(
    client: TestClient,
) -> None:
    """Answer `PROJECT_BUSY` under the project lock and 404 for an unknown session."""
    recording_id, project_id = _create_recording(client)
    reservation = acquire_project_operation(DATA_ROOT, project_id, "test")
    try:
        busy = _complete(client, recording_id)
    finally:
        release_project_operation(reservation)
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "PROJECT_BUSY"

    unknown = _complete(client, "00000000-0000-4000-8000-000000000000")
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "RECORDING_NOT_FOUND"

    blank = _complete(client, recording_id, name="   ")
    assert blank.status_code == 422


@needs_ffmpeg
def test_complete_aligns_mixes_validates_and_keeps_the_microphone(
    client: TestClient, session: Session
) -> None:
    """Save a two-track recording: video copied, later microphone delayed and mixed, no job."""
    recording_id, project_id = _create_recording(client)
    # The microphone started 500 ms after the screen track.
    _upload_track(client, recording_id, "screen", _screen(4.0), offset=200)
    _upload_track(client, recording_id, "microphone", _microphone(3.0), chunks=4, offset=700)

    response = _complete(client, recording_id, name="  Weekly sync  ")
    assert response.status_code == 200, response.text
    project = response.json()
    assert project["name"] == "Weekly sync"
    assert project["status"] == "uploaded"
    assert project["activeJobId"] is None
    assert project["recording"] == {"id": recording_id, "state": "completed", "interrupted": False}
    asset = project["videoAsset"]
    assert asset["mimeType"] == "video/webm"
    assert asset["originalFilename"] == "Weekly sync.webm"
    assert (asset["width"], asset["height"]) == (320, 240)
    assert asset["durationSeconds"] == pytest.approx(4.0, abs=0.15)
    assert session.scalar(select(func.count()).select_from(ProcessingJob)) == 0

    source = _source_dir(project_id) / "source.webm"
    microphone = _source_dir(project_id) / "microphone.webm"
    assert sorted(path.name for path in _source_dir(project_id).iterdir()) == [
        "microphone.webm",
        "source.webm",
    ]
    assert asset["sizeBytes"] == source.stat().st_size
    # Ends with the video although the delayed microphone runs to 3.5 s only.
    assert _duration(source) == pytest.approx(4.0, abs=0.15)

    mixed = _audio(source)
    assert _tone(mixed, 440, 0.05, 0.45) > PRESENT
    assert _tone(mixed, 1000, 0.05, 0.45) < ABSENT
    # Both sources keep their recorded level (1/8) in the mix instead of being halved.
    assert _tone(mixed, 1000, 0.7, 3.3) == pytest.approx(0.125, abs=0.025)
    assert _tone(mixed, 440, 0.7, 3.3) == pytest.approx(0.125, abs=0.025)
    assert _tone(mixed, 1000, 3.65, 3.95) < ABSENT

    kept = _audio(microphone)
    assert _tone(kept, 1000, 0.05, 0.45) < ABSENT
    assert _tone(kept, 1000, 0.7, 3.3) > PRESENT

    assert _chunk_rows(session, recording_id) == 0
    assert not (DATA_ROOT / "projects" / project_id / "recording").exists()
    state = client.get(f"/api/v1/recordings/{recording_id}").json()
    assert state["state"] == "completed"
    assert state["storedBytes"] == 0

    streamed = client.get(f"/api/v1/projects/{project_id}/video")
    assert streamed.status_code == 200
    assert streamed.content == source.read_bytes()
    again = _complete(client, recording_id)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "INVALID_PROJECT_STATE"
    chunk = client.put(
        f"/api/v1/recordings/{recording_id}/tracks/screen/chunks/9",
        content=b"late",
        headers={"Content-Type": "video/webm"},
    )
    assert chunk.status_code == 409
    discard = client.delete(f"/api/v1/recordings/{recording_id}")
    assert discard.status_code == 409


@needs_ffmpeg
def test_an_earlier_microphone_loses_its_head(client: TestClient) -> None:
    """Trim a microphone that started 800 ms before the screen, so its tone moves earlier."""
    recording_id, project_id = _create_recording(client)
    _upload_track(client, recording_id, "screen", _screen(3.0, audio=False), offset=800)
    # The tone starts 1.0 s into the microphone, which is 0.2 s into the screen track.
    _upload_track(client, recording_id, "microphone", _microphone(3.0, tone_from=1.0), offset=0)

    response = _complete(client, recording_id)
    assert response.status_code == 200, response.text
    assert response.json()["name"].startswith("Recording ")

    mixed = _audio(_source_dir(project_id) / "source.webm")
    assert _tone(mixed, 1000, 0.0, 0.15) < ABSENT
    assert _tone(mixed, 1000, 0.3, 0.9) > PRESENT
    # Without system audio the microphone alone is the soundtrack; it ends at 2.2 s.
    assert _tone(mixed, 440, 0.3, 0.9) < ABSENT
    assert _duration(_source_dir(project_id) / "source.webm") == pytest.approx(3.0, abs=0.15)


@needs_ffmpeg
def test_system_audio_alone_is_copied_and_no_microphone_file_is_written(
    client: TestClient,
) -> None:
    """Save a recording without a microphone track: the screen audio stays as recorded."""
    recording_id, project_id = _create_recording(client)
    _upload_track(client, recording_id, "screen", _screen(2.0))

    response = _complete(client, recording_id)
    assert response.status_code == 200, response.text
    assert [path.name for path in _source_dir(project_id).iterdir()] == ["source.webm"]
    mixed = _audio(_source_dir(project_id) / "source.webm")
    assert _tone(mixed, 440, 0.2, 1.8) > PRESENT


@needs_ffmpeg
def test_a_recording_without_any_audio_is_refused(client: TestClient, session: Session) -> None:
    """Refuse a silent screen recording without microphone with `VIDEO_AUDIO_TRACK_MISSING`."""
    recording_id, project_id = _create_recording(client)
    _upload_track(client, recording_id, "screen", _screen(2.0, audio=False), chunks=3)

    response = _complete(client, recording_id)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VIDEO_AUDIO_TRACK_MISSING"
    assert list(_source_dir(project_id).iterdir()) == []
    assert _chunk_rows(session, recording_id) == 3
    discard = client.delete(f"/api/v1/recordings/{recording_id}")
    assert discard.status_code == 204


@needs_ffmpeg
def test_stop_at_gap_saves_what_was_recorded_before_the_first_missing_chunk(
    client: TestClient,
) -> None:
    """Save an interrupted recording up to its first hole when the caller asks for it."""
    recording_id, project_id = _create_recording(client)
    _upload_track(client, recording_id, "screen", _screen(6.0), chunks=6, skip=frozenset({3}))
    _upload_track(client, recording_id, "microphone", _microphone(6.0), chunks=3)

    refused = _complete(client, recording_id)
    assert refused.status_code == 409
    response = _complete(client, recording_id, stopAtGap=True)
    assert response.status_code == 200, response.text
    duration = response.json()["videoAsset"]["durationSeconds"]
    # Three of six byte ranges hold roughly the first half of the stream.
    assert 1.0 < duration < 4.5
    mixed = _audio(_source_dir(project_id) / "source.webm")
    assert _tone(mixed, 1000, 0.2, duration - 0.2) > PRESENT


@needs_ffmpeg
def test_non_webm_video_is_copied_into_matroska(client: TestClient) -> None:
    """Keep an H.264 screen recording without re-encoding by writing `source.mkv`."""
    recording_id, project_id = _create_recording(client)
    screen = _encode(
        ["testsrc=size=320x240:rate=15"],
        ["-c:v", "libx264", "-pix_fmt", "yuv420p"],
        2.0,
        "matroska",
    )
    _upload_track(client, recording_id, "screen", screen, chunks=2)
    _upload_track(client, recording_id, "microphone", _microphone(2.0), chunks=2)

    response = _complete(client, recording_id)
    assert response.status_code == 200, response.text
    assert response.json()["videoAsset"]["mimeType"] == "video/x-matroska"
    assert (_source_dir(project_id) / "source.mkv").is_file()


@needs_ffmpeg
def test_a_chunk_stored_during_the_conversion_cancels_the_save(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse to save when a chunk commits while FFmpeg runs; leave no file and nothing saved."""
    recording_id, project_id = _create_recording(client)
    _upload_track(client, recording_id, "screen", _screen(2.0), chunks=2)
    original = recording_completion._validate_source

    def validate_then_store_a_late_chunk(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        metadata = original(*args, **kwargs)  # type: ignore[arg-type]
        with SessionLocal() as other:
            other.add(
                RecordingChunk(
                    recording_id=recording_id,
                    track=RecordingTrack.SCREEN,
                    sequence=2,
                    size_bytes=1,
                    sha256="0" * 64,
                )
            )
            other.commit()
        return metadata

    monkeypatch.setattr(recording_completion, "_validate_source", validate_then_store_a_late_chunk)
    response = _complete(client, recording_id)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RECORDING_INCOMPLETE"
    assert list(_source_dir(project_id).iterdir()) == []
    assert _chunk_rows(session, recording_id) == 3
    project = client.get(f"/api/v1/projects/{project_id}").json()
    assert project["status"] == "draft"
    assert project["videoAsset"] is None
    assert project["recording"]["state"] == "recording"
