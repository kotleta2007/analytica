"""user_settings.cron_hold_until: our cron waits for the unticked-dailies answer.

Revision ID: 0011_cron_hold
Revises: 0010_tts
Create Date: 2026-09-18 21:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_cron_hold"
down_revision: str | Sequence[str] | None = "0010_tts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("user_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("cron_hold_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("user_settings", schema=None) as batch_op:
        batch_op.drop_column("cron_hold_until")
