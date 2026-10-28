"""Add adaptive screenshot metadata and local diagnostics.

Revision ID: 20260720_0002
Revises: 20260715_0001
Create Date: 2026-07-20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20260720_0002"
down_revision = "20260715_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable derivatives and explainable adaptive-selection metadata."""
    with op.batch_alter_table("screenshots") as batch:
        batch.alter_column("relative_path", existing_type=sa.String(length=500), nullable=True)
        batch.add_column(sa.Column("thumbnail_relative_path", sa.String(length=500)))
        batch.add_column(
            sa.Column(
                "materialization_state",
                sa.String(length=30),
                nullable=False,
                server_default="png_ready",
            )
        )
        batch.add_column(
            sa.Column(
                "selection_reason",
                sa.String(length=50),
                nullable=False,
                server_default="legacy",
            )
        )
        batch.add_column(sa.Column("visual_change_score", sa.Float()))
        batch.add_column(sa.Column("text_change_score", sa.Float()))
        batch.add_column(sa.Column("cluster_id", sa.String(length=80)))
        batch.add_column(
            sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch.create_unique_constraint(
            "uq_screenshots_thumbnail_relative_path", ["thumbnail_relative_path"]
        )
        batch.create_index("ix_screenshots_cluster_id", ["cluster_id"], unique=False)

    with op.batch_alter_table("segment_screenshot_associations") as batch:
        batch.add_column(sa.Column("coverage_score", sa.Float()))
        batch.add_column(sa.Column("role_rank_reason", sa.String(length=80)))

    with op.batch_alter_table("processing_jobs") as batch:
        batch.add_column(
            sa.Column("configuration_snapshot", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(
            sa.Column("metrics_summary", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("metrics_version", sa.Integer()))
        batch.add_column(sa.Column("diagnostics_relative_path", sa.String(length=500)))

    with op.batch_alter_table("project_exports") as batch:
        batch.add_column(
            sa.Column("metrics_summary", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("metrics_version", sa.Integer()))
        batch.add_column(sa.Column("diagnostics_relative_path", sa.String(length=500)))


def downgrade() -> None:
    """Remove adaptive metadata while leaving original Version 0.2 columns intact."""
    with op.batch_alter_table("project_exports") as batch:
        batch.drop_column("diagnostics_relative_path")
        batch.drop_column("metrics_version")
        batch.drop_column("metrics_summary")

    with op.batch_alter_table("processing_jobs") as batch:
        batch.drop_column("diagnostics_relative_path")
        batch.drop_column("metrics_version")
        batch.drop_column("metrics_summary")
        batch.drop_column("configuration_snapshot")

    with op.batch_alter_table("segment_screenshot_associations") as batch:
        batch.drop_column("role_rank_reason")
        batch.drop_column("coverage_score")

    with op.batch_alter_table("screenshots") as batch:
        batch.drop_index("ix_screenshots_cluster_id")
        batch.drop_constraint("uq_screenshots_thumbnail_relative_path", type_="unique")
        batch.drop_column("is_mandatory")
        batch.drop_column("cluster_id")
        batch.drop_column("text_change_score")
        batch.drop_column("visual_change_score")
        batch.drop_column("selection_reason")
        batch.drop_column("materialization_state")
        batch.drop_column("thumbnail_relative_path")
        batch.alter_column("relative_path", existing_type=sa.String(length=500), nullable=False)
