"""Create `generation_models` for the install state of the local generation models (P06.2a).

Revision ID: 20261030_0010
Revises: 20261029_0009
Create Date: 2026-10-30
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "20261030_0010"
down_revision = "20261029_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the table empty; a model without a row is `not_installed`, so nothing is installed."""
    op.create_table(
        "generation_models",
        sa.Column("model_id", sa.String(length=32), nullable=False),
        sa.Column(
            "state",
            sa.Enum(
                "not_installed",
                "downloading",
                "verifying",
                "installed",
                "enabled",
                "failed",
                name="model_install_state",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("downloaded_bytes", sa.BigInteger(), nullable=False),
        sa.Column("total_bytes", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.String(length=40), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("model_id"),
    )


def downgrade() -> None:
    """Drop the table; model files already under `/data/models` are left for the user to remove."""
    op.drop_table("generation_models")
