"""Locked backend packages and third-party behavior that the dependency refresh must keep."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from app.main import create_app

BACKEND = Path(__file__).resolve().parents[1]
REPOSITORY = BACKEND.parent
RUNTIME_LOCK = BACKEND / "requirements.lock"
DEV_LOCK = BACKEND / "requirements-dev.lock"
CI_WORKFLOW = REPOSITORY / ".github" / "workflows" / "ci.yml"
CI_JOBS = {"frontend", "desktop", "runner", "backend", "contracts", "containers", "fullstack"}
REQUIREMENT_LINE = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)==(?P<version>[^\s;\\]+)")
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")


@dataclass
class LockedPackage:
    """One `name==version` entry of a lock file with its environment marker and hashes."""

    name: str
    version: str
    marker: str | None
    hashes: list[str] = field(default_factory=list)


def _read_lock(path: Path) -> dict[str, LockedPackage]:
    """Return the entries of a `uv pip compile --generate-hashes` file by canonical name.

    Fails the calling test when a requirement line is not an exact `==` pin, because the image
    must never resolve a version on its own.
    """
    packages: dict[str, LockedPackage] = {}
    current: LockedPackage | None = None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not line.startswith(" "):
            match = REQUIREMENT_LINE.match(stripped)
            assert match, f"{path.name}: not an exact pin: {stripped}"
            requirement = Requirement(stripped.rstrip("\\ ").strip())
            current = LockedPackage(
                name=canonicalize_name(match["name"]),
                version=match["version"],
                marker=str(requirement.marker) if requirement.marker else None,
            )
            packages[current.name] = current
        elif current is not None:
            current.hashes.extend(HASH.findall(stripped))
    return packages


def _pins(requirements: list[str]) -> dict[str, str]:
    """Map each `name[extras]==version` requirement of `pyproject.toml` to its version."""
    pins: dict[str, str] = {}
    for text in requirements:
        requirement = Requirement(text)
        specifiers = list(requirement.specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "==", text
        pins[canonicalize_name(requirement.name)] = specifiers[0].version
    return pins


def _pyproject() -> dict[str, object]:
    """Return the parsed backend `pyproject.toml`."""
    with (BACKEND / "pyproject.toml").open("rb") as source:
        return tomllib.load(source)


def _ci_jobs() -> dict[str, Any]:
    """Return the jobs of the parsed CI workflow; raises `yaml.YAMLError` on invalid YAML.

    PyYAML is locked as a runtime dependency of faster-whisper and Uvicorn, so it cannot be a
    development pin as well; `test_installed_packages_are_the_locked_versions` covers its version.
    """
    jobs: dict[str, Any] = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]
    return jobs


def test_every_locked_package_is_pinned_with_hashes() -> None:
    """Pin every runtime and development package exactly, each with at least one SHA-256 hash."""
    for lock in (RUNTIME_LOCK, DEV_LOCK):
        packages = _read_lock(lock)
        assert packages, lock.name
        unhashed = [package.name for package in packages.values() if not package.hashes]
        assert unhashed == [], f"{lock.name}: packages without a hash: {unhashed}"


def test_locks_contain_the_pyproject_pins_and_agree_with_each_other() -> None:
    """Keep `pyproject.toml`, the runtime lock and the development lock on the same versions."""
    project = _pyproject()
    runtime = _read_lock(RUNTIME_LOCK)
    development = _read_lock(DEV_LOCK)
    metadata = project["project"]
    assert isinstance(metadata, dict)
    groups = project["dependency-groups"]
    build_system = project["build-system"]
    assert isinstance(groups, dict) and isinstance(build_system, dict)
    runtime_pins = _pins(metadata["dependencies"]) | _pins(groups["build"])
    # The build backend must be the locked one, because the image builds without isolation.
    assert _pins(build_system["requires"]).items() <= _pins(groups["build"]).items()
    development_pins = _pins(metadata["optional-dependencies"]["dev"])

    for name, version in runtime_pins.items():
        assert name in runtime, f"{name} is missing from requirements.lock"
        assert runtime[name].version == version, name
    for name, version in development_pins.items():
        assert name in development, f"{name} is missing from requirements-dev.lock"
        assert development[name].version == version, name
    for name, package in runtime.items():
        assert name in development, f"{name} is missing from requirements-dev.lock"
        assert (development[name].version, development[name].marker) == (
            package.version,
            package.marker,
        ), name
    assert not set(development_pins) & set(runtime), "development tools leaked into the image"


def test_python_version_agrees_across_project_image_ci_and_locks() -> None:
    """Use one Python minor version for the package metadata, tools, image, CI and lock files."""
    project = _pyproject()
    metadata = project["project"]
    tools = project["tool"]
    assert isinstance(metadata, dict) and isinstance(tools, dict)
    assert metadata["requires-python"] == ">=3.14,<3.15"
    assert tools["ruff"]["target-version"] == "py314"
    assert tools["mypy"]["python_version"] == "3.14"
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    image = re.search(
        r"^FROM docker\.io/library/python:(\d+\.\d+)\.(\d+)-slim-trixie@sha256:[0-9a-f]{64}"
        r" AS runtime$",
        dockerfile,
        re.M,
    )
    assert image and image[1] == "3.14", "the runtime image must use Python 3.14"
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert set(re.findall(r"python-version: (\S+)", workflow)) == {f"3.14.{image[2]}"}
    for lock in (RUNTIME_LOCK, DEV_LOCK):
        assert "--python-version 3.14 " in lock.read_text(encoding="utf-8"), lock.name


def test_python_version_file_names_the_version_of_the_image_and_ci() -> None:
    """Give version managers the Python of the image and CI; `.python-version` once fell behind."""
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    image = re.search(
        r"^FROM docker\.io/library/python:(\d+\.\d+\.\d+)-slim-trixie@", dockerfile, re.M
    )
    assert image
    assert (REPOSITORY / ".python-version").read_text(encoding="utf-8").strip() == image[1]
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert set(re.findall(r"python-version: (\S+)", workflow)) == {image[1]}


def test_image_and_ci_install_only_from_the_locks() -> None:
    """Install every package from a lock with hashes and the project without resolving anything."""
    dockerfile = (BACKEND / "Dockerfile").read_text(encoding="utf-8")
    installs = re.findall(r"pip install ([^\\\n]+)", dockerfile)
    assert installs == [
        "--require-hashes --no-deps --only-binary=:all: -r requirements.lock ",
        "--no-deps --no-build-isolation --no-index . ",
    ]
    assert "COPY pyproject.toml requirements.lock ./" in dockerfile
    assert "python -m pip check" in dockerfile
    ci_commands = [
        step["run"] for job in _ci_jobs().values() for step in job["steps"] if "run" in step
    ]
    ci_installs = [
        install for command in ci_commands for install in re.findall(r"pip install (.+)", command)
    ]
    assert set(ci_installs) == {
        "--require-hashes --no-deps --only-binary=:all: -r backend/requirements-dev.lock",
        "--no-deps --no-build-isolation --no-index -e ./backend",
    }


def test_ci_workflow_parses_and_keeps_every_job() -> None:
    """Keep the CI file valid YAML, because GitHub runs no job at all of an invalid workflow."""
    assert set(_ci_jobs()) == CI_JOBS


def test_ci_jobs_use_one_release_tag_of_each_action() -> None:
    """Keep each Action on one release tag in every job, so that an update cannot miss a job."""
    tags: dict[str, set[str]] = {}
    for job in _ci_jobs().values():
        for step in job["steps"]:
            if "uses" in step:
                action, tag = step["uses"].split("@")
                tags.setdefault(action, set()).add(tag)
    assert set(tags) == {
        "actions/checkout",
        "actions/setup-node",
        "actions/setup-python",
        "actions/upload-artifact",
    }
    for action, used in tags.items():
        assert len(used) == 1 and re.fullmatch(r"v\d+", next(iter(used))), (action, used)


def test_ci_job_environments_do_not_use_the_runner_context() -> None:
    """Keep `runner.*` out of a job's `env`: GitHub refuses the whole workflow file for it."""
    misplaced = [name for name, job in _ci_jobs().items() if "runner." in str(job.get("env", {}))]
    assert misplaced == []


def test_yaml_parser_rejects_an_unquoted_colon_in_a_command() -> None:
    """Refuse the mistake that disabled CI: `:all: ` in a plain scalar starts a nested mapping."""
    with pytest.raises(yaml.YAMLError, match="mapping values are not allowed here"):
        yaml.safe_load("- run: python -m pip install --only-binary=:all: -r requirements.lock\n")


def test_installed_packages_are_the_locked_versions() -> None:
    """Run the suite on the development lock, so a passing run vouches for the locked set.

    A failure means the environment was not installed from `requirements-dev.lock`; reinstall it
    as `docs/DEVELOPMENT.md` describes.
    """
    mismatched: list[str] = []
    for package in _read_lock(DEV_LOCK).values():
        if package.marker and not Marker(package.marker).evaluate():
            continue
        try:
            installed = importlib.metadata.version(package.name)
        except importlib.metadata.PackageNotFoundError:
            installed = "missing"
        if installed != package.version:
            mismatched.append(f"{package.name} {installed} (lock {package.version})")
    assert mismatched == []


def test_fastapi_telemetry_stays_off_even_when_the_environment_asks_for_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Never instrument or export requests, whatever `OTEL_*` variables say (CLAUDE.md rule 11).

    FastAPI 0.142 and later depend on `opentelemetry-api` and wrap every request in its
    instrumentation as soon as a global provider is configured, which this test simulates. No
    OpenTelemetry SDK or exporter may be installed either.
    """
    from fastapi.telemetry import _asgi

    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9/otlp")
    monkeypatch.setattr(_asgi, "_unconfigured", lambda provider: False)
    instrumented: list[str] = []

    async def record(
        self: object, *, app: Any, scope: Any, receive: Any, send: Any, **_: Any
    ) -> None:
        """Note that FastAPI routed a request through its instrumentation, then serve it."""
        instrumented.append(scope["path"])
        await app(scope, receive, send)

    monkeypatch.setattr(_asgi.NativeTelemetry, "__call__", record)
    application = create_app()

    with TestClient(application) as client:
        assert client.get("/api/v1/health").status_code in {200, 503}

    assert instrumented == []
    config = application._telemetry  # FastAPI keeps the resolved settings here.
    assert (config["tracing"], config["metrics"], config["logs"]) == (False, False, False)
    assert config["auto_configure"] is False
    for module in ("opentelemetry.sdk", "opentelemetry.exporter"):
        assert importlib.util.find_spec(module) is None, module


def test_no_locked_package_is_a_pre_release() -> None:
    """Adopt stable releases only; `uv` falls back to a pre-release when a pin needs one."""
    pre_releases = [
        f"{package.name} {package.version}"
        for package in _read_lock(DEV_LOCK).values()
        if Version(package.version).is_prerelease
    ]
    assert pre_releases == []


def test_license_inventory_lists_every_locked_version() -> None:
    """Keep the package table of `docs/LICENSES.md` on the locked versions (CLAUDE.md rule 17)."""
    document = (REPOSITORY / "docs" / "LICENSES.md").read_text(encoding="utf-8")
    start = document.index("## Backend Python packages")
    inventory = document[start : document.index("### Kept behind", start)]
    listed = {
        canonicalize_name(name): version
        for name, version in re.findall(r"^\| `([A-Za-z0-9._-]+)` \| ([^ |]+) \|", inventory, re.M)
    }
    locked = {package.name: package.version for package in _read_lock(DEV_LOCK).values()}
    assert listed == locked
