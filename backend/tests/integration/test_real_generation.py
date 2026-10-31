"""Opt-in proof of the generation job with the real `llama-server` and the pinned E2B files.

A short generated meeting goes through `perform_generation`, the path a Celery task takes, with
nothing replaced but the server folder. Without `NARRASTEP_RUN_REAL_GENERATION_TESTS=1` the real
model tests are deselected (`conftest.py`). With it they need a folder holding both pinned files in
`NARRASTEP_REAL_GENERATION_MODEL_DIR`; the server comes from `NARRASTEP_REAL_LLAMA_SERVER_DIR`, or
from the backend image's folder. The checks of a document are plain functions, and their tests run
without the opt-in. Only generated text and images are used.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import (
    Generation,
    JobStatus,
    ModelInstallState,
    OutputMode,
    Project,
    ProjectState,
    Screenshot,
    SegmentScreenshotAssociation,
    TranscriptSegment,
)
from app.services import generation_job
from app.services.export_guides import guide_for
from app.services.generation_input import GenerationInput, build_generation_input
from app.services.generation_job import (
    GENERATION_PROMPT_VERSION,
    perform_generation,
    pinned_model_files,
)
from app.services.llama_server import LLAMA_SERVER_DIR, check_pinned_llama_server
from app.services.model_catalog import GEMMA_4_E2B
from app.services.model_download import model_directory
from app.services.model_manager import transition_model
from app.services.workspaces import create_project_workspace

# Read by `conftest.py`; the test folder is not a package, so the name cannot be imported from it.
REAL_GENERATION_OPT_IN = "NARRASTEP_RUN_REAL_GENERATION_TESTS"
SCREENSHOT_SECONDS = 22.0
# Start, end and text of each segment. The gaps between segments are deliberate: a citation that
# falls in one, or after the end, was invented by the model.
MEETING: tuple[tuple[float, float, str], ...] = (
    (5.0, 14.0, "Welcome to the release meeting. Today we choose the date of the mobile release."),
    (
        20.0,
        31.0,
        "The slide on screen shows the test results. We decided to ship the release on Friday.",
    ),
    (
        40.0,
        52.0,
        "Marta will update the release notes before Friday. The translation budget is unclear.",
    ),
    (65.0, 78.0, "Nobody knows yet who approves the translation budget, so that stays open."),
)
# Small enough to force a merge of this meeting: a part holds the image or a few segments.
MERGE_PART_BUDGET_TOKENS = 320

_CLOCK = re.compile(r"(?<![\d:])\d{1,3}:\d{2}(?::\d{2})?(?![\d:])")
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s+)?(?:\d+[.)]\s+)?\*{0,2}(?:\d+[.)]\s+)?(?P<title>[^*#\n]+?):?\*{0,2}:?\s*$"
)


def document_body(document: str) -> str:
    """Return the model's text: the document without its leading provenance comment."""
    return _COMMENT.sub("", document, count=1)


def cited_seconds(body: str) -> list[int]:
    """Return every `mm:ss` or `h:mm:ss` time written in `body`, in seconds, bracketed or not."""
    times: list[int] = []
    for match in _CLOCK.findall(body):
        seconds = 0
        for field in match.split(":"):
            seconds = seconds * 60 + int(field)
        times.append(seconds)
    return times


def invented_seconds(
    body: str, segments: tuple[tuple[float, float, str], ...], screenshots: tuple[float, ...]
) -> list[int]:
    """Return the cited times that are in no segment and on no screenshot.

    Times are compared in whole seconds, because the model only sees `mm:ss`.
    """
    spans = [(int(start), int(end)) for start, end, _ in segments]
    moments = {int(item) for item in screenshots}
    return [
        second
        for second in cited_seconds(body)
        if second not in moments and not any(start <= second <= end for start, end in spans)
    ]


def section_problems(body: str, sections: tuple[str, ...]) -> list[str]:
    """Return what is wrong with the headings of `body`: empty when all appear, in order.

    A heading is a line that holds only a title, written as `# Title`, `**Title**` or `1. Title`,
    with any case. Each problem names the missing section.
    """
    titles = [
        match["title"].strip().casefold()
        for match in (_HEADING.match(line) for line in body.splitlines())
        if match is not None
    ]
    problems: list[str] = []
    position = 0
    for section in sections:
        wanted = section.casefold()
        found = next(
            (index for index in range(position, len(titles)) if titles[index] == wanted), None
        )
        if found is None:
            problems.append(section)
        else:
            position = found + 1
    return problems


def test_invented_seconds_finds_times_outside_the_evidence() -> None:
    """A time in a gap or after the end is invented; one in a segment or on a screenshot is not."""
    body = "Fine [00:05] and [00:31-00:40], gap [00:17], late 01:19, long 00:01:30, shot [00:22]."
    assert invented_seconds(body, MEETING, (SCREENSHOT_SECONDS,)) == [17, 79, 90]
    assert invented_seconds("No citation here.", MEETING, ()) == []


def test_section_problems_needs_every_heading_in_order() -> None:
    """Sections count only as headings and only in the given order."""
    sections = guide_for(OutputMode.MEETING.value).sections
    good = "# Summary\nx\n## 2. Decisions\nx\n**Action items**\nx\n3. Open questions:\nx\n"
    assert section_problems(f"{good}## Topics with time\n", sections) == []
    assert section_problems(good, sections) == ["Topics with time"]
    assert section_problems(f"The Topics with time are here.\n{good}", sections) == [
        "Topics with time"
    ]
    swapped = "# Decisions\n# Summary\n# Action items\n# Open questions\n# Topics with time\n"
    assert section_problems(swapped, sections) == ["Decisions"]


def test_document_body_keeps_times_out_of_the_provenance_comment() -> None:
    """The comment before the model's text is not part of what is checked."""
    assert document_body("<!--\npromptVersion: 12:34\n-->\n\n# Summary\n").strip() == "# Summary"


def _collected_tests(*, opted_in: bool) -> str:
    """Return the report of a child pytest that only collects this file; raises when it fails."""
    environment = {key: value for key, value in os.environ.items() if key != REAL_GENERATION_OPT_IN}
    if opted_in:
        environment[REAL_GENERATION_OPT_IN] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-p", "no:cacheprovider", __file__],
        cwd=Path(__file__).parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    ).stdout


def test_real_model_tests_are_off_until_the_opt_in_is_set() -> None:
    """A default run never reaches the model; the variable brings back both scenarios."""
    real_test = test_real_model_writes_the_sections_and_cites_only_times_that_exist.__name__
    by_default = _collected_tests(opted_in=False)
    assert real_test not in by_default
    assert "2 deselected" in by_default
    assert test_document_body_keeps_times_out_of_the_provenance_comment.__name__ in by_default

    opted_in = _collected_tests(opted_in=True)
    assert f"{real_test}[one-part]" in opted_in
    assert f"{real_test}[merged-parts]" in opted_in


def _sha256(path: Path) -> str:
    """Return the SHA-256 of a file, read in chunks because the files take gigabytes."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _running_server_processes() -> list[str]:
    """Return the identifiers of processes whose command is `llama-server`, on Linux."""
    found: list[str] = []
    for entry in Path("/proc").glob("[0-9]*/comm"):
        try:
            if entry.read_text(encoding="utf-8").strip().startswith("llama-server"):
                found.append(entry.parent.name)
        except OSError:
            continue
    return found


@pytest.fixture
def real_files() -> tuple[Path, Path]:
    """Return the pinned model folder and the server folder, or fail with an actionable reason.

    This runs only after the opt-in, so missing files are a failure: somebody asked for the proof.
    Other files than the pinned ones fail too, because a run with them would prove nothing.
    """
    configured_model = os.environ.get("NARRASTEP_REAL_GENERATION_MODEL_DIR")
    if not configured_model or not Path(configured_model).is_dir():
        pytest.fail(
            "Put the pinned E2B files in a folder and set NARRASTEP_REAL_GENERATION_MODEL_DIR."
        )
    model_folder = Path(configured_model)
    server_folder = Path(os.environ.get("NARRASTEP_REAL_LLAMA_SERVER_DIR") or LLAMA_SERVER_DIR)
    if not (server_folder / "llama-server").is_file():
        pytest.fail("Run in the backend image or set NARRASTEP_REAL_LLAMA_SERVER_DIR.")
    check_pinned_llama_server(server_folder)
    for file in GEMMA_4_E2B.files:
        assert _sha256(model_folder / file.name) == file.sha256, file.name
    return model_folder, server_folder


def _meeting_project(session: Session, settings: Settings) -> Project:
    """Store a reviewed meeting with the segments of `MEETING` and one generated slide."""
    project = Project(
        name="Release meeting", state=ProjectState.REVIEW, output_mode=OutputMode.MEETING
    )
    session.add(project)
    session.flush()
    workspace = create_project_workspace(settings.data_root, project.id)
    segments = [
        TranscriptSegment(
            project_id=project.id,
            sequence=index,
            start_seconds=start,
            end_seconds=end,
            raw_text=text,
            reviewed_text=text,
            raw_words=[],
        )
        for index, (start, end, text) in enumerate(MEETING)
    ]
    session.add_all(segments)
    session.flush()
    image_path = workspace / "screenshots" / "frame-0001.png"
    slide = Image.new("RGB", (640, 360), (245, 245, 245))
    ImageDraw.Draw(slide).text((40, 160), "Test results: 412 passed, 0 failed", fill=(20, 20, 20))
    slide.save(image_path)
    screenshot = Screenshot(
        project_id=project.id,
        relative_path=image_path.relative_to(settings.data_root).as_posix(),
        timestamp_seconds=SCREENSHOT_SECONDS,
        perceptual_hash="0123456789abcdef",
        quality_score=0.9,
        width=slide.width,
        height=slide.height,
    )
    session.add(screenshot)
    session.flush()
    session.add(
        SegmentScreenshotAssociation(
            segment_id=segments[1].id,
            screenshot_id=screenshot.id,
            is_primary=True,
            rank=0,
            score=0.9,
        )
    )
    session.commit()
    return project


@pytest.mark.integration
@pytest.mark.real_generation
@pytest.mark.parametrize(
    "part_budget_tokens",
    [None, MERGE_PART_BUDGET_TOKENS],
    ids=["one-part", "merged-parts"],
)
def test_real_model_writes_the_sections_and_cites_only_times_that_exist(
    session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    real_files: tuple[Path, Path],
    part_budget_tokens: int | None,
) -> None:
    """The real server turns a generated meeting into a document, then stops.

    Prints the generated document and the time taken, so a run can be read with `-s`; both hold
    only generated text. The memory check is the real one and fails the job on too small a machine.
    """
    model_folder, server_folder = real_files
    settings = Settings(data_root=tmp_path / "data")
    for step in (
        ModelInstallState.DOWNLOADING,
        ModelInstallState.VERIFYING,
        ModelInstallState.INSTALLED,
        ModelInstallState.ENABLED,
    ):
        transition_model(session, settings, GEMMA_4_E2B.model_id, step)
    # A link, so the 3.4 GB are neither copied nor placed below the temporary folder.
    directory = model_directory(settings, GEMMA_4_E2B.model_id)
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.symlink_to(model_folder, target_is_directory=True)
    monkeypatch.setattr(generation_job, "LLAMA_SERVER_DIR", server_folder)
    if part_budget_tokens is not None:
        monkeypatch.setattr(generation_job, "PART_BUDGET_TOKENS", part_budget_tokens)

    requests: list[GenerationInput] = []

    def recording_input(*args: Any, **kwargs: Any) -> GenerationInput:
        """Return the input the job would build, after recording it in `requests`."""
        request = build_generation_input(*args, **kwargs)
        requests.append(request)
        return request

    monkeypatch.setattr(generation_job, "build_generation_input", recording_input)

    project = _meeting_project(session, settings)
    generation = Generation(
        project_id=project.id,
        model_id=GEMMA_4_E2B.model_id,
        celery_task_id="real-model",
        prompt_version=GENERATION_PROMPT_VERSION,
        model_files=pinned_model_files(GEMMA_4_E2B),
    )
    session.add(generation)
    session.commit()

    started = time.monotonic()
    perform_generation(generation.id, settings, celery_task_id="real-model")
    elapsed = time.monotonic() - started

    session.expire_all()
    stored = session.get(Generation, generation.id)
    assert stored is not None
    assert stored.status == JobStatus.COMPLETED, (stored.error_code, stored.error_message)
    assert _running_server_processes() == []
    assert stored.relative_path is not None
    document = (settings.data_root / stored.relative_path).read_text(encoding="utf-8")
    print(f"\n--- {stored.parts_total} part(s) in {elapsed:.0f} s ---\n{document}")

    assert sum(part.kind == "image" for request in requests for part in request.parts) == 1
    if part_budget_tokens is None:
        assert stored.parts_total == 1
    else:
        assert stored.parts_total is not None and stored.parts_total > 1
    assert stored.parts_completed == stored.parts_total

    body = document_body(document)
    assert section_problems(body, guide_for(OutputMode.MEETING.value).sections) == []
    assert cited_seconds(body), "The document cites no time."
    assert invented_seconds(body, MEETING, (SCREENSHOT_SECONDS,)) == []
