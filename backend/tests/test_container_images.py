"""Pinned container images of the Compose stack and the license inventory that records them."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

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
LOCAL_IMAGE = re.compile(r"^localhost/narrastep-[a-z]+:\d+\.\d+\.\d+$")
# The first path part must be a registry host, because Podman without a configured search registry
# asks or fails for a short name (ADR 0016). `docker.io` also needs its `library/` or user part.
QUALIFIED = re.compile(r"^(?:docker\.io/[a-z0-9-]+/[a-z0-9._-]+|ghcr\.io/[a-z0-9./-]+):")


def _compose_services() -> dict[str, Any]:
    """Return the `services` mapping of `docker-compose.yml`; fails when the file is not YAML."""

    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]


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


def test_every_image_name_is_fully_qualified() -> None:
    """Resolve the same image on Docker and Podman, for pulled and locally built images (P08.9)."""

    short = [image for image in _external_images() if not QUALIFIED.match(image)]
    assert short == []
    built = [service["image"] for service in _compose_services().values() if "build" in service]
    assert built
    assert [image for image in built if not LOCAL_IMAGE.match(image)] == []


def test_qualified_name_patterns_reject_short_names() -> None:
    """Show that the patterns above fail on the names P08.9 replaced, so they can catch a return."""

    for short in ("python:3.14.10", "valkey/valkey:9.1.3", "docker.io/python:3.14.10"):
        assert not QUALIFIED.match(short), short
    assert not LOCAL_IMAGE.match("narrastep-backend:0.2.0")
    assert not LOCAL_IMAGE.match("localhost/narrastep-backend:latest")


def test_every_service_others_wait_for_has_a_compose_health_check() -> None:
    """Keep `service_healthy` working on Podman, whose OCI images drop `HEALTHCHECK` (P08.9)."""

    services = _compose_services()
    awaited = {name for service in services.values() for name in service.get("depends_on", {})}
    assert awaited == {"redis", "api"}
    for name in awaited | {"web"}:
        assert services[name]["healthcheck"]["test"][0] == "CMD", name
    for dockerfile in DOCKERFILES:
        assert not re.search(r"^HEALTHCHECK", dockerfile.read_text(encoding="utf-8"), re.M)


def test_the_model_import_folder_is_the_only_bind_mount_read_only_on_api_with_relabel() -> None:
    """Mount the import folder on `api` alone, read-only, with the SELinux relabel (P06.2d)."""

    binds = {
        name: [
            volume
            for volume in service.get("volumes", [])
            if isinstance(volume, str) and volume.split(":")[0].startswith((".", "/", "$"))
        ]
        for name, service in _compose_services().items()
    }
    assert {name: mounts for name, mounts in binds.items() if mounts} == {
        "api": ["${NARRASTEP_MODEL_IMPORT_DIR:-./data/models-import}:/import:ro,z"]
    }
    api = _compose_services()["api"]
    assert api["environment"]["NARRASTEP_MODEL_IMPORT_DIR"] == "/import"


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
    assert re.search(r"^    image: docker\.io/valkey/valkey:", service, re.M)
    assert '    command: ["valkey-server", "--appendonly", "yes"]' in service
    assert 'test: ["CMD", "valkey-cli", "ping"]' in service
    images = re.findall(r"^\s+image: (\S+)$", compose, re.M)
    assert not [image for image in images if re.match(r"^(docker\.io/)?(library/)?redis:", image)]


def test_web_build_uses_the_node_version_of_nvmrc() -> None:
    """Build the web image with the Node.js version contributors and CI use (`engine-strict`).

    `desktop/package.json` is left out on purpose: its `engines` is the oldest host Node.js the
    pinned Electron accepts, a floor and not the version the project itself runs on.
    """

    web = REPOSITORY / "apps" / "web"
    dockerfile = (web / "Dockerfile").read_text(encoding="utf-8")
    image = re.search(r"^FROM docker\.io/library/node:(\d+\.\d+\.\d+)-alpine@", dockerfile, re.M)
    assert image
    for nvmrc in (REPOSITORY / ".nvmrc", web / ".nvmrc"):
        assert nvmrc.read_text(encoding="utf-8").strip() == image[1], nvmrc
    package = json.loads((web / "package.json").read_text(encoding="utf-8"))
    assert package["engines"]["node"] == image[1]
    lock = json.loads((web / "package-lock.json").read_text(encoding="utf-8"))
    assert lock["packages"][""]["engines"]["node"] == image[1]
    workflow = (REPOSITORY / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert set(re.findall(r"node-version: (\S+)", workflow)) == {image[1]}
