"""Store each export's number and download name on `project_exports`.

Revision ID: 20261029_0004
Revises: 20261029_0003
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261029_0004"
down_revision = "20261029_0003"
branch_labels = None
depends_on = None

SEQUENCE_INDEX = "uq_export_sequence_per_project"


def upgrade() -> None:
    """Add `sequence` and `download_name`; number existing exports by creation order.

    Existing rows get a sequence but no download name: their archives use the Version 0.2 layout
    and are still served as `narrastep-{id}.zip`. Ties on `created_at` are broken by `id`, the same
    order the worker used before the number was stored. SQLite allows several NULL sequences under
    the unique index, so a row inserted without a number (tests, manual repair) does not collide.
    """
    op.add_column("project_exports", sa.Column("sequence", sa.Integer(), nullable=True))
    op.add_column("project_exports", sa.Column("download_name", sa.String(length=255)))
    op.execute(
        sa.text(
            "UPDATE project_exports SET sequence = ("
            "SELECT COUNT(*) FROM project_exports AS earlier "
            "WHERE earlier.project_id = project_exports.project_id "
            "AND (earlier.created_at < project_exports.created_at "
            "OR (earlier.created_at = project_exports.created_at "
            "AND earlier.id <= project_exports.id)))"
        )
    )
    op.create_index(SEQUENCE_INDEX, "project_exports", ["project_id", "sequence"], unique=True)


def downgrade() -> None:
    """Drop the index and both columns; the ZIP files on disk are untouched."""
    op.drop_index(SEQUENCE_INDEX, table_name="project_exports")
    with op.batch_alter_table("project_exports") as batch:
        batch.drop_column("download_name")
        batch.drop_column("sequence")
