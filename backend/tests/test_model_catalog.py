"""Verify the pinned model catalog and the local generation settings (P06.2a)."""

from __future__ import annotations

import re
import typing
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.services.model_catalog import (
    DEFAULT_MODEL_ID,
    GEMMA_4_E2B,
    GEMMA_4_E4B,
    MODEL_CATALOG,
    get_model_option,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SETTING_KEYS = {
    "NARRASTEP_LOCAL_GENERATION": "available",
    "NARRASTEP_GENERATION_MODEL": "gemma-4-e2b",
    "NARRASTEP_GENERATION_THREADS": "0",
    "NARRASTEP_GENERATION_IMAGE_MAX_EDGE": "1024",
}


def test_catalog_pins_revision_names_sizes_and_hashes() -> None:
    """Pin both options to a 40-hex revision and 64-hex hashes, with the files of the document."""
    assert set(MODEL_CATALOG) == {"gemma-4-e2b", "gemma-4-e4b"}
    assert DEFAULT_MODEL_ID == "gemma-4-e2b"
    for option, weights, projector in (
        (GEMMA_4_E2B, "gemma-4-E2B-it-Q4_0.gguf", "mmproj-gemma-4-E2B-it-Q8_0.gguf"),
        (GEMMA_4_E4B, "gemma-4-E4B-it-Q4_0.gguf", "mmproj-gemma-4-E4B-it-Q8_0.gguf"),
    ):
        assert re.fullmatch(r"[0-9a-f]{40}", option.revision)
        assert [item.name for item in option.files] == [weights, projector]
        for item in option.files:
            assert re.fullmatch(r"[0-9a-f]{64}", item.sha256)
            assert item.size_bytes > 0
            assert "/" not in item.name and "\\" not in item.name
    assert GEMMA_4_E2B.revision != GEMMA_4_E4B.revision
    all_hashes = [item.sha256 for option in MODEL_CATALOG.values() for item in option.files]
    assert len(set(all_hashes)) == len(all_hashes)


def test_catalog_sizes_match_the_documented_downloads() -> None:
    """Keep the totals at the 3.4 GB and 5.2 GB that docs/LOCAL_GENERATION.md announces."""
    assert GEMMA_4_E2B.total_bytes == 2_841_481_184 + 557_368_064
    assert GEMMA_4_E4B.total_bytes == 4_590_807_392 + 559_874_816
    assert round(GEMMA_4_E2B.total_bytes / 1e9, 1) == 3.4
    assert round(GEMMA_4_E4B.total_bytes / 1e9, 1) == 5.2


def test_catalog_file_urls_are_pinned_to_the_revision() -> None:
    """Build download addresses from the revision, never from a moving branch name."""
    url = GEMMA_4_E2B.file_url(GEMMA_4_E2B.files[0])
    assert url == (
        "https://huggingface.co/ggml-org/gemma-4-E2B-it-GGUF/resolve/"
        "6938c58e698502037401a5f19f6905f2fb2f7dee/gemma-4-E2B-it-Q4_0.gguf"
    )
    assert "/main/" not in url
    with pytest.raises(KeyError):
        get_model_option("gemma-4-e9b")


def test_configured_model_names_are_exactly_the_catalog_keys() -> None:
    """Keep the `generation_model` literal and the catalog in step."""
    annotation = Settings.model_fields["generation_model"].annotation
    assert set(typing.get_args(annotation)) == set(MODEL_CATALOG)
    assert Settings.model_fields["generation_model"].default == DEFAULT_MODEL_ID


def test_generation_settings_default_to_available_light_model(tmp_path: Path) -> None:
    """Default to the E2B model with automatic threads and 1024-pixel screenshots."""
    settings = Settings(data_root=tmp_path)
    assert settings.local_generation == "available"
    assert settings.local_generation_available is True
    assert settings.generation_model == "gemma-4-e2b"
    assert settings.generation_threads == 0
    assert settings.generation_image_max_edge == 1024


def test_generation_settings_read_the_environment_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Accept the four documented NARRASTEP_* variables and apply the kill switch."""
    monkeypatch.setenv("NARRASTEP_LOCAL_GENERATION", "disabled")
    monkeypatch.setenv("NARRASTEP_GENERATION_MODEL", "gemma-4-e4b")
    monkeypatch.setenv("NARRASTEP_GENERATION_THREADS", "6")
    monkeypatch.setenv("NARRASTEP_GENERATION_IMAGE_MAX_EDGE", "768")
    settings = Settings(data_root=tmp_path)
    assert settings.local_generation_available is False
    assert settings.generation_model == "gemma-4-e4b"
    assert settings.generation_threads == 6
    assert settings.generation_image_max_edge == 768


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("local_generation", "enabled"),
        ("local_generation", ""),
        ("generation_model", "gemma-4-e9b"),
        ("generation_model", "GEMMA-4-E2B"),
        ("generation_threads", -1),
        ("generation_threads", 257),
        ("generation_threads", "many"),
        ("generation_image_max_edge", 0),
        ("generation_image_max_edge", 255),
        ("generation_image_max_edge", 4097),
    ],
)
def test_generation_settings_reject_invalid_values(tmp_path: Path, key: str, value: object) -> None:
    """Refuse values outside the documented choices and ranges."""
    with pytest.raises(ValidationError):
        Settings(data_root=tmp_path, **{key: value})


def test_generation_settings_accept_the_range_limits(tmp_path: Path) -> None:
    """Accept the lowest and highest documented values."""
    assert Settings(data_root=tmp_path, generation_threads=256).generation_threads == 256
    low = Settings(data_root=tmp_path, generation_image_max_edge=256)
    assert low.generation_image_max_edge == 256
    high = Settings(data_root=tmp_path, generation_image_max_edge=4096)
    assert high.generation_image_max_edge == 4096


def test_compose_readme_and_documents_list_the_settings_with_the_same_defaults() -> None:
    """Pass each key to the services with the default of the settings and document it."""
    compose = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    specification = (REPOSITORY_ROOT / "docs" / "LOCAL_GENERATION.md").read_text(encoding="utf-8")
    for key, default in SETTING_KEYS.items():
        assert f"  {key}: ${{{key}:-{default}}}" in compose
        assert f"`{key}`" in readme
        assert f"`{key}` | `{default}`" in specification
