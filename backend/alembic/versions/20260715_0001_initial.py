"""Create the complete Narrastep 0.2 persistence model.

Revision ID: 20260715_0001
Revises: None
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260715_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the immutable Version 0.2 schema in dependency-safe order."""
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source_language", sa.String(length=16), nullable=False),
        sa.Column("detected_language", sa.String(length=16), nullable=True),
        sa.Column(
            "output_mode",
            sa.Enum(
                "tutorial",
                "meeting",
                "technical_walkthrough",
                "general",
                name="output_mode",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum(
                "draft",
                "uploaded",
                "queued",
                "processing",
                "review",
                "exporting",
                "completed",
                "failed",
                name="project_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_projects_state", "projects", ["state"], unique=False)

    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("display_filename", sa.String(length=255), nullable=False),
        sa.Column("stored_filename", sa.String(length=80), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("container_format", sa.String(length=80), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
    )
    op.create_index("ix_media_assets_project_id", "media_assets", ["project_id"], unique=True)

    op.create_table(
        "transcript_segments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("start_seconds", sa.Float(), nullable=False),
        sa.Column("end_seconds", sa.Float(), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("reviewed_text", sa.Text(), nullable=False),
        sa.Column("raw_words", sa.JSON(), nullable=False),
        sa.Column("speaker", sa.String(length=100), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_active_segment_order",
        "transcript_segments",
        ["project_id", "sequence"],
        unique=False,
    )
    op.create_index(
        "ix_transcript_segments_active", "transcript_segments", ["active"], unique=False
    )

    op.create_table(
        "screenshots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("timestamp_seconds", sa.Float(), nullable=False),
        sa.Column("perceptual_hash", sa.String(length=16), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("relative_path"),
    )

    op.create_table(
        "segment_screenshot_associations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("screenshot_id", sa.String(length=36), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("user_selected", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["screenshot_id"], ["screenshots.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "screenshot_id", name="uq_segment_screenshot"),
    )
    op.create_index(
        "ix_segment_screenshot_associations_screenshot_id",
        "segment_screenshot_associations",
        ["screenshot_id"],
        unique=False,
    )
    op.create_index(
        "ix_segment_screenshot_associations_segment_id",
        "segment_screenshot_associations",
        ["segment_id"],
        unique=False,
    )
    op.create_index(
        "uq_primary_screenshot_per_segment",
        "segment_screenshot_associations",
        ["segment_id"],
        unique=True,
        sqlite_where=sa.text("is_primary = 1"),
    )

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "processing",
                "cancelling",
                "completed",
                "failed",
                "cancelled",
                "interrupted",
                name="job_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("stage", sa.String(length=80), nullable=False),
        sa.Column("message", sa.String(length=300), nullable=False),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("progress_version", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("celery_task_id", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_processing_jobs_status", "processing_jobs", ["status"], unique=False)
    op.create_index(
        "uq_active_processing_job_per_project",
        "processing_jobs",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'processing', 'cancelling')"),
    )

    op.create_table(
        "project_exports",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "queued",
                "processing",
                "completed",
                "failed",
                name="export_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("progress_percent", sa.Integer(), nullable=False),
        sa.Column("current_stage", sa.String(length=80), nullable=True),
        sa.Column("include_original_video", sa.Boolean(), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("celery_task_id", sa.String(length=80), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_project_exports_status", "project_exports", ["status"], unique=False)
    op.create_index(
        "uq_active_export_per_project",
        "project_exports",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'processing')"),
    )

    op.create_table(
        "segment_lineage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("child_segment_id", sa.String(length=36), nullable=False),
        sa.Column("source_segment_id", sa.String(length=36), nullable=False),
        sa.Column("operation", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["child_segment_id"], ["transcript_segments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_segment_id"], ["transcript_segments.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("child_segment_id", "source_segment_id", name="uq_segment_lineage"),
    )
    op.create_index(
        "ix_segment_lineage_child_segment_id",
        "segment_lineage",
        ["child_segment_id"],
        unique=False,
    )
    op.create_index(
        "ix_segment_lineage_source_segment_id",
        "segment_lineage",
        ["source_segment_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the Version 0.2 schema in exact reverse dependency order."""
    op.drop_index("ix_segment_lineage_source_segment_id", table_name="segment_lineage")
    op.drop_index("ix_segment_lineage_child_segment_id", table_name="segment_lineage")
    op.drop_table("segment_lineage")

    op.drop_index("uq_active_export_per_project", table_name="project_exports")
    op.drop_index("ix_project_exports_status", table_name="project_exports")
    op.drop_table("project_exports")

    op.drop_index("uq_active_processing_job_per_project", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_status", table_name="processing_jobs")
    op.drop_table("processing_jobs")

    op.drop_index(
        "uq_primary_screenshot_per_segment",
        table_name="segment_screenshot_associations",
    )
    op.drop_index(
        "ix_segment_screenshot_associations_segment_id",
        table_name="segment_screenshot_associations",
    )
    op.drop_index(
        "ix_segment_screenshot_associations_screenshot_id",
        table_name="segment_screenshot_associations",
    )
    op.drop_table("segment_screenshot_associations")

    op.drop_table("screenshots")

    op.drop_index("ix_transcript_segments_active", table_name="transcript_segments")
    op.drop_index("ix_active_segment_order", table_name="transcript_segments")
    op.drop_table("transcript_segments")

    op.drop_index("ix_media_assets_project_id", table_name="media_assets")
    op.drop_table("media_assets")

    op.drop_index("ix_projects_state", table_name="projects")
    op.drop_table("projects")
