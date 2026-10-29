"""Validated runtime configuration for native and container execution."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def default_data_root() -> Path:
    """Return a platform-appropriate user data directory for native execution."""
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return Path(local_app_data) / "Narrastep" / "data"
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        return Path(xdg_data_home) / "narrastep"
    return Path.home() / ".local" / "share" / "narrastep"


def default_whisper_model_path() -> Path:
    """Return a native local model path outside synchronized project workspaces."""
    data_root = default_data_root()
    application_root = data_root.parent if data_root.name.lower() == "data" else data_root
    return application_root / "models" / "small"


class Settings(BaseSettings):
    """Hold and validate all behavior-affecting Narrastep configuration."""

    model_config = SettingsConfigDict(
        env_prefix="NARRASTEP_",
        env_file=".env",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    app_name: str = "Narrastep API"
    environment: Literal["development", "test", "production"] = Field(
        default="development",
        validation_alias=AliasChoices("NARRASTEP_ENV", "NARRASTEP_ENVIRONMENT"),
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    data_root: Path = Field(
        default_factory=default_data_root,
        validation_alias=AliasChoices("NARRASTEP_DATA_DIR", "NARRASTEP_DATA_ROOT"),
    )
    database_url: str | None = None
    api_host: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("NARRASTEP_HOST", "NARRASTEP_API_HOST"),
    )
    api_port: int = Field(default=8000, ge=1, le=65535)
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_always_eager: bool = False
    max_upload_mb: int = Field(
        default=4096,
        ge=1,
        validation_alias=AliasChoices("NARRASTEP_MAX_UPLOAD_SIZE_MB", "NARRASTEP_MAX_UPLOAD_MB"),
    )
    disk_reserve_mb: int = Field(
        default=1024,
        ge=0,
        validation_alias=AliasChoices("NARRASTEP_MIN_FREE_DISK_MB", "NARRASTEP_DISK_RESERVE_MB"),
    )
    allowed_video_extensions: Annotated[list[str], NoDecode] = Field(
        default=[".mp4", ".webm", ".mov", ".mkv"],
        validation_alias="NARRASTEP_ALLOWED_VIDEO_EXTENSIONS",
    )
    upload_chunk_bytes: int = Field(default=1024 * 1024, ge=64 * 1024)
    # One recording chunk covers a few seconds (about 1.6 MB at 2.5 Mbit/s and 5 s); the limit only
    # stops a runaway request. The whole session is bounded by `max_upload_mb`.
    recording_max_chunk_mb: int = Field(default=64, ge=1)
    # A session still recording with no chunk for this long is reported as interrupted.
    recording_interrupted_after_seconds: int = Field(default=30, ge=5)
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ffmpeg_timeout_seconds: int = Field(
        default=600,
        ge=1,
        validation_alias=AliasChoices(
            "NARRASTEP_MEDIA_COMMAND_TIMEOUT_SECONDS", "NARRASTEP_FFMPEG_TIMEOUT_SECONDS"
        ),
    )
    ffprobe_timeout_seconds: int = Field(
        default=30,
        ge=1,
        validation_alias=AliasChoices(
            "NARRASTEP_MEDIA_COMMAND_TIMEOUT_SECONDS", "NARRASTEP_FFPROBE_TIMEOUT_SECONDS"
        ),
    )
    whisper_model_path: Path = Field(default_factory=default_whisper_model_path)
    whisper_model: Literal[
        "tiny", "base", "small", "medium", "large-v1", "large-v2", "large-v3"
    ] = "small"
    whisper_device: Literal["cpu"] = "cpu"
    whisper_compute_type: Literal["int8"] = "int8"
    whisper_local_files_only: bool = True
    frame_interval_seconds: float = Field(default=3.0, gt=0)
    frame_neighbor_seconds: float = Field(
        default=0.2,
        ge=0,
        validation_alias=AliasChoices(
            "NARRASTEP_STABILITY_SAMPLE_OFFSET_SECONDS", "NARRASTEP_FRAME_NEIGHBOR_SECONDS"
        ),
    )
    duplicate_similarity_threshold: float = Field(
        default=0.92,
        ge=0,
        le=1,
        validation_alias=AliasChoices(
            "NARRASTEP_FRAME_DUPLICATE_THRESHOLD",
            "NARRASTEP_DUPLICATE_SIMILARITY_THRESHOLD",
        ),
    )
    screenshot_alternatives: int = Field(
        default=2,
        ge=0,
        le=20,
        validation_alias=AliasChoices(
            "NARRASTEP_SCREENSHOT_ALTERNATIVES_PER_SEGMENT",
            "NARRASTEP_SCREENSHOT_ALTERNATIVES",
        ),
    )
    screenshot_selection_strategy: Literal["legacy", "adaptive_v1"] = "legacy"
    ocr_mode: Literal["off", "selective"] = "selective"
    image_budget_profile: Literal["compact", "balanced", "detailed"] = "balanced"
    tesseract_path: str = "tesseract"
    adaptive_direct_duplicate_threshold: float = Field(default=0.94, ge=0, le=1)
    adaptive_borderline_duplicate_threshold: float = Field(default=0.84, ge=0, le=1)
    adaptive_borderline_ssim_threshold: float = Field(default=0.96, ge=0, le=1)
    adaptive_ocr_token_jaccard_threshold: float = Field(default=0.85, ge=0, le=1)
    chatgpt_max_files_per_batch: int = Field(default=20, ge=2)
    sse_heartbeat_seconds: int = Field(default=15, ge=1)
    sse_poll_seconds: float = Field(default=0.5, gt=0)
    orphaned_job_seconds: int = Field(
        default=300,
        ge=10,
        validation_alias=AliasChoices(
            "NARRASTEP_JOB_STALE_AFTER_SECONDS", "NARRASTEP_ORPHANED_JOB_SECONDS"
        ),
    )
    job_visibility_timeout_seconds: int = Field(
        default=86400,
        ge=60,
        validation_alias="NARRASTEP_JOB_VISIBILITY_TIMEOUT_SECONDS",
    )
    mock_processing: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "NARRASTEP_ENABLE_MOCK_PROCESSING", "NARRASTEP_MOCK_PROCESSING"
        ),
    )
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://127.0.0.1:4200", "http://localhost:4200"],
        validation_alias=AliasChoices("NARRASTEP_CORS_ORIGINS", "NARRASTEP_ALLOWED_ORIGINS"),
    )

    @field_validator("data_root", "whisper_model_path", mode="before")
    @classmethod
    def expand_configured_path(cls, value: object) -> object:
        """Expand environment variables and home markers in configured paths."""
        if isinstance(value, str):
            return Path(os.path.expandvars(value)).expanduser()
        return value

    @field_validator("allowed_video_extensions", "allowed_origins", mode="before")
    @classmethod
    def parse_comma_separated_list(cls, value: object) -> object:
        """Accept readable comma-separated lists from Compose environment variables."""
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("allowed_video_extensions")
    @classmethod
    def normalize_video_extensions(cls, value: list[str]) -> list[str]:
        """Normalize configured video suffixes to lowercase dot-prefixed values."""
        normalized = [
            item.lower() if item.startswith(".") else f".{item.lower()}" for item in value
        ]
        supported = {".mp4", ".webm", ".mov", ".mkv"}
        if not normalized or any(item not in supported for item in normalized):
            raise ValueError("Allowed video extensions must be a nonempty supported subset.")
        return list(dict.fromkeys(normalized))

    @field_validator("redis_url", "celery_broker_url", "celery_result_backend")
    @classmethod
    def validate_redis_url(cls, value: str | None) -> str | None:
        """Require local Redis-compatible URLs for broker, results, and transient signals."""
        if value is None:
            return None
        parsed = urlparse(value)
        if parsed.scheme not in {"redis", "rediss"} or not parsed.hostname:
            raise ValueError("Redis service URLs must use redis:// or rediss:// with a host.")
        return value

    @field_validator("whisper_local_files_only")
    @classmethod
    def require_offline_only_model_loading(cls, value: bool) -> bool:
        """Reject any attempt to disable the offline-only model loading invariant."""
        if not value:
            raise ValueError("Offline-only model loading is required and cannot be disabled.")
        return value

    @model_validator(mode="after")
    def derive_service_urls(self) -> Settings:
        """Derive local database and Celery URLs when they were not supplied."""
        resolved_root = self.data_root.resolve()
        self.data_root = resolved_root
        if "whisper_model_path" not in self.model_fields_set:
            self.whisper_model_path = self.whisper_model_path.parent / self.whisper_model
        if not self.database_url:
            self.database_url = f"sqlite:///{(resolved_root / 'narrastep.db').as_posix()}"
        if not self.database_url.startswith(("sqlite:///", "sqlite+pysqlite:///")):
            raise ValueError("Narrastep 0.2 supports only a local SQLite database.")
        if not self.celery_broker_url:
            self.celery_broker_url = self.redis_url
        if not self.celery_result_backend:
            self.celery_result_backend = self.redis_url
        if self.adaptive_borderline_duplicate_threshold > self.adaptive_direct_duplicate_threshold:
            raise ValueError(
                "The adaptive borderline duplicate threshold cannot exceed the direct threshold."
            )
        if self.environment != "test" and self.mock_processing:
            raise ValueError("Mock processing can only be enabled in the test environment.")
        return self

    @property
    def max_upload_bytes(self) -> int:
        """Return the configured upload limit in bytes."""
        return self.max_upload_mb * 1024 * 1024

    @property
    def recording_max_chunk_bytes(self) -> int:
        """Return the largest accepted recording chunk in bytes."""
        return self.recording_max_chunk_mb * 1024 * 1024

    @property
    def disk_reserve_bytes(self) -> int:
        """Return the reserved free disk capacity in bytes."""
        return self.disk_reserve_mb * 1024 * 1024

    @property
    def resolved_database_url(self) -> str:
        """Return the database URL after the settings derivation validator has run."""
        if self.database_url is None:
            raise RuntimeError("The database URL was not initialized.")
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Create one validated settings object per application process."""
    return Settings()
