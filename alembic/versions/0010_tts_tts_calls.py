"""tts_calls: one row per reply through the speech synthesiser.

Revision ID: 0010_tts
Revises: 0009_glossary
Create Date: 2026-09-14 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_tts"
down_revision: str | Sequence[str] | None = "0009_glossary"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tts_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("chars", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("tts_calls", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tts_calls_at"), ["at"], unique=False)
        batch_op.create_index(batch_op.f("ix_tts_calls_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("tts_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tts_calls_user_id"))
        batch_op.drop_index(batch_op.f("ix_tts_calls_at"))
    op.drop_table("tts_calls")
