"""Create `generations` for the local document generations of a project (P06.3d).

Revision ID: 20261031_0011
Revises: 20261030_0010
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261031_0011"
down_revision = "20261030_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the table empty; no project has a generation until the user asks for one."""
    op.create_table(
        "generations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("model_id", sa.String(length=32), nullable=False),
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
                name="generation_status",
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
        sa.Column("parts_total", sa.Integer(), nullable=False),
        sa.Column("parts_completed", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("prompt_version", sa.String(length=40), nullable=False),
        sa.Column("model_files", sa.JSON(), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
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
    op.create_index("ix_generations_status", "generations", ["status"], unique=False)
    op.create_index(
        "uq_active_generation_per_project",
        "generations",
        ["project_id"],
        unique=True,
        sqlite_where=sa.text("status IN ('queued', 'processing', 'cancelling')"),
    )


def downgrade() -> None:
    """Drop the table; document files under `projects/{id}/generations/` stay on disk."""
    op.drop_index("uq_active_generation_per_project", table_name="generations")
    op.drop_index("ix_generations_status", table_name="generations")
    op.drop_table("generations")
