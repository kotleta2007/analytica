"""sessions, and the session link + model-format copy on messages.

Revision ID: 0006_sessions
Revises: 0005_llm
Create Date: 2026-09-13 11:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_sessions"
down_revision: str | Sequence[str] | None = "0005_llm"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=8), nullable=False),
        sa.Column("topic", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("collapsed_summary", sa.Text(), nullable=True),
        sa.Column("collapsed_through_id", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sessions_user_id"), ["user_id"], unique=False)

    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("session_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("llm", sa.JSON(), nullable=True))
        batch_op.create_index(batch_op.f("ix_messages_session_id"), ["session_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_messages_session_id_sessions", "sessions", ["session_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.drop_constraint("fk_messages_session_id_sessions", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_messages_session_id"))
        batch_op.drop_column("llm")
        batch_op.drop_column("session_id")

    with op.batch_alter_table("sessions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sessions_user_id"))

    op.drop_table("sessions")
