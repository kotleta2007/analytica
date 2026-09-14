"""asr_glossary: the words the recogniser gets wrong for this user.

Revision ID: 0009_glossary
Revises: 0008_transcriptions
Create Date: 2026-09-14 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_glossary"
down_revision: str | Sequence[str] | None = "0008_transcriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asr_glossary",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("term", sa.String(length=64), nullable=False),
        sa.Column("added_from", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("asr_glossary", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_asr_glossary_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("asr_glossary", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_asr_glossary_user_id"))
    op.drop_table("asr_glossary")
