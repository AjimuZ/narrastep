"""Record when the original recording of a project was removed after export (P09.2).

Revision ID: 20261029_0009
Revises: 20261029_0008
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261029_0009"
down_revision = "20261029_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add `media_assets.source_removed_at`; existing projects keep their video (NULL)."""
    with op.batch_alter_table("media_assets") as batch:
        batch.add_column(sa.Column("source_removed_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Drop `media_assets.source_removed_at`; a removed video stays removed but looks present."""
    with op.batch_alter_table("media_assets") as batch:
        batch.drop_column("source_removed_at")
