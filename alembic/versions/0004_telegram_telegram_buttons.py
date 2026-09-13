"""telegram_buttons: action and payload behind each inline button.

Revision ID: 0004_telegram
Revises: 0003_chat
Create Date: 2026-09-13 00:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_telegram"
down_revision: str | Sequence[str] | None = "0003_chat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_buttons",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("telegram_buttons", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_telegram_buttons_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("telegram_buttons", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_telegram_buttons_user_id"))

    op.drop_table("telegram_buttons")
