"""tasks.position: the order the user arranged in the app.

Revision ID: 0007_task_position
Revises: 0006_sessions
Create Date: 2026-09-14 13:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_task_position"
down_revision: str | Sequence[str] | None = "0006_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(sa.Column("position", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_column("position")
