"""Allow projects without a project type until processing starts (ADR 0010, P05.4).

Revision ID: 20261029_0006
Revises: 20261029_0005
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261029_0006"
down_revision = "20261029_0005"
branch_labels = None
depends_on = None

# Revisions are immutable, so the value set is spelled out instead of importing the ORM enum.
PROJECT_TYPES = ("task_briefing", "meeting", "tutorial", "general")


def _mode_enum() -> sa.Enum:
    """Return the non-native SQLite enum, with its named CHECK constraint, for the four types."""
    return sa.Enum(*PROJECT_TYPES, name="output_mode", native_enum=False, create_constraint=True)


def upgrade() -> None:
    """Make `projects.output_mode` nullable; every stored project keeps its type.

    SQLite cannot drop NOT NULL in place, so batch mode rebuilds the table. The CHECK constraint
    stays and accepts NULL. The Alembic engine does not enable `PRAGMA foreign_keys`, so the
    rebuild does not cascade deletes into child tables (see migration 20261029_0003).
    """
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("output_mode", existing_type=_mode_enum(), nullable=True)


def downgrade() -> None:
    """Give projects without a type the `general` type, then make the column required again."""
    op.execute(sa.text("UPDATE projects SET output_mode = 'general' WHERE output_mode IS NULL"))
    with op.batch_alter_table("projects") as batch:
        batch.alter_column("output_mode", existing_type=_mode_enum(), nullable=False)
