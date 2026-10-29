"""Pinned container images of the Compose stack and the license inventory that records them."""

from __future__ import annotations

import re
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
DOCKERFILES = (REPOSITORY / "backend" / "Dockerfile", REPOSITORY / "apps" / "web" / "Dockerfile")
COMPOSE = REPOSITORY / "docker-compose.yml"
LICENSES = REPOSITORY / "docs" / "LICENSES.md"
# `name:tag@sha256:digest`, where the tag must carry an exact three-part version (`9.1.3-alpine`,
# `server-v0.5.0`); floating tags such as `alpine` or `stable` are rejected.
PINNED = re.compile(
    r"^(?P<name>[a-z0-9./-]+):(?P<tag>[A-Za-z0-9._-]*\d+\.\d+\.\d+[A-Za-z0-9._-]*)"
    r"@(?P<digest>sha256:[0-9a-f]{64})$"
)
# Images built from this repository; Compose names them and never pulls them.
LOCAL_IMAGE = re.compile(r"^narrastep-[a-z]+:")


def _external_images() -> list[str]:
    """Return every image reference the Dockerfiles build from and Compose pulls, in file order."""

    references = []
    for dockerfile in DOCKERFILES:
        text = dockerfile.read_text(encoding="utf-8")
        references += re.findall(r"^FROM (\S+)", text, re.M)
    compose = COMPOSE.read_text(encoding="utf-8")
    references += [
        image
        for image in re.findall(r"^\s+image: (\S+)$", compose, re.M)
        if not LOCAL_IMAGE.match(image)
    ]
    return references


def _documented_images() -> set[tuple[str, str, str]]:
    """Return the (image, tag, digest) rows of the "Container images" table in `docs/LICENSES.md`.

    Fails the calling test when the section or its rows are missing.
    """

    text = LICENSES.read_text(encoding="utf-8")
    section = text.split("\n## Container images\n", 1)
    assert len(section) == 2, "docs/LICENSES.md has no 'Container images' section"
    body = section[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| `([^`]+)` \| `([^`]+)` \| `(sha256:[0-9a-f]{64})` \|", body, re.M)
    assert rows, "the 'Container images' table has no rows"
    return set(rows)


def test_every_external_image_has_an_exact_tag_and_digest() -> None:
    """Pull the same bytes on every build: an exact version tag and the index digest (P08.3)."""

    references = _external_images()
    assert len(references) == 5, references
    unpinned = [reference for reference in references if not PINNED.match(reference)]
    assert unpinned == []


def test_license_inventory_lists_exactly_the_pinned_images() -> None:
    """Keep the image table in `docs/LICENSES.md` equal to the files, digest included."""

    pinned = set()
    for reference in _external_images():
        match = PINNED.match(reference)
        assert match, reference
        pinned.add((match["name"], match["tag"], match["digest"]))
    assert _documented_images() == pinned


def test_broker_is_valkey_with_its_own_commands() -> None:
    """Run the BSD-licensed Valkey server, not Redis 7.4 or later (ADR 0017, risk K9)."""

    compose = COMPOSE.read_text(encoding="utf-8")
    service = compose.split("\n  redis:\n", 1)[1].split("\n\n", 1)[0]
    assert re.search(r"^    image: valkey/valkey:", service, re.M)
    assert '    command: ["valkey-server", "--appendonly", "yes"]' in service
    assert 'test: ["CMD", "valkey-cli", "ping"]' in service
    images = re.findall(r"^\s+image: (\S+)$", compose, re.M)
    assert not [image for image in images if re.match(r"^(docker\.io/)?(library/)?redis:", image)]


def test_web_build_uses_the_node_version_of_nvmrc() -> None:
    """Build the web image with the Node.js version contributors and CI use (`engine-strict`)."""

    dockerfile = (REPOSITORY / "apps" / "web" / "Dockerfile").read_text(encoding="utf-8")
    image = re.search(r"^FROM node:(\d+\.\d+\.\d+)-alpine@", dockerfile, re.M)
    assert image
    for nvmrc in (REPOSITORY / ".nvmrc", REPOSITORY / "apps" / "web" / ".nvmrc"):
        assert nvmrc.read_text(encoding="utf-8").strip() == image[1], nvmrc
