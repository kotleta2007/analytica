"""empty baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-10 14:43:43.075077

"""

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema yet: the telemetry tables arrive in phase 01."""


def downgrade() -> None:
    pass
