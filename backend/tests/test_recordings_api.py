"""Recording session API tests: chunk order, repeats, gaps, limits, resume, discard (P05.1)."""

from __future__ import annotations

import re
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models import RecordingChunk, RecordingSession, RecordingState, utc_now
from app.services.workspaces import acquire_project_operation, release_project_operation

WEBM_HEADER = b"\x1a\x45\xdf\xa3"
DATA_ROOT = get_settings().data_root


def _create_recording(client: TestClient, **body: object) -> dict[str, object]:
    """Open a recording session through the public contract and return its payload."""
    response = client.post("/api/v1/recordings", json=body or None)
    assert response.status_code == 201, response.text
    return response.json()


def _put_chunk(
    client: TestClient,
    recording_id: str,
    track: str,
    sequence: int,
    content: bytes,
    *,
    media_type: str = "video/webm;codecs=vp9,opus",
    offset: int | None = None,
) -> Any:
    """Upload one chunk body exactly as the recorder does: raw bytes, no multipart."""
    params = {"startOffsetMs": offset} if offset is not None else None
    return client.put(
        f"/api/v1/recordings/{recording_id}/tracks/{track}/chunks/{sequence}",
        content=content,
        headers={"Content-Type": media_type},
        params=params,
    )


def _chunk_body(sequence: int, size: int = 64) -> bytes:
    """Return deterministic chunk bytes; sequence 0 starts with the WebM header."""
    filler = bytes([sequence % 251]) * size
    return WEBM_HEADER + filler if sequence == 0 else filler


def _track(payload: dict[str, object], name: str) -> dict[str, object]:
    """Pick one track summary from a recording payload."""
    tracks = payload["tracks"]
    assert isinstance(tracks, list)
    return next(item for item in tracks if item["track"] == name)


def _track_dir(project_id: str, track: str) -> Path:
    """Return the chunk folder of one track."""
    return DATA_ROOT / "projects" / project_id / "recording" / track


@pytest.fixture
def small_limits() -> Generator[None]:
    """Lower the session limit to 1 MB and the chunk limit to 1 MB for size tests."""
    limited = get_settings().model_copy(update={"max_upload_mb": 1, "recording_max_chunk_mb": 1})
    app.dependency_overrides[get_settings] = lambda: limited
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_create_opens_session_with_dated_project_and_empty_tracks(client: TestClient) -> None:
    """Name the project after the date, keep it unprocessed and list both tracks empty."""
    recording = _create_recording(client)
    assert recording["state"] == "recording"
    assert recording["interrupted"] is False
    assert recording["storedBytes"] == 0
    assert recording["maxBytes"] == get_settings().max_upload_bytes
    assert recording["lastChunkAt"] is None
    assert recording["capture"] == {
        "chunkSeconds": 5,
        "videoBitsPerSecond": 2_500_000,
        "maxFrameRate": 15,
    }
    for name in ("screen", "microphone"):
        assert _track(recording, name) == {
            "track": name,
            "chunkCount": 0,
            "storedBytes": 0,
            "nextSequence": 0,
            "missingSequences": [],
            "missingCount": 0,
            "startOffsetMs": None,
        }
        assert _track_dir(str(recording["projectId"]), name).is_dir()

    project = client.get(f"/api/v1/projects/{recording['projectId']}").json()
    assert re.fullmatch(r"Recording \d{4}-\d{2}-\d{2} \d{2}:\d{2}", project["name"])
    assert project["status"] == "draft"
    assert project["mode"] is None
    assert project["videoAsset"] is None
    assert project["activeJobId"] is None
    assert project["recording"] == {
        "id": recording["id"],
        "state": "recording",
        "interrupted": False,
    }

    named = _create_recording(client, name="  Registrazione 2026-10-29 14:05 ", sourceLanguage="it")
    named_project = client.get(f"/api/v1/projects/{named['projectId']}").json()
    assert named_project["name"] == "Registrazione 2026-10-29 14:05"
    assert named_project["sourceLanguage"] == "it"

    plain = client.post("/api/v1/projects", json={"name": "Uploaded", "mode": "meeting"}).json()
    assert plain["recording"] is None


def test_out_of_order_and_repeated_chunks_are_stored_once_per_track(
    client: TestClient, session: Session
) -> None:
    """Store chunks in any order per track; an identical repeat is a 200 no-op."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    project_id = str(recording["projectId"])

    for sequence in (2, 0, 1):
        response = _put_chunk(client, recording_id, "screen", sequence, _chunk_body(sequence))
        assert response.status_code == 201, response.text
        assert response.json() == {
            "track": "screen",
            "sequence": sequence,
            "sizeBytes": len(_chunk_body(sequence)),
            "duplicate": False,
        }
    microphone = _put_chunk(
        client, recording_id, "microphone", 0, _chunk_body(0, 32), media_type="audio/webm"
    )
    assert microphone.status_code == 201

    repeat = _put_chunk(client, recording_id, "screen", 1, _chunk_body(1))
    assert repeat.status_code == 200
    assert repeat.json()["duplicate"] is True

    state = client.get(f"/api/v1/recordings/{recording_id}").json()
    screen = _track(state, "screen")
    assert screen["chunkCount"] == 3
    assert screen["nextSequence"] == 3
    assert screen["missingSequences"] == []
    assert screen["storedBytes"] == sum(len(_chunk_body(item)) for item in range(3))
    assert _track(state, "microphone")["chunkCount"] == 1
    assert state["storedBytes"] == screen["storedBytes"] + len(_chunk_body(0, 32))
    assert state["lastChunkAt"] is not None

    files = sorted(path.name for path in _track_dir(project_id, "screen").iterdir())
    assert files == ["000000.webm", "000001.webm", "000002.webm"]
    assert (_track_dir(project_id, "screen") / "000002.webm").read_bytes() == _chunk_body(2)
    assert [path.name for path in _track_dir(project_id, "microphone").iterdir()] == ["000000.webm"]
    rows = session.scalars(
        select(RecordingChunk).where(RecordingChunk.recording_id == recording_id)
    ).all()
    assert len(rows) == 4


def test_concurrent_uploads_of_both_tracks_and_repeats_store_each_chunk_once(
    client: TestClient, session: Session
) -> None:
    """Accept parallel uploads of both tracks, each sequence sent twice, without losing or
    doubling a chunk: one upload per sequence stores it, the other reports a duplicate."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    jobs = [
        (track, sequence)
        for track in ("screen", "microphone")
        for sequence in range(4)
        for _ in range(2)
    ]

    def upload(job: tuple[str, int]) -> int:
        track, sequence = job
        # Without `with`, the client skips the startup lifespan, whose cleanup would remove the
        # partial files of uploads still running, and serves each request on its own event loop.
        parallel_client = TestClient(app)
        response = _put_chunk(parallel_client, recording_id, track, sequence, _chunk_body(sequence))
        return response.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(upload, jobs))

    assert sorted(codes) == [200] * 8 + [201] * 8
    rows = session.scalars(
        select(RecordingChunk).where(RecordingChunk.recording_id == recording_id)
    ).all()
    assert sorted((row.track.value, row.sequence) for row in rows) == sorted(set(jobs))
    for track in ("screen", "microphone"):
        names = sorted(
            path.name for path in _track_dir(str(recording["projectId"]), track).iterdir()
        )
        assert names == [f"{sequence:06d}.webm" for sequence in range(4)]


def test_repeated_sequence_with_other_bytes_conflicts_and_keeps_the_first(
    client: TestClient,
) -> None:
    """Refuse different content for a stored sequence without touching the stored file."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    assert _put_chunk(client, recording_id, "screen", 0, _chunk_body(0)).status_code == 201

    conflict = _put_chunk(client, recording_id, "screen", 0, WEBM_HEADER + b"other bytes")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "RECORDING_CHUNK_CONFLICT"
    stored = _track_dir(str(recording["projectId"]), "screen") / "000000.webm"
    assert stored.read_bytes() == _chunk_body(0)
    assert [path.name for path in stored.parent.iterdir()] == ["000000.webm"]


def test_missing_chunks_are_reported_for_resume_and_can_be_filled(client: TestClient) -> None:
    """Report the gaps below the next sequence so the recorder knows what to upload again."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    for sequence in (0, 1, 4):
        assert _put_chunk(
            client, recording_id, "screen", sequence, _chunk_body(sequence)
        ).is_success

    screen = _track(client.get(f"/api/v1/recordings/{recording_id}").json(), "screen")
    assert screen["nextSequence"] == 5
    assert screen["missingSequences"] == [2, 3]
    assert screen["missingCount"] == 2

    for sequence in (3, 2):
        assert _put_chunk(
            client, recording_id, "screen", sequence, _chunk_body(sequence)
        ).is_success
    screen = _track(client.get(f"/api/v1/recordings/{recording_id}").json(), "screen")
    assert screen["missingSequences"] == []
    assert screen["missingCount"] == 0
    assert screen["chunkCount"] == 5


def test_large_gaps_are_capped_in_the_list_but_counted(client: TestClient) -> None:
    """List at most 100 missing sequences while counting all of them."""
    recording = _create_recording(client)
    response = _put_chunk(client, str(recording["id"]), "microphone", 250, b"late chunk")
    assert response.status_code == 201
    microphone = _track(client.get(f"/api/v1/recordings/{recording['id']}").json(), "microphone")
    assert microphone["missingSequences"] == list(range(100))
    assert microphone["missingCount"] == 250
    assert microphone["nextSequence"] == 251


@pytest.mark.usefixtures("small_limits")
def test_oversized_chunk_and_session_are_rejected_without_leftovers(
    client: TestClient, session: Session
) -> None:
    """Refuse a chunk above its limit and a chunk that would push the session above its limit."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    directory = _track_dir(str(recording["projectId"]), "screen")
    assert recording["maxBytes"] == 1024 * 1024

    too_big = _put_chunk(client, recording_id, "screen", 0, WEBM_HEADER + b"x" * (1024 * 1024))
    assert too_big.status_code == 413
    assert too_big.json()["error"]["code"] == "UPLOAD_TOO_LARGE"

    first = _put_chunk(client, recording_id, "screen", 0, WEBM_HEADER + b"a" * 600_000)
    assert first.status_code == 201
    over = _put_chunk(client, recording_id, "microphone", 0, WEBM_HEADER + b"b" * 600_000)
    assert over.status_code == 413
    assert over.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    assert "recording" in over.json()["error"]["message"]

    assert [path.name for path in directory.iterdir()] == ["000000.webm"]
    assert list(directory.parent.joinpath("microphone").iterdir()) == []
    rows = session.scalars(
        select(RecordingChunk).where(RecordingChunk.recording_id == recording_id)
    ).all()
    assert [(row.track.value, row.sequence) for row in rows] == [("screen", 0)]


def test_resume_after_restart_keeps_chunks_and_removes_incomplete_writes(
    client: TestClient, session: Session
) -> None:
    """Keep stored chunks across an API restart, drop partial and unlisted files, accept more."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    project_id = str(recording["projectId"])
    for sequence in (0, 1):
        assert _put_chunk(
            client, recording_id, "screen", sequence, _chunk_body(sequence)
        ).is_success
    screen_dir = _track_dir(project_id, "screen")
    (screen_dir / ".chunk-abandoned.partial").write_bytes(b"half")
    (screen_dir / "000002.webm").write_bytes(b"renamed but never committed")
    (screen_dir / "notes.txt").write_text("not ours", encoding="utf-8")

    with TestClient(app) as restarted:
        state = restarted.get(f"/api/v1/recordings/{recording_id}").json()
        assert state["state"] == "recording"
        assert _track(state, "screen")["chunkCount"] == 2
        assert sorted(path.name for path in screen_dir.iterdir()) == [
            "000000.webm",
            "000001.webm",
            "notes.txt",
        ]
        resumed = _put_chunk(restarted, recording_id, "screen", 2, _chunk_body(2))
        assert resumed.status_code == 201
        assert (screen_dir / "000002.webm").read_bytes() == _chunk_body(2)

    stored = session.get(RecordingSession, recording_id)
    assert stored is not None
    stored.last_chunk_at = utc_now() - timedelta(minutes=5)
    session.commit()
    assert client.get(f"/api/v1/recordings/{recording_id}").json()["interrupted"] is True
    listed = client.get("/api/v1/projects").json()["items"]
    assert listed[0]["recording"]["interrupted"] is True


def test_discard_removes_chunks_rows_and_project(client: TestClient, session: Session) -> None:
    """Discard an open session together with its files and the project created for it."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    project_id = str(recording["projectId"])
    assert _put_chunk(client, recording_id, "screen", 0, _chunk_body(0)).is_success
    assert _put_chunk(client, recording_id, "microphone", 0, _chunk_body(0)).is_success

    assert client.delete(f"/api/v1/recordings/{recording_id}").status_code == 204
    assert client.get(f"/api/v1/recordings/{recording_id}").json()["error"]["code"] == (
        "RECORDING_NOT_FOUND"
    )
    assert client.get(f"/api/v1/projects/{project_id}").status_code == 404
    assert not (DATA_ROOT / "projects" / project_id).exists()
    assert not (DATA_ROOT / ".trash" / project_id).exists()
    assert session.scalars(select(RecordingChunk)).all() == []
    assert client.delete(f"/api/v1/recordings/{recording_id}").status_code == 404
    late = _put_chunk(client, recording_id, "screen", 1, _chunk_body(1))
    assert late.status_code == 404


def test_discard_waits_for_other_project_operations(client: TestClient) -> None:
    """Refuse a discard while another guarded project operation holds the reservation."""
    recording = _create_recording(client)
    reservation = acquire_project_operation(DATA_ROOT, str(recording["projectId"]), "update")
    try:
        busy = client.delete(f"/api/v1/recordings/{recording['id']}")
        assert busy.status_code == 409
        assert busy.json()["error"]["code"] == "PROJECT_BUSY"
    finally:
        release_project_operation(reservation)
    assert client.delete(f"/api/v1/recordings/{recording['id']}").status_code == 204


def test_deleting_the_project_also_removes_its_recording(client: TestClient) -> None:
    """Treat the project delete endpoint as an equivalent way to discard a recording."""
    recording = _create_recording(client)
    assert _put_chunk(client, str(recording["id"]), "screen", 0, _chunk_body(0)).is_success
    assert client.delete(f"/api/v1/projects/{recording['projectId']}").status_code == 204
    assert client.get(f"/api/v1/recordings/{recording['id']}").status_code == 404


def test_session_without_microphone_track_reports_an_empty_track(client: TestClient) -> None:
    """Allow screen-only recordings: the microphone track stays listed with nothing stored."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    for sequence in range(3):
        assert _put_chunk(
            client, recording_id, "screen", sequence, _chunk_body(sequence)
        ).is_success
    state = client.get(f"/api/v1/recordings/{recording_id}").json()
    assert _track(state, "screen")["chunkCount"] == 3
    assert _track(state, "microphone") == {
        "track": "microphone",
        "chunkCount": 0,
        "storedBytes": 0,
        "nextSequence": 0,
        "missingSequences": [],
        "missingCount": 0,
        "startOffsetMs": None,
    }


def test_track_start_offsets_are_stored_once_and_conflicts_are_refused(
    client: TestClient,
) -> None:
    """Keep the first offset sent for a track, accept it again, refuse a different value."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    assert _put_chunk(client, recording_id, "screen", 0, _chunk_body(0), offset=0).is_success
    assert _put_chunk(client, recording_id, "microphone", 0, _chunk_body(0), offset=180).is_success
    assert _put_chunk(client, recording_id, "microphone", 1, _chunk_body(1), offset=180).is_success
    assert _put_chunk(client, recording_id, "screen", 1, _chunk_body(1)).is_success

    conflict = _put_chunk(client, recording_id, "microphone", 2, _chunk_body(2), offset=200)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "RECORDING_CHUNK_CONFLICT"
    state = client.get(f"/api/v1/recordings/{recording_id}").json()
    assert _track(state, "screen")["startOffsetMs"] == 0
    assert _track(state, "microphone")["startOffsetMs"] == 180
    assert _track(state, "microphone")["chunkCount"] == 2
    assert not (_track_dir(str(recording["projectId"]), "microphone") / "000002.webm").exists()


@pytest.mark.parametrize(
    ("track", "sequence", "content", "media_type", "status_code", "code"),
    [
        ("camera", 0, WEBM_HEADER, "video/webm", 422, "VALIDATION_ERROR"),
        ("..", 0, WEBM_HEADER, "video/webm", 404, "RESOURCE_NOT_FOUND"),
        ("screen", -1, WEBM_HEADER, "video/webm", 422, "VALIDATION_ERROR"),
        ("screen", 1_000_000, WEBM_HEADER, "video/webm", 422, "VALIDATION_ERROR"),
        ("screen", 0, WEBM_HEADER, "text/plain", 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("screen", 0, b"not a webm file", "video/webm", 415, "UNSUPPORTED_MEDIA_TYPE"),
        ("screen", 3, b"", "video/webm", 422, "VIDEO_VALIDATION_FAILED"),
    ],
)
def test_invalid_chunks_are_refused_without_leftovers(
    client: TestClient,
    track: str,
    sequence: int,
    content: bytes,
    media_type: str,
    status_code: int,
    code: str,
) -> None:
    """Reject unknown tracks, out-of-range sequences, wrong types, a bad header and empty bodies."""
    recording = _create_recording(client)
    response = _put_chunk(
        client, str(recording["id"]), track, sequence, content, media_type=media_type
    )
    assert response.status_code == status_code, response.text
    assert response.json()["error"]["code"] == code
    project_dir = DATA_ROOT / "projects" / str(recording["projectId"]) / "recording"
    assert [path for path in project_dir.rglob("*") if path.is_file()] == []
    state = client.get(f"/api/v1/recordings/{recording['id']}").json()
    assert state["storedBytes"] == 0


def test_saved_recording_refuses_chunks_and_discard(client: TestClient, session: Session) -> None:
    """Keep a completed session unchanged: no more chunks and no discard."""
    recording = _create_recording(client)
    recording_id = str(recording["id"])
    stored = session.get(RecordingSession, recording_id)
    assert stored is not None
    stored.state = RecordingState.COMPLETED
    session.commit()

    late = _put_chunk(client, recording_id, "screen", 0, _chunk_body(0))
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "INVALID_PROJECT_STATE"
    discard = client.delete(f"/api/v1/recordings/{recording_id}")
    assert discard.status_code == 409
    assert discard.json()["error"]["code"] == "INVALID_PROJECT_STATE"
    assert client.get(f"/api/v1/projects/{recording['projectId']}").status_code == 200
    assert client.get(f"/api/v1/recordings/{recording_id}").json()["interrupted"] is False


def test_open_recording_blocks_upload_and_processing(client: TestClient) -> None:
    """Refuse an uploaded video or processing while the project is still being recorded."""
    recording = _create_recording(client)
    project_id = recording["projectId"]
    upload = client.post(
        f"/api/v1/projects/{project_id}/video",
        files={"file": ("call.webm", WEBM_HEADER + b"data", "video/webm")},
    )
    assert upload.status_code == 409
    assert upload.json()["error"]["code"] == "INVALID_PROJECT_STATE"
    process = client.post(f"/api/v1/projects/{project_id}/process")
    assert process.status_code == 409
    assert process.json()["error"]["code"] == "PROJECT_ASSET_MISSING"


def test_unknown_recording_uses_stable_not_found_error(client: TestClient) -> None:
    """Answer every recording endpoint for an unknown ID with `RECORDING_NOT_FOUND`."""
    unknown = "00000000-0000-4000-8000-000000000000"
    for response in (
        client.get(f"/api/v1/recordings/{unknown}"),
        client.delete(f"/api/v1/recordings/{unknown}"),
        _put_chunk(client, unknown, "screen", 0, _chunk_body(0)),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "RECORDING_NOT_FOUND"


def test_create_rejects_blank_name_and_unknown_language(client: TestClient) -> None:
    """Validate the optional creation fields with the shared error envelope."""
    for body in ({"name": "   "}, {"sourceLanguage": "xx"}):
        response = client.post("/api/v1/recordings", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"
