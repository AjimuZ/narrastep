"""Store the generation an export includes on `project_exports` (P06.4c).

Revision ID: 20261031_0012
Revises: 20261031_0011
Create Date: 2026-10-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261031_0012"
down_revision = "20261031_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable `generation_id`; existing exports stay null, so none of them changes."""
    op.add_column("project_exports", sa.Column("generation_id", sa.String(length=36)))


def downgrade() -> None:
    """Drop the column; ZIP files on disk keep their generated document."""
    with op.batch_alter_table("project_exports") as batch:
        batch.drop_column("generation_id")
