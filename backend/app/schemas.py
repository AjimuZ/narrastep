"""Camel-case HTTP schemas that isolate public contracts from database naming."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models import ExportStatus, JobStatus, OutputMode, ProjectState

SUPPORTED_WHISPER_LANGUAGES = frozenset(
    {
        "af",
        "am",
        "ar",
        "as",
        "az",
        "ba",
        "be",
        "bg",
        "bn",
        "bo",
        "br",
        "bs",
        "ca",
        "cs",
        "cy",
        "da",
        "de",
        "el",
        "en",
        "es",
        "et",
        "eu",
        "fa",
        "fi",
        "fo",
        "fr",
        "gl",
        "gu",
        "ha",
        "haw",
        "he",
        "hi",
        "hr",
        "ht",
        "hu",
        "hy",
        "id",
        "is",
        "it",
        "ja",
        "jw",
        "ka",
        "kk",
        "km",
        "kn",
        "ko",
        "la",
        "lb",
        "ln",
        "lo",
        "lt",
        "lv",
        "mg",
        "mi",
        "mk",
        "ml",
        "mn",
        "mr",
        "ms",
        "mt",
        "my",
        "ne",
        "nl",
        "nn",
        "no",
        "oc",
        "pa",
        "pl",
        "ps",
        "pt",
        "ro",
        "ru",
        "sa",
        "sd",
        "si",
        "sk",
        "sl",
        "sn",
        "so",
        "sq",
        "sr",
        "su",
        "sv",
        "sw",
        "ta",
        "te",
        "tg",
        "th",
        "tk",
        "tl",
        "tr",
        "tt",
        "uk",
        "ur",
        "uz",
        "vi",
        "yi",
        "yo",
        "zh",
    }
)


def to_camel(value: str) -> str:
    """Convert a snake-case Python field name into a camel-case JSON key."""
    first, *remaining = value.split("_")
    return first + "".join(part.capitalize() for part in remaining)


def serialize_utc(value: datetime) -> str:
    """Serialize SQLite timestamps as explicit UTC ISO-8601 values."""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


class ApiModel(BaseModel):
    """Apply the shared aliases and ORM loading behavior to API schemas."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
        use_enum_values=True,
    )

    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_datetime_fields(self, value: Any) -> Any:
        """Attach explicit UTC information to datetime fields loaded from SQLite."""
        return serialize_utc(value) if isinstance(value, datetime) else value


class ErrorDetail(ApiModel):
    """Describe one stable and non-sensitive API error."""

    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorEnvelope(ApiModel):
    """Wrap API failures in one predictable top-level property."""

    error: ErrorDetail


class HealthResponse(ApiModel):
    """Report process health without leaking configuration or filesystem paths."""

    status: str
    version: str
    database: str
    redis: str
    worker: str


class ProjectCreate(ApiModel):
    """Validate metadata required to create a project workspace."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    source_language: str = Field(default="auto", max_length=16)
    mode: OutputMode = OutputMode.TUTORIAL

    @field_validator("name")
    @classmethod
    def strip_required_name(cls, value: str) -> str:
        """Reject names that contain only whitespace while retaining user wording."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Project name must not be empty.")
        return normalized

    @field_validator("source_language")
    @classmethod
    def validate_source_language(cls, value: str) -> str:
        """Accept automatic detection or a lowercase ISO-639-1 language code."""
        normalized = value.strip().lower()
        if normalized != "auto" and normalized not in SUPPORTED_WHISPER_LANGUAGES:
            raise ValueError("Source language must be 'auto' or a Whisper-supported language code.")
        return normalized


class ProjectUpdate(ApiModel):
    """Validate editable project metadata without changing evidence or state."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    source_language: str | None = Field(default=None, max_length=16)
    mode: OutputMode | None = None

    @field_validator("name", "source_language", "mode", mode="before")
    @classmethod
    def reject_required_metadata_null(cls, value: object) -> object:
        """Reject explicit null while allowing each non-null metadata field to be omitted."""
        if value is None:
            raise ValueError("This project metadata field cannot be null.")
        return value

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        """Normalize an optional project name and reject whitespace-only updates."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Project name must not be empty.")
        return normalized

    @field_validator("source_language")
    @classmethod
    def validate_optional_source_language(cls, value: str | None) -> str | None:
        """Validate an optional source-language update against the public contract."""
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized != "auto" and normalized not in SUPPORTED_WHISPER_LANGUAGES:
            raise ValueError("Source language must be 'auto' or a Whisper-supported language code.")
        return normalized


class MediaAssetResponse(ApiModel):
    """Expose safe source-video metadata without exposing its local absolute path."""

    id: str
    project_id: str
    asset_type: str = "video"
    original_filename: str
    mime_type: str
    size_bytes: int
    duration_seconds: float
    width: int
    height: int
    created_at: datetime


class ProjectResponse(ApiModel):
    """Expose project metadata, state, and optional validated source information."""

    id: str
    name: str
    description: str | None
    source_language: str
    mode: OutputMode
    detected_language: str | None
    status: ProjectState
    created_at: datetime
    updated_at: datetime
    video_asset_id: str | None
    active_job_id: str | None
    video_asset: MediaAssetResponse | None = None
    active_segment_count: int = 0
    screenshot_count: int = 0


class ProjectListResponse(ApiModel):
    """Return a stable pageless collection shape for the local project list."""

    items: list[ProjectResponse]
    total: int


class WordResponse(ApiModel):
    """Expose immutable word-level evidence used by split operations."""

    text: str
    start_seconds: float
    end_seconds: float
    probability: float | None = None


class ScreenshotResponse(ApiModel):
    """Expose one generated screenshot without revealing its local path."""

    id: str
    project_id: str
    timestamp_seconds: float
    width: int
    height: int
    quality_score: float
    is_candidate: bool = True
    role: str | None = None
    confidence: float | None = None
    is_user_selected: bool = False
    selection_reason: str = "legacy"
    visual_change_score: float | None = None
    text_change_score: float | None = None
    cluster_id: str | None = None
    is_mandatory: bool = False
    materialization_state: str = "png_ready"
    shared_segment_count: int = 0
    created_at: datetime


class ScreenshotAssociationResponse(ApiModel):
    """Expose one segment-to-screenshot role with embedded screenshot metadata."""

    id: str
    segment_id: str
    screenshot_id: str
    role: str
    confidence: float | None
    coverage_score: float | None = None
    role_rank_reason: str | None = None
    is_user_selected: bool
    screenshot: ScreenshotResponse
    created_at: datetime


class SegmentResponse(ApiModel):
    """Expose one active reviewed segment and its available visual evidence."""

    id: str
    project_id: str
    segment_index: int
    start_seconds: float
    end_seconds: float
    raw_text: str
    reviewed_text: str
    words: list[WordResponse]
    speaker_label: str | None
    confidence: float | None
    created_at: datetime
    updated_at: datetime


class SegmentListResponse(ApiModel):
    """Return active reviewed segments in deterministic timeline order."""

    items: list[SegmentResponse]
    total: int


class SegmentUpdate(ApiModel):
    """Validate an explicit reviewed-text save, including an intentional empty value."""

    reviewed_text: str = Field(max_length=100_000)


class SegmentSplitRequest(ApiModel):
    """Carry the exact reviewed text portions and chosen timeline split point."""

    left_reviewed_text: str = Field(max_length=100_000)
    right_reviewed_text: str = Field(max_length=100_000)
    split_seconds: float = Field(ge=0)


class SegmentMergeRequest(ApiModel):
    """Identify two or more ordered, active, contiguous segments to merge."""

    segment_ids: list[str] = Field(min_length=2)

    @field_validator("segment_ids")
    @classmethod
    def reject_duplicate_segment_ids(cls, value: list[str]) -> list[str]:
        """Reject ambiguous merge requests that repeat the same source segment."""
        if len(value) != len(set(value)):
            raise ValueError("Segment identifiers must be unique.")
        return value


class JobResponse(ApiModel):
    """Expose persisted processing progress for stream and polling clients."""

    id: str
    project_id: str
    status: JobStatus
    current_stage: str
    progress_percent: int
    message: str
    error_code: str | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    version: int
    selection_strategy: str = "legacy"
    ocr_mode: str = "off"
    image_budget_profile: str = "balanced"
    metrics: dict[str, Any] = Field(default_factory=dict)


class ExportCreate(ApiModel):
    """Configure one immutable export without including source video by default."""

    include_original_video: bool = False


class ExportResponse(ApiModel):
    """Expose export progress and a download URL only after successful completion."""

    id: str
    project_id: str
    status: ExportStatus
    progress_percent: int
    current_stage: str | None
    include_original_video: bool
    error_code: str | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None
    size_bytes: int | None
    metrics: dict[str, Any] = Field(default_factory=dict)


class ScreenshotFromVideoRequest(ApiModel):
    """Request one exact source-video frame for the active review segment."""

    timestamp_seconds: float = Field(ge=0)


class ExportEstimateResponse(ApiModel):
    """Expose a local conservative estimate produced by the real export selector."""

    canonical_image_count: int
    batch_count: int
    batch_image_copy_count: int
    estimated_zip_bytes: int
    is_approximate: bool = True
    redundancy_warning: bool


class ExportListResponse(ApiModel):
    """Return immutable export history newest first."""

    items: list[ExportResponse]
    total: int
