"""Camel-case HTTP schemas that isolate public contracts from database naming."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from app.models import (
    ExportStatus,
    JobStatus,
    ModelInstallState,
    OutputMode,
    ProjectState,
    RecordingState,
    RecordingTrack,
    SpeakerLabel,
    SpeakerSource,
)
from app.services.glossary import (
    MAX_GLOSSARY_TERM_LENGTH,
    MAX_GLOSSARY_TERMS,
    normalize_glossary,
)

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


def validate_glossary(value: list[str] | None) -> list[str] | None:
    """Normalize a glossary and enforce the term count and length limits.

    Returns None for None. Raises `ValueError` (reported as a 422) when more than
    `MAX_GLOSSARY_TERMS` terms remain after normalization or a term is longer than
    `MAX_GLOSSARY_TERM_LENGTH` characters.
    """
    if value is None:
        return None
    terms = normalize_glossary(value)
    if len(terms) > MAX_GLOSSARY_TERMS:
        raise ValueError(f"The glossary can hold at most {MAX_GLOSSARY_TERMS} terms.")
    if any(len(term) > MAX_GLOSSARY_TERM_LENGTH for term in terms):
        raise ValueError(f"A glossary term can have at most {MAX_GLOSSARY_TERM_LENGTH} characters.")
    return terms


class ProjectCreate(ApiModel):
    """Validate metadata required to create a project workspace."""

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    source_language: str = Field(default="auto", max_length=16)
    mode: OutputMode | None = None
    glossary: list[str] = Field(default_factory=list)

    @field_validator("glossary")
    @classmethod
    def validate_glossary_terms(cls, value: list[str]) -> list[str]:
        """Store the glossary normalized: trimmed, single-spaced, without blanks or repeats."""
        return validate_glossary(value) or []

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
    glossary: list[str] | None = None

    @field_validator("glossary")
    @classmethod
    def validate_glossary_terms(cls, value: list[str] | None) -> list[str] | None:
        """Normalize the replacement glossary; an empty list clears it."""
        return validate_glossary(value)

    @field_validator("name", "source_language", "mode", "glossary", mode="before")
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


class ProcessStart(ApiModel):
    """Validate the optional choices sent when processing starts (P05.4).

    `mode` sets the project type and is required when the project has none; `source_language`
    replaces the spoken language and `glossary` the project's terms. All are stored atomically
    with the queued state.
    """

    mode: OutputMode | None = None
    source_language: str | None = Field(default=None, max_length=16)
    glossary: list[str] | None = None

    @field_validator("glossary")
    @classmethod
    def validate_glossary_terms(cls, value: list[str] | None) -> list[str] | None:
        """Normalize the replacement glossary; an empty list clears it."""
        return validate_glossary(value)

    @field_validator("source_language")
    @classmethod
    def validate_source_language(cls, value: str | None) -> str | None:
        """Accept automatic detection or a lowercase Whisper-supported language code."""
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
    # Set once the original video was removed after export (P09.2); the metadata above stays.
    source_removed_at: datetime | None = None


class ProjectRecordingResponse(ApiModel):
    """Summarize the recording session of a project made in the recorder, for list views."""

    id: str
    state: RecordingState
    interrupted: bool


class ProjectResponse(ApiModel):
    """Expose project metadata, state, and optional validated source information."""

    id: str
    name: str
    description: str | None
    source_language: str
    mode: OutputMode | None
    glossary: list[str]
    detected_language: str | None
    status: ProjectState
    created_at: datetime
    updated_at: datetime
    video_asset_id: str | None
    active_job_id: str | None
    video_asset: MediaAssetResponse | None = None
    active_segment_count: int = 0
    screenshot_count: int = 0
    recording: ProjectRecordingResponse | None = None


class ProjectListResponse(ApiModel):
    """Return a stable pageless collection shape for the local project list."""

    items: list[ProjectResponse]
    total: int


class StorageCategoriesResponse(ApiModel):
    """Split a project's bytes by the folders of its workspace; the parts add up to the total."""

    source: int
    audio: int
    transcript: int
    screenshots: int
    exports: int
    other: int


class ProjectStorageResponse(ApiModel):
    """Report what one project uses on disk and whether its original video can be removed now."""

    project_id: str
    name: str
    status: ProjectState
    total_bytes: int
    categories: StorageCategoriesResponse
    # Size of the original video file on disk; 0 once removed or when the file is gone.
    source_video_bytes: int
    source_removed_at: datetime | None
    # None when removable; else `no_video`, `already_removed`, `busy` or `package_required`.
    removal_blocked_reason: str | None


class StorageResponse(ApiModel):
    """List the space each project uses, largest first, with the free space of the data volume."""

    total_bytes: int
    free_bytes: int | None
    items: list[ProjectStorageResponse]


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
    # `you`, `others` or null; `speakerSource` is `automatic` (loudness comparison of an in-app
    # recording), `user` (set or cleared in Review) or null when no label was ever set.
    speaker_label: SpeakerLabel | None
    speaker_source: SpeakerSource | None
    confidence: float | None
    created_at: datetime
    updated_at: datetime


class SegmentListResponse(ApiModel):
    """Return active reviewed segments in deterministic timeline order."""

    items: list[SegmentResponse]
    total: int


class SegmentUpdate(ApiModel):
    """Validate an explicit review save of the text, the speaker label, or both.

    `reviewedText` may be an intentional empty value. `speakerLabel` set to null clears the
    label; leaving the field out keeps it. At least one of the two fields must be sent.
    """

    reviewed_text: str | None = Field(default=None, max_length=100_000)
    speaker_label: SpeakerLabel | None = None

    @model_validator(mode="after")
    def require_a_change(self) -> SegmentUpdate:
        """Reject a body that names neither field, and an explicit null reviewed text."""
        if not self.model_fields_set & {"reviewed_text", "speaker_label"}:
            raise ValueError("Send reviewedText, speakerLabel or both.")
        if "reviewed_text" in self.model_fields_set and self.reviewed_text is None:
            raise ValueError("reviewedText cannot be null.")
        return self

    @property
    def updates_speaker(self) -> bool:
        """Return true when the request sets or clears the speaker label."""
        return "speaker_label" in self.model_fields_set


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
    """Expose export progress, the per-project number and the name the ZIP downloads under.

    `download_name` is the stored name, or `narrastep-{id}.zip` for exports made before names were
    stored; `sequence` is null only for such rows if they were never numbered.
    """

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
    sequence: int | None
    download_name: str
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


class RecordingCreate(ApiModel):
    """Validate the optional metadata of a new recording session and its project.

    The recorder sends the name in the interface language and local time (ADR 0015); without it
    the API names the project `Recording {YYYY-MM-DD HH:MM}` in the server's local time.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    source_language: str = Field(default="auto", max_length=16)

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        """Normalize a supplied name and reject one that holds only whitespace."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Recording name must not be empty.")
        return normalized

    @field_validator("source_language")
    @classmethod
    def validate_source_language(cls, value: str) -> str:
        """Accept automatic detection or a lowercase ISO-639-1 language code."""
        normalized = value.strip().lower()
        if normalized != "auto" and normalized not in SUPPORTED_WHISPER_LANGUAGES:
            raise ValueError("Source language must be 'auto' or a Whisper-supported language code.")
        return normalized


class RecordingComplete(ApiModel):
    """Validate the optional input of a recording's completion into the project source video.

    `name` replaces the project name when sent (the recorder's Save dialog). `stopAtGap` keeps
    each track only up to its first missing chunk, for an interrupted recording whose missing
    chunks can no longer be uploaded; without it a missing chunk refuses the completion.
    """

    name: str | None = Field(default=None, min_length=1, max_length=200)
    stop_at_gap: bool = False

    @field_validator("name")
    @classmethod
    def strip_optional_name(cls, value: str | None) -> str | None:
        """Normalize a supplied name and reject one that holds only whitespace."""
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Recording name must not be empty.")
        return normalized


class RecordingTrackResponse(ApiModel):
    """Report what is stored for one track so an interrupted recorder can resume its uploads.

    `nextSequence` is one past the highest stored sequence (0 when none is stored).
    `missingSequences` lists, in ascending order and at most 100 at a time, the sequences below
    it that are not stored; `missingCount` is their full number.
    """

    track: RecordingTrack
    chunk_count: int
    stored_bytes: int
    next_sequence: int
    missing_sequences: list[int]
    missing_count: int
    start_offset_ms: int | None


class RecordingCaptureResponse(ApiModel):
    """Capture settings the recorder applies to its `MediaRecorder` instances (P05.3).

    `chunkSeconds` is the `MediaRecorder` timeslice, `videoBitsPerSecond` the screen track's video
    bitrate and `maxFrameRate` the highest frame rate requested from the screen capture.
    """

    chunk_seconds: int
    video_bits_per_second: int
    max_frame_rate: int


class RecordingResponse(ApiModel):
    """Expose the state, per-track storage and capture settings of one recording session."""

    id: str
    project_id: str
    state: RecordingState
    interrupted: bool
    stored_bytes: int
    max_bytes: int
    created_at: datetime
    updated_at: datetime
    last_chunk_at: datetime | None
    tracks: list[RecordingTrackResponse]
    capture: RecordingCaptureResponse


class RecordingChunkResponse(ApiModel):
    """Acknowledge one durably stored chunk; `duplicate` is true for a repeated identical upload."""

    track: RecordingTrack
    sequence: int
    size_bytes: int
    duplicate: bool


class ModelFileResponse(ApiModel):
    """Name one pinned file of a generation model with its size; the path is never exposed."""

    name: str
    size_bytes: int


class ModelResponse(ApiModel):
    """Expose the install state, download progress and license of one local generation model.

    `error_message` is the readable English reason of a `failed` state and null otherwise;
    `downloaded_bytes` of a model without a stored row is 0.
    """

    id: str
    label: str
    state: ModelInstallState
    downloaded_bytes: int
    total_bytes: int
    files: list[ModelFileResponse]
    repository: str
    revision: str
    license_name: str
    license_url: str
    error_message: str | None
    updated_at: datetime | None


class ModelListResponse(ApiModel):
    """List the catalog models and say whether the kill switch allows downloads."""

    local_generation: str
    items: list[ModelResponse]


class ModelImportFileResponse(ApiModel):
    """Name one pinned file and say whether the import folder holds a file of that name and size."""

    name: str
    size_bytes: int
    found: bool


class ModelImportIgnoredResponse(ApiModel):
    """Name an entry of the import folder that is not a pinned model file; never a path."""

    name: str
    reason: Literal["not_a_pinned_model_file"]


class ModelImportPreviewResponse(ApiModel):
    """Show what an import would use and the license text the user must see before it.

    `folder_available` is false when the import folder is missing or unreadable. `ignored` holds at
    most 50 entries and `ignored_count` counts all of them. The SHA-256 is checked by the import.
    """

    model_id: str
    folder_available: bool
    files: list[ModelImportFileResponse]
    ignored: list[ModelImportIgnoredResponse]
    ignored_count: int
    license_name: str
    license_url: str
    license_text: str


class GenerationCreate(ApiModel):
    """Choose the optional language of the document to generate (P06.3d).

    Without `language` the document follows the transcript language.
    """

    language: str | None = Field(default=None, max_length=16)

    @field_validator("language")
    @classmethod
    def validate_document_language(cls, value: str | None) -> str | None:
        """Accept a lowercase Whisper-supported language code; `auto` and blank mean no choice."""
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized in {"", "auto"}:
            return None
        if normalized not in SUPPORTED_WHISPER_LANGUAGES:
            raise ValueError("Language must be a Whisper-supported language code.")
        return normalized


class GenerationModelFileResponse(ApiModel):
    """Record one model file that produced a document: name, size and the pinned SHA-256."""

    name: str
    size_bytes: int
    sha256: str


class GenerationResponse(ApiModel):
    """Expose the progress and result of one document generation.

    `parts_completed` of `parts_total` counts finished model requests per chronological part
    (`parts_total` is 0 until the parts are planned). `document_available` is true once the
    document can be read. `prompt_version` and `model_files` say what produced the document.
    """

    id: str
    project_id: str
    model_id: str
    status: JobStatus
    current_stage: str
    progress_percent: int
    message: str
    parts_total: int
    parts_completed: int
    language: str | None
    prompt_version: str
    model_files: list[GenerationModelFileResponse]
    document_available: bool
    size_bytes: int | None
    error_code: str | None
    error_message: str | None
    version: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class GenerationListResponse(ApiModel):
    """Return the generations of a project, newest first."""

    items: list[GenerationResponse]
    total: int


class ModelImportRequest(ApiModel):
    """Confirm that the user saw the license; the body names no path, because none is accepted."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid", use_enum_values=True
    )

    license_accepted: bool
