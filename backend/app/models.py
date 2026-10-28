"""Relational model for projects, evidence, processing, review, and exports."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def new_uuid() -> str:
    """Return a lowercase UUID string suitable for identifiers and safe paths."""
    return str(uuid.uuid4())


def utc_now() -> datetime:
    """Return an aware UTC timestamp for persisted lifecycle events."""
    return datetime.now(UTC)


class ProjectState(StrEnum):
    """Represent the user-visible project lifecycle."""

    DRAFT = "draft"
    UPLOADED = "uploaded"
    QUEUED = "queued"
    PROCESSING = "processing"
    REVIEW = "review"
    EXPORTING = "exporting"
    COMPLETED = "completed"
    FAILED = "failed"


class JobStatus(StrEnum):
    """Represent the durable state of background processing work."""

    QUEUED = "queued"
    RUNNING = "processing"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class ExportStatus(StrEnum):
    """Represent the durable state of an immutable export artifact."""

    QUEUED = "queued"
    RUNNING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class OutputMode(StrEnum):
    """Describe the intended downstream document style for the export prompt."""

    TUTORIAL = "tutorial"
    MEETING = "meeting"
    TECHNICAL_WALKTHROUGH = "technical_walkthrough"
    GENERAL = "general"


class Project(Base):
    """Persist project metadata and the authoritative workflow state."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_language: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    detected_language: Mapped[str | None] = mapped_column(String(16))
    output_mode: Mapped[OutputMode] = mapped_column(
        Enum(
            OutputMode,
            name="output_mode",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=OutputMode.TUTORIAL,
        nullable=False,
    )
    state: Mapped[ProjectState] = mapped_column(
        Enum(
            ProjectState,
            name="project_state",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=ProjectState.DRAFT,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    media_asset: Mapped[MediaAsset | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False, lazy="selectin"
    )
    segments: Mapped[list[TranscriptSegment]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    screenshots: Mapped[list[Screenshot]] = relationship(
        back_populates="project", cascade="all, delete-orphan", lazy="selectin"
    )
    jobs: Mapped[list[ProcessingJob]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    exports: Mapped[list[ProjectExport]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class MediaAsset(Base):
    """Describe the single validated source video owned by a project."""

    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    display_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(80), nullable=False)
    relative_path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    container_format: Mapped[str] = mapped_column(String(80), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="media_asset")


class TranscriptSegment(Base):
    """Persist immutable evidence fields alongside editable reviewed text."""

    __tablename__ = "transcript_segments"
    __table_args__ = (Index("ix_active_segment_order", "project_id", "sequence", unique=False),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    reviewed_text: Mapped[str] = mapped_column(Text, nullable=False)
    raw_words: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    speaker: Mapped[str | None] = mapped_column(String(100))
    confidence: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="segments")
    screenshot_associations: Mapped[list[SegmentScreenshotAssociation]] = relationship(
        back_populates="segment", cascade="all, delete-orphan", lazy="selectin"
    )


class Screenshot(Base):
    """Describe an extracted frame candidate stored on the local filesystem."""

    __tablename__ = "screenshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    relative_path: Mapped[str | None] = mapped_column(String(500), nullable=True, unique=True)
    thumbnail_relative_path: Mapped[str | None] = mapped_column(
        String(500), nullable=True, unique=True
    )
    materialization_state: Mapped[str] = mapped_column(
        String(30), default="png_ready", nullable=False
    )
    selection_reason: Mapped[str] = mapped_column(String(50), default="legacy", nullable=False)
    visual_change_score: Mapped[float | None] = mapped_column(Float)
    text_change_score: Mapped[float | None] = mapped_column(Float)
    cluster_id: Mapped[str | None] = mapped_column(String(80), index=True)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    perceptual_hash: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="screenshots")
    segment_associations: Mapped[list[SegmentScreenshotAssociation]] = relationship(
        back_populates="screenshot", cascade="all, delete-orphan"
    )


class SegmentScreenshotAssociation(Base):
    """Rank a reusable screenshot candidate for one active transcript segment."""

    __tablename__ = "segment_screenshot_associations"
    __table_args__ = (
        UniqueConstraint("segment_id", "screenshot_id", name="uq_segment_screenshot"),
        Index(
            "uq_primary_screenshot_per_segment",
            "segment_id",
            unique=True,
            sqlite_where=text("is_primary = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    screenshot_id: Mapped[str] = mapped_column(
        ForeignKey("screenshots.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    coverage_score: Mapped[float | None] = mapped_column(Float)
    role_rank_reason: Mapped[str | None] = mapped_column(String(80))
    user_selected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    segment: Mapped[TranscriptSegment] = relationship(back_populates="screenshot_associations")
    screenshot: Mapped[Screenshot] = relationship(
        back_populates="segment_associations", lazy="selectin"
    )


class ProcessingJob(Base):
    """Persist process progress so UI state survives refreshes and restarts."""

    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index(
            "uq_active_processing_job_per_project",
            "project_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'processing', 'cancelling')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=JobStatus.QUEUED,
        nullable=False,
        index=True,
    )
    stage: Mapped[str] = mapped_column(String(80), default="queued", nullable=False)
    message: Mapped[str] = mapped_column(
        String(300), default="Waiting for the local worker.", nullable=False
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    configuration_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    metrics_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metrics_version: Mapped[int | None] = mapped_column(Integer)
    diagnostics_relative_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="jobs")


class ProjectExport(Base):
    """Persist an immutable export request and its generated ZIP location."""

    __tablename__ = "project_exports"
    __table_args__ = (
        Index(
            "uq_active_export_per_project",
            "project_id",
            unique=True,
            sqlite_where=text("status IN ('queued', 'processing')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[ExportStatus] = mapped_column(
        Enum(
            ExportStatus,
            name="export_status",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda values: [item.value for item in values],
        ),
        default=ExportStatus.QUEUED,
        nullable=False,
        index=True,
    )
    progress_percent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(80), default="queued")
    include_original_video: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    relative_path: Mapped[str | None] = mapped_column(String(500))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    celery_task_id: Mapped[str | None] = mapped_column(String(80))
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(500))
    metrics_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    metrics_version: Mapped[int | None] = mapped_column(Integer)
    diagnostics_relative_path: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project] = relationship(back_populates="exports")


class SegmentLineage(Base):
    """Record which archived source segments produced each reviewed child segment."""

    __tablename__ = "segment_lineage"
    __table_args__ = (
        UniqueConstraint("child_segment_id", "source_segment_id", name="uq_segment_lineage"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    child_segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_segment_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_segments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    operation: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
