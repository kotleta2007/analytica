"""chat tables: conversation_state, messages.

Revision ID: 0003_chat
Revises: 0002_telemetry
Create Date: 2026-09-12 23:50:00.843093

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_chat"
down_revision: str | Sequence[str] | None = "0002_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversation_state",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_messages_at"), ["at"], unique=False)
        batch_op.create_index(batch_op.f("ix_messages_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_messages_user_id"))
        batch_op.drop_index(batch_op.f("ix_messages_at"))

    op.drop_table("messages")
    op.drop_table("conversation_state")
