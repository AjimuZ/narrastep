"""Constrain speaker labels to You / Others and record who set them (ADR 0010, P05.5).

Revision ID: 20261029_0007
Revises: 20261029_0006
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261029_0007"
down_revision = "20261029_0006"
branch_labels = None
depends_on = None

# Revisions are immutable, so the value sets are spelled out instead of importing the ORM enums.
SPEAKER_LABELS = ("you", "others")
SPEAKER_SOURCES = ("automatic", "user")


def upgrade() -> None:
    """Turn `transcript_segments.speaker` into the `you` / `others` enum and add `speaker_source`.

    No release wrote a speaker before this revision (processing always stored NULL), but any
    other stored value is cleared first so the new CHECK constraint can be created. SQLite cannot
    change a column type or add a constraint in place, so batch mode rebuilds the table; the
    Alembic engine does not enable `PRAGMA foreign_keys`, so the rebuild does not cascade deletes
    into associations or lineage (see migration 20261029_0003).
    """
    labels = ", ".join(f"'{value}'" for value in SPEAKER_LABELS)
    op.execute(
        sa.text(f"UPDATE transcript_segments SET speaker = NULL WHERE speaker NOT IN ({labels})")
    )
    with op.batch_alter_table("transcript_segments") as batch:
        batch.alter_column(
            "speaker",
            existing_type=sa.String(length=100),
            type_=sa.Enum(
                *SPEAKER_LABELS, name="speaker_label", native_enum=False, create_constraint=True
            ),
            existing_nullable=True,
        )
        batch.add_column(
            sa.Column(
                "speaker_source",
                sa.Enum(
                    *SPEAKER_SOURCES,
                    name="speaker_source",
                    native_enum=False,
                    create_constraint=True,
                ),
                nullable=True,
            )
        )


def downgrade() -> None:
    """Drop `speaker_source` and return `speaker` to free text; stored labels are kept.

    The named CHECK constraints are dropped explicitly: the batch rebuild would otherwise copy
    them onto the new table, where one names a column that no longer exists.
    """
    with op.batch_alter_table("transcript_segments") as batch:
        batch.drop_constraint("speaker_source", type_="check")
        batch.drop_constraint("speaker_label", type_="check")
        batch.drop_column("speaker_source")
        batch.alter_column(
            "speaker",
            existing_type=sa.Enum(*SPEAKER_LABELS, name="speaker_label", native_enum=False),
            type_=sa.String(length=100),
            existing_nullable=True,
        )
