"""Builds the single-file Markdown of a generated document, with its cited screenshots embedded."""

from __future__ import annotations

import base64
import logging
import re
import tempfile
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import Generation, Project, canonical_output_mode
from app.services.generation_input import downscale_screenshot
from app.services.generation_job import select_project_screenshots
from app.services.screenshot_materialization import decode_exact_frame, write_png_atomic
from app.services.workspaces import ensure_within_root

logger = logging.getLogger(__name__)

# The names `screenshot_file_name` gives, which is what a generated document cites.
_CITED_NAME = re.compile(r"step-\d{3,}_\d{2,}m\d{2}s\.png")
_IMAGE_LINK = re.compile(r"(!\[[^\]\n]*\]\()<?(step-\d{3,}_\d{2,}m\d{2}s\.png)>?(\))")


def embed_screenshots(markdown: str, images: Mapping[str, bytes]) -> str:
    """Return the Markdown with each known screenshot embedded once as a `data:` image.

    `images` maps a package file name to PNG bytes. An image link that already points to a known
    name gets its target replaced by the `data:` URI. A plain mention gets an image line right
    after the line that first cites it, so the cited text stays where it was. A name absent from
    `images` is left as plain text, and a name already embedded is not embedded again, which keeps
    the file small. Everything else, including line endings, is unchanged.
    """
    if not images:
        return markdown
    embedded: set[str] = set()
    uris: dict[str, str] = {}

    def uri(name: str) -> str:
        if name not in uris:
            encoded = base64.b64encode(images[name]).decode("ascii")
            uris[name] = f"data:image/png;base64,{encoded}"
        return uris[name]

    def replace_target(match: re.Match[str]) -> str:
        name = match.group(2)
        if name not in images:
            return match.group(0)
        embedded.add(name)
        return f"{match.group(1)}{uri(name)}{match.group(3)}"

    output: list[str] = []
    for line in markdown.splitlines(keepends=True):
        line = _IMAGE_LINK.sub(replace_target, line)
        output.append(line)
        added: list[str] = []
        for name in _CITED_NAME.findall(line):
            if name in images and name not in embedded:
                embedded.add(name)
                added.append(f"![{name}]({uri(name)})")
        if added:
            ending = "\r\n" if line.endswith("\r\n") else "\n"
            if not line.endswith("\n"):
                output.append(ending)
            output.extend(f"{ending}{image}{ending}" for image in added)
    return "".join(output)


def cited_screenshot_images(
    session: Session, settings: Settings, project: Project, markdown: str
) -> dict[str, bytes]:
    """Return PNG bytes, by package file name, for the screenshots the document cites.

    Only names the project's package selection produces are looked up, so a cited name never
    selects a path: the files come from the project's own stored screenshots, below the data root,
    or from a frame decoded out of its original video. Images are downscaled to the same longest
    edge the model saw. A screenshot that cannot be read is left out, so its name stays plain
    text. Returns an empty mapping when the document cites nothing or the selection fails.
    """
    cited = set(_CITED_NAME.findall(markdown))
    if not cited:
        return {}
    try:
        selected = select_project_screenshots(
            session, settings, project, canonical_output_mode(project.mode_value)
        )
    except FileNotFoundError, OSError, ValueError:
        logger.warning("Screenshots could not be selected for project %s.", project.id)
        return {}
    images: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="narrastep-embed-") as scratch:
        for item in selected:
            if item.file_name not in cited or item.file_name in images:
                continue
            try:
                path = _readable_path(settings, item.source_path)
                if path is None and (
                    item.source_video_path is not None and item.source_timestamp_seconds is not None
                ):
                    path = write_png_atomic(
                        decode_exact_frame(item.source_video_path, item.source_timestamp_seconds),
                        Path(scratch) / item.file_name,
                    )
                if path is not None:
                    images[item.file_name] = downscale_screenshot(
                        path, settings.generation_image_max_edge
                    )
            except Exception:
                # One unreadable frame must not fail the whole download.
                logger.warning("A cited screenshot of project %s was left as text.", project.id)
    return images


def build_single_file_markdown(session: Session, settings: Settings, generation: Generation) -> str:
    """Return the document of a completed generation with its cited screenshots embedded.

    Requires `generation.relative_path` to be set; the caller has checked that the generation
    completed and that the file exists. Raises `FileNotFoundError` when the document is gone.
    Embedded images come only from the project's own screenshots; no path is read from the text.
    """
    if not generation.relative_path:
        raise FileNotFoundError("The generated document is unavailable.")
    path = ensure_within_root(settings.data_root, generation.relative_path)
    markdown = path.read_text(encoding="utf-8")
    project = session.get(Project, generation.project_id)
    if project is None:
        return markdown
    return embed_screenshots(
        markdown, cited_screenshot_images(session, settings, project, markdown)
    )


def _readable_path(settings: Settings, path: Path | None) -> Path | None:
    """Return `path` when it is an existing file below the data root, else None."""
    if path is None:
        return None
    try:
        resolved = ensure_within_root(settings.data_root, path)
    except ApiError:
        return None
    return resolved if resolved.is_file() else None
