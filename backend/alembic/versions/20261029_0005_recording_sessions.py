"""Create `recording_sessions` and `recording_chunks` for in-app recording (ADR 0010).

Revision ID: 20261029_0005
Revises: 20261029_0004
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261029_0005"
down_revision = "20261029_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create both tables; existing projects have no recording session and are unchanged."""
    op.create_table(
        "recording_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "recording",
                "completed",
                name="recording_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("screen_start_offset_ms", sa.Integer(), nullable=True),
        sa.Column("microphone_start_offset_ms", sa.Integer(), nullable=True),
        sa.Column("last_chunk_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recording_sessions_project_id", "recording_sessions", ["project_id"], unique=True
    )

    op.create_table(
        "recording_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recording_id", sa.String(length=36), nullable=False),
        sa.Column(
            "track",
            sa.Enum(
                "screen",
                "microphone",
                name="recording_track",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["recording_id"], ["recording_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "recording_id", "track", "sequence", name="uq_recording_chunk_sequence"
        ),
    )


def downgrade() -> None:
    """Drop both tables; chunk files already on disk are left for the user to remove."""
    op.drop_table("recording_chunks")
    op.drop_index("ix_recording_sessions_project_id", table_name="recording_sessions")
    op.drop_table("recording_sessions")
