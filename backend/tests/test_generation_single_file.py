"""Single-file Markdown of a generated document with its screenshots embedded (P06.4d)."""

from __future__ import annotations

import base64
import io
import re
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import (
    Generation,
    JobStatus,
    OutputMode,
    Project,
    ProjectState,
    Screenshot,
    SegmentScreenshotAssociation,
    SpeakerLabel,
    SpeakerSource,
    TranscriptSegment,
)
from app.services.generation_markdown import embed_screenshots
from app.services.workspaces import create_project_workspace

CITED = "step-001_00m00s.png"
UNKNOWN = "step-099_09m09s.png"
DATA_IMAGE = re.compile(r"!\[([^\]]*)\]\(data:image/png;base64,([A-Za-z0-9+/=]+)\)")
DOCUMENT = (
    "<!--\nnarrastep generated document\n-->\n\n"
    "# Weekly sync\n\n"
    f"The budget was opened [00:00] ({CITED}).\n\n"
    f"The same screen came back later ({CITED}).\n\n"
    f"A screen nobody stored is cited here ({UNKNOWN}).\n"
)


def png(width: int, height: int) -> bytes:
    """Return the bytes of a generated PNG."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (90, 120, 150)).save(buffer, format="PNG")
    return buffer.getvalue()


def project_with_screenshot(session: Session, size: tuple[int, int] = (32, 18)) -> Project:
    """Create a project in review with one reviewed segment and one stored screenshot."""
    project = Project(name="Weekly sync", state=ProjectState.REVIEW, output_mode=OutputMode.MEETING)
    session.add(project)
    session.flush()
    root = get_settings().data_root
    workspace = create_project_workspace(root, project.id)
    segment = TranscriptSegment(
        project_id=project.id,
        sequence=0,
        start_seconds=0.0,
        end_seconds=9.0,
        raw_text="Budget",
        reviewed_text="Budget",
        raw_words=[],
        speaker=SpeakerLabel.YOU,
        speaker_source=SpeakerSource.AUTOMATIC,
    )
    session.add(segment)
    session.flush()
    image_path = workspace / "screenshots" / "frame-0001.png"
    Image.new("RGB", size, (90, 120, 150)).save(image_path)
    screenshot = Screenshot(
        project_id=project.id,
        relative_path=image_path.relative_to(root).as_posix(),
        timestamp_seconds=1.0,
        perceptual_hash="0123456789abcdef",
        quality_score=0.9,
        width=size[0],
        height=size[1],
    )
    session.add(screenshot)
    session.flush()
    session.add(
        SegmentScreenshotAssociation(
            segment_id=segment.id,
            screenshot_id=screenshot.id,
            is_primary=True,
            rank=0,
            score=0.9,
        )
    )
    session.commit()
    return project


def add_generation(
    session: Session,
    project: Project,
    text: str = DOCUMENT,
    *,
    status: JobStatus = JobStatus.COMPLETED,
    with_file: bool = True,
) -> Generation:
    """Store a generation row and, unless told not to, its `document.md` in the workspace."""
    generation = Generation(
        project_id=project.id,
        model_id="gemma-4-e2b",
        status=status,
        prompt_version="generation-prompts-v1",
        completed_at=datetime(2026, 10, 31, 9, 0, tzinfo=UTC),
    )
    session.add(generation)
    session.flush()
    root = get_settings().data_root
    document = (
        create_project_workspace(root, project.id) / "generations" / generation.id / "document.md"
    )
    if with_file:
        document.parent.mkdir(parents=True)
        document.write_bytes(text.encode("utf-8"))
    generation.relative_path = document.relative_to(root).as_posix()
    session.commit()
    return generation


def single_file_url(generation: Generation) -> str:
    """Return the endpoint of the single-file Markdown."""
    return f"/api/v1/generations/{generation.id}/document/single-file"


def test_the_download_embeds_each_cited_screenshot_once_and_keeps_unknown_names_as_text(
    client: TestClient, session: Session
) -> None:
    """Embed the stored screenshot as PNG data, leave the unmatched name alone, keep the file."""
    project = project_with_screenshot(session)
    generation = add_generation(session, project)
    stored_before = (get_settings().data_root / str(generation.relative_path)).read_bytes()

    response = client.get(single_file_url(generation))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        'attachment; filename="weekly-sync_document-with-images.md"'
    )
    images = DATA_IMAGE.findall(response.text)
    assert [name for name, _ in images] == [CITED]
    decoded = Image.open(io.BytesIO(base64.b64decode(images[0][1])))
    assert decoded.format == "PNG" and decoded.size == (32, 18)
    assert response.text.count("data:image/png;base64,") == 1
    # The cited line stays; the image follows it as its own paragraph.
    assert f"The budget was opened [00:00] ({CITED}).\n\n![{CITED}](data:image/png" in response.text
    assert f"A screen nobody stored is cited here ({UNKNOWN})." in response.text
    assert f"![{UNKNOWN}]" not in response.text
    assert response.text.startswith("<!--\nnarrastep generated document\n-->")
    assert (get_settings().data_root / str(generation.relative_path)).read_bytes() == stored_before


def test_an_existing_image_link_gets_its_target_replaced_by_the_data_uri(
    client: TestClient, session: Session
) -> None:
    """Keep the model's own alt text and add no second copy of the image."""
    project = project_with_screenshot(session)
    text = f"# Plan\n\nBudget view:\n\n![Budget screen]({CITED})\n\nLater, see {CITED} again.\n"
    generation = add_generation(session, project, text)

    response = client.get(single_file_url(generation))

    assert response.status_code == 200
    assert "![Budget screen](data:image/png;base64," in response.text
    assert response.text.count("data:image/png;base64,") == 1
    assert "Later, see step-001_00m00s.png again.\n" in response.text


def test_large_screenshots_are_downscaled_before_they_are_embedded(
    client: TestClient, session: Session
) -> None:
    """Embed at most the longest edge the model saw, so the file stays a sensible size."""
    project = project_with_screenshot(session, size=(2048, 1024))
    generation = add_generation(session, project)

    response = client.get(single_file_url(generation))

    encoded = DATA_IMAGE.findall(response.text)[0][1]
    embedded = Image.open(io.BytesIO(base64.b64decode(encoded)))
    assert max(embedded.size) == get_settings().generation_image_max_edge


def test_a_document_without_screenshot_names_downloads_unchanged(
    client: TestClient, session: Session
) -> None:
    """Return the stored text byte for byte when there is nothing to embed."""
    project = project_with_screenshot(session)
    generation = add_generation(session, project, "# Plan\n\nNo screens were cited.\n")

    response = client.get(single_file_url(generation))

    assert response.status_code == 200
    assert response.content == b"# Plan\n\nNo screens were cited.\n"


def test_a_screenshot_whose_file_is_gone_stays_as_text_and_the_download_succeeds(
    client: TestClient, session: Session
) -> None:
    """A missing image never fails the download or reads another path."""
    project = project_with_screenshot(session)
    generation = add_generation(session, project)
    (get_settings().data_root / "projects" / project.id / "screenshots" / "frame-0001.png").unlink()

    response = client.get(single_file_url(generation))

    assert response.status_code == 200
    assert "data:image" not in response.text
    assert f"({CITED})." in response.text


@pytest.mark.parametrize(
    ("case", "status", "code"),
    [
        ("missing", 404, "GENERATION_NOT_FOUND"),
        ("running", 409, "GENERATION_NOT_READY"),
        ("failed", 409, "GENERATION_NOT_READY"),
        ("file_gone", 404, "PROJECT_ASSET_MISSING"),
    ],
)
def test_a_missing_or_unfinished_generation_is_refused(
    client: TestClient, session: Session, case: str, status: int, code: str
) -> None:
    """Answer with the same stable codes as the plain document."""
    project = project_with_screenshot(session)
    url = "/api/v1/generations/00000000-0000-0000-0000-000000000000/document/single-file"
    if case == "running":
        url = single_file_url(add_generation(session, project, status=JobStatus.RUNNING))
    elif case == "failed":
        url = single_file_url(add_generation(session, project, status=JobStatus.FAILED))
    elif case == "file_gone":
        url = single_file_url(add_generation(session, project, with_file=False))

    response = client.get(url)

    assert response.status_code == status
    assert response.json()["error"]["code"] == code


def test_embedding_handles_windows_line_endings_and_a_last_line_without_a_newline() -> None:
    """Insert the image after the citing line with that line's own ending."""
    images = {CITED: b"PNG"}
    uri = "data:image/png;base64," + base64.b64encode(b"PNG").decode("ascii")

    crlf = embed_screenshots(f"See {CITED}.\r\nNext\r\n", images)
    bare = embed_screenshots(f"See {CITED}.", images)

    assert crlf == f"See {CITED}.\r\n\r\n![{CITED}]({uri})\r\nNext\r\n"
    assert bare == f"See {CITED}.\n\n![{CITED}]({uri})\n"
    assert embed_screenshots("Nothing here\n", images) == "Nothing here\n"
    assert embed_screenshots(f"See {CITED}.\n", {}) == f"See {CITED}.\n"
