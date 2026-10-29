"""Project glossary: normalization, the Whisper hint, the API contract and the migration (P09.1)."""

from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from alembic import command
from app.config import get_settings
from app.models import ProcessingJob, Project, ProjectState
from app.services import processing
from app.services.glossary import (
    MAX_GLOSSARY_TERM_LENGTH,
    MAX_GLOSSARY_TERMS,
    MAX_HINT_CHARACTERS,
    build_transcription_hint,
    normalize_glossary,
)
from app.services.pipeline import transcribe_audio
from app.tasks.celery_app import process_project_task
from tests.test_jobs_and_processing import _uploaded_project

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _wav(path: Path) -> Path:
    """Write a short silent mono 16 kHz WAV, the input `transcribe_audio` requires."""
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16_000)
        output.writeframes(b"\x00\x00" * 1_600)
    return path


class RecordingModel:
    """Fake Whisper model that keeps the keyword arguments of each `transcribe` call."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.calls: list[dict[str, object]] = []

    def transcribe(self, _samples: object, **options: object) -> tuple[object, object]:
        """Record the options and return no segments with a minimal info object."""
        self.calls.append(options)
        return iter([]), SimpleNamespace(language="en", language_probability=1, duration=0.1)


def _transcribe_with(tmp_path: Path, glossary: list[str] | None) -> dict[str, object]:
    """Run `transcribe_audio` against the recording model and return the options it received."""
    model = RecordingModel()
    kwargs = {} if glossary is None else {"glossary": glossary}
    transcribe_audio(
        _wav(tmp_path / "audio.wav"),
        model_path=tmp_path / "model",
        model_factory=lambda *_, **__: model,
        **kwargs,
    )
    (options,) = model.calls
    return options


def test_normalize_trims_collapses_and_drops_blank_and_repeated_terms() -> None:
    """Keep the first spelling of a term, compare case-insensitively and preserve order."""
    assert normalize_glossary(
        ["  Ada  Lovelace ", "", "   ", "ada lovelace", "Narrastep", "ADA"]
    ) == [
        "Ada Lovelace",
        "Narrastep",
        "ADA",
    ]


@pytest.mark.parametrize("terms", [[], ["", "  "]])
def test_hint_is_none_for_an_empty_glossary(terms: list[str]) -> None:
    """Send no hint at all when there is nothing to hint."""
    assert build_transcription_hint(terms) is None


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (["Kubernetes", "Narrastep", "OAuth"], "Kubernetes, Narrastep, OAuth"),
        (
            ["Giovanni Battista", "caffè", "Città della Pieve"],
            "Giovanni Battista, caffè, Città della Pieve",
        ),
    ],
)
def test_hint_joins_english_and_italian_terms_without_adding_words(
    terms: list[str], expected: str
) -> None:
    """Keep the hint language-neutral so it suits either spoken language."""
    assert build_transcription_hint(terms) == expected


def test_long_glossary_hint_is_a_prefix_that_fits_the_limit() -> None:
    """Cut a long glossary between whole terms, keeping the earliest (most important) ones."""
    terms = [f"Term{index:03d}" + "x" * 40 for index in range(MAX_GLOSSARY_TERMS)]
    hint = build_transcription_hint(terms)
    assert hint is not None
    assert len(hint) <= MAX_HINT_CHARACTERS
    included = hint.split(", ")
    assert included == terms[: len(included)]
    assert 1 < len(included) < len(terms)
    # One more term would have exceeded the limit.
    assert len(hint) + 2 + len(terms[len(included)]) > MAX_HINT_CHARACTERS


def test_whisper_receives_the_glossary_as_hotwords(tmp_path: Path) -> None:
    """Pass the saved terms to the model as `hotwords` for English and Italian speech alike."""
    options = _transcribe_with(tmp_path, ["Narrastep", "Città della Pieve"])
    assert options["hotwords"] == "Narrastep, Città della Pieve"


@pytest.mark.parametrize("glossary", [None, []])
def test_whisper_gets_no_hotwords_option_without_a_glossary(
    tmp_path: Path, glossary: list[str] | None
) -> None:
    """Leave decoding exactly as before when the glossary is empty."""
    assert "hotwords" not in _transcribe_with(tmp_path, glossary)


def test_whisper_hint_stays_within_the_limit_for_a_long_glossary(tmp_path: Path) -> None:
    """Never hand the model more than the hint limit, however long the saved glossary is."""
    terms = [f"Name{index}" + "y" * 60 for index in range(MAX_GLOSSARY_TERMS)]
    hotwords = _transcribe_with(tmp_path, terms)["hotwords"]
    assert isinstance(hotwords, str) and len(hotwords) <= MAX_HINT_CHARACTERS


def test_project_stores_a_normalized_glossary(client: TestClient) -> None:
    """Normalize on create, replace on update and clear with an empty list."""
    created = client.post(
        "/api/v1/projects",
        json={
            "name": "Glossary",
            "glossary": [" Narrastep ", "narrastep", "Città  della Pieve", ""],
        },
    )
    assert created.status_code == 201
    assert created.json()["glossary"] == ["Narrastep", "Città della Pieve"]
    project_id = created.json()["id"]

    replaced = client.patch(f"/api/v1/projects/{project_id}", json={"glossary": ["Valkey"]})
    assert replaced.status_code == 200
    assert replaced.json()["glossary"] == ["Valkey"]
    untouched = client.patch(f"/api/v1/projects/{project_id}", json={"name": "Renamed"})
    assert untouched.json()["glossary"] == ["Valkey"]
    cleared = client.patch(f"/api/v1/projects/{project_id}", json={"glossary": []})
    assert cleared.json()["glossary"] == []
    assert client.get(f"/api/v1/projects/{project_id}").json()["glossary"] == []


def test_project_without_a_glossary_returns_an_empty_list(client: TestClient) -> None:
    """Default to an empty list instead of null so clients need no special case."""
    created = client.post("/api/v1/projects", json={"name": "Plain"})
    assert created.json()["glossary"] == []


@pytest.mark.parametrize(
    "glossary",
    [
        ["x" * (MAX_GLOSSARY_TERM_LENGTH + 1)],
        [f"term {index}" for index in range(MAX_GLOSSARY_TERMS + 1)],
    ],
)
def test_glossary_limits_are_enforced(client: TestClient, glossary: list[str]) -> None:
    """Reject too many or too long terms with the stable validation error."""
    created = client.post("/api/v1/projects", json={"name": "Limits", "glossary": glossary})
    assert created.status_code == 422
    assert created.json()["error"]["code"] == "VALIDATION_ERROR"
    project = client.post("/api/v1/projects", json={"name": "Limits"}).json()
    patched = client.patch(f"/api/v1/projects/{project['id']}", json={"glossary": glossary})
    assert patched.status_code == 422
    nulled = client.patch(f"/api/v1/projects/{project['id']}", json={"glossary": None})
    assert nulled.status_code == 422


def test_glossary_at_the_limits_is_accepted(client: TestClient) -> None:
    """Accept exactly the maximum number of terms of the maximum length."""
    terms = [
        f"{index:03d}" + "z" * (MAX_GLOSSARY_TERM_LENGTH - 3) for index in range(MAX_GLOSSARY_TERMS)
    ]
    created = client.post("/api/v1/projects", json={"name": "Max", "glossary": terms})
    assert created.status_code == 201
    assert created.json()["glossary"] == terms


def test_glossary_cannot_change_after_transcription(client: TestClient, session: Session) -> None:
    """Keep the glossary tied to the transcript, like the spoken language."""
    project = Project(name="Done", state=ProjectState.COMPLETED, glossary=["Old"])
    session.add(project)
    session.commit()

    refused = client.patch(f"/api/v1/projects/{project.id}", json={"glossary": ["New"]})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "INVALID_PROJECT_STATE"
    same = client.patch(f"/api/v1/projects/{project.id}", json={"glossary": ["Old"]})
    assert same.status_code == 200


def test_process_body_stores_the_glossary_with_the_queued_state(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Let the Process dialog set the glossary of a recording atomically with the queued job."""
    project, _ = _uploaded_project(session)
    monkeypatch.setattr(process_project_task, "apply_async", lambda **_: None)

    queued = client.post(
        f"/api/v1/projects/{project.id}/process", json={"glossary": [" Narrastep ", "narrastep"]}
    )
    assert queued.status_code == 202
    session.expire_all()
    stored = session.get(Project, project.id)
    assert stored is not None and stored.glossary == ["Narrastep"]


def test_processing_passes_the_saved_glossary_to_the_transcriber(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hand the stored terms to `transcribe_audio` and record only their count in the metrics."""

    project, _ = _uploaded_project(session)
    project.state = ProjectState.QUEUED
    project.glossary = ["Narrastep", "Città della Pieve"]
    job = ProcessingJob(project_id=project.id)
    session.add(job)
    session.commit()
    received: dict[str, object] = {}

    def fake_transcribe(*_: object, **kwargs: object) -> None:
        """Capture the keyword arguments and fail, which processing records as a failed job."""
        received.update(kwargs)
        raise RuntimeError("stop after the transcriber call")

    def fake_audio(_: Path, destination: Path, **__: object) -> Path:
        """Create the audio file the transcriber would read."""
        _wav(destination)
        return destination

    metadata = SimpleNamespace(duration_seconds=4)
    monkeypatch.setattr(processing, "validate_video", lambda *_a, **_k: metadata)
    monkeypatch.setattr(processing, "extract_pcm_audio", fake_audio)
    monkeypatch.setattr(processing, "transcribe_audio", fake_transcribe)
    monkeypatch.setattr("app.services.progress.publish_job_event", lambda _job: None)

    processing.perform_processing(job.id, get_settings())
    assert received["glossary"] == ["Narrastep", "Città della Pieve"]


def test_glossary_revision_adds_an_empty_list_and_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give existing projects an empty glossary on upgrade and drop the column on downgrade."""
    data_root = tmp_path / "migration-glossary"
    database_url = f"sqlite:///{(data_root / 'narrastep.db').as_posix()}"
    monkeypatch.setenv("NARRASTEP_DATA_DIR", str(data_root))
    monkeypatch.setenv("NARRASTEP_DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    try:
        command.upgrade(config, "20261029_0007")
        engine = create_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, source_language, output_mode, state,"
                    " created_at, updated_at) VALUES ('p', 'P', 'auto', 'meeting', 'review',"
                    " '2026-10-29', '2026-10-29')"
                )
            )
        engine.dispose()

        command.upgrade(config, "head")
        command.check(config)
        engine = create_engine(database_url)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT glossary FROM projects")).scalar() == "[]"
        engine.dispose()

        command.downgrade(config, "20261029_0007")
        engine = create_engine(database_url)
        with engine.connect() as connection:
            columns = {row[1] for row in connection.execute(text("PRAGMA table_info(projects)"))}
        engine.dispose()
        assert "glossary" not in columns
    finally:
        get_settings.cache_clear()
