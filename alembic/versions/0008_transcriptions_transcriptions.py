"""transcriptions: one row per voice note through the recogniser.

Revision ID: 0008_transcriptions
Revises: 0007_task_position
Create Date: 2026-09-14 19:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_transcriptions"
down_revision: str | Sequence[str] | None = "0007_task_position"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transcriptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("cleaned_text", sa.Text(), nullable=True),
        sa.Column("accepted_text", sa.Text(), nullable=True),
        sa.Column("language", sa.String(length=8), nullable=True),
        sa.Column("audio_seconds", sa.Float(), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("transcriptions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_transcriptions_at"), ["at"], unique=False)
        batch_op.create_index(batch_op.f("ix_transcriptions_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("transcriptions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_transcriptions_user_id"))
        batch_op.drop_index(batch_op.f("ix_transcriptions_at"))
    op.drop_table("transcriptions")
