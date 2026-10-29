"""Replace the Version 0.2 output modes with the four ADR 0009 project types.

Revision ID: 20261029_0003
Revises: 20260720_0002
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261029_0003"
down_revision = "20260720_0002"
branch_labels = None
depends_on = None

# Revisions are immutable, so both value sets are spelled out instead of importing the ORM enum.
LEGACY_MODES = ("tutorial", "meeting", "technical_walkthrough", "general")
PROJECT_TYPES = ("task_briefing", "meeting", "tutorial", "general")
# Wide enough for every value of both sets while the CHECK constraint is lifted.
TRANSITION_LENGTH = max(len(value) for value in LEGACY_MODES + PROJECT_TYPES)


def _mode_enum(values: tuple[str, ...]) -> sa.Enum:
    """Return the non-native SQLite enum, with its named CHECK constraint, for `values`."""
    return sa.Enum(*values, name="output_mode", native_enum=False, create_constraint=True)


def _replace_mode_values(
    old_values: tuple[str, ...], new_values: tuple[str, ...], renamed: tuple[str, str]
) -> None:
    """Rewrite `projects.output_mode` from `old_values` to `new_values`, renaming one value.

    SQLite cannot alter a CHECK constraint in place, so batch mode rebuilds the table twice:
    first as an unconstrained string so the renamed value is accepted, then with the new
    constraint. Rows, identifiers and other columns are copied unchanged. The Alembic engine
    (see `alembic/env.py`) does not enable `PRAGMA foreign_keys`, so the table rebuild does not
    cascade deletes into child tables; the migration test asserts that child rows survive.
    """
    with op.batch_alter_table("projects") as batch:
        batch.alter_column(
            "output_mode",
            existing_type=_mode_enum(old_values),
            type_=sa.String(length=TRANSITION_LENGTH),
            existing_nullable=False,
        )
    op.execute(
        sa.text("UPDATE projects SET output_mode = :new WHERE output_mode = :old").bindparams(
            old=renamed[0], new=renamed[1]
        )
    )
    with op.batch_alter_table("projects") as batch:
        batch.alter_column(
            "output_mode",
            existing_type=sa.String(length=TRANSITION_LENGTH),
            type_=_mode_enum(new_values),
            existing_nullable=False,
        )


def upgrade() -> None:
    """Map `technical_walkthrough` to `task_briefing`; other modes keep their value."""
    _replace_mode_values(LEGACY_MODES, PROJECT_TYPES, ("technical_walkthrough", "task_briefing"))


def downgrade() -> None:
    """Map `task_briefing` back to `technical_walkthrough`, the closest Version 0.2 mode.

    Projects created as task briefings after the upgrade also become technical walkthroughs;
    Version 0.2 has no other value that describes them.
    """
    _replace_mode_values(PROJECT_TYPES, LEGACY_MODES, ("task_briefing", "technical_walkthrough"))
