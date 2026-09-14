"""llm_calls: one row per request to the model provider.

Revision ID: 0005_llm
Revises: 0004_telegram
Create Date: 2026-09-13 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_llm"
down_revision: str | Sequence[str] | None = "0004_telegram"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("tool_calls", sa.Integer(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_llm_calls_at"), ["at"], unique=False)
        batch_op.create_index(batch_op.f("ix_llm_calls_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("llm_calls", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_llm_calls_user_id"))
        batch_op.drop_index(batch_op.f("ix_llm_calls_at"))

    op.drop_table("llm_calls")
