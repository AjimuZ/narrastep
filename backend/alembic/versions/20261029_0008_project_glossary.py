"""Add the per-project glossary passed to the transcriber as hints (P09.1).

Revision ID: 20261029_0008
Revises: 20261029_0007
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261029_0008"
down_revision = "20261029_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add `projects.glossary`, a JSON list of terms; existing projects get an empty list."""
    with op.batch_alter_table("projects") as batch:
        batch.add_column(
            sa.Column("glossary", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    """Drop `projects.glossary`; the saved terms are lost."""
    with op.batch_alter_table("projects") as batch:
        batch.drop_column("glossary")
