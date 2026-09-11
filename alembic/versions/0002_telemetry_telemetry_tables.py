"""telemetry tables: user_snapshots, tasks, task_snapshots, task_events, daily_records,
webhook_deliveries, user_settings.

Revision ID: 0002_telemetry
Revises: 0001_baseline
Create Date: 2026-09-11 17:32:11.565301

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_telemetry"
down_revision: str | Sequence[str] | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tasks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("habitica_id", sa.String(length=36), nullable=False),
        sa.Column("type", sa.String(length=8), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "habitica_id"),
    )
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_tasks_user_id"), ["user_id"], unique=False)

    op.create_table(
        "user_settings",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("model", sa.String(length=8), nullable=False),
        sa.Column("tts", sa.String(length=3), nullable=False),
        sa.Column("reminder_time", sa.String(length=5), nullable=True),
        sa.Column("gear_int", sa.JSON(), nullable=False),
        sa.Column("gear_per", sa.JSON(), nullable=False),
        sa.Column("gear_con", sa.JSON(), nullable=False),
        sa.Column("last_reminder_day", sa.Date(), nullable=True),
        sa.Column("dismissed_proposals", sa.JSON(), nullable=False),
        sa.Column("recent_suggestions", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "user_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("hp", sa.Float(), nullable=False),
        sa.Column("mp", sa.Float(), nullable=False),
        sa.Column("exp", sa.Float(), nullable=False),
        sa.Column("gp", sa.Float(), nullable=False),
        sa.Column("lvl", sa.Integer(), nullable=False),
        sa.Column("class", sa.String(length=16), nullable=False),
        sa.Column("to_next_level", sa.Integer(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("user_snapshots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_user_snapshots_taken_at"), ["taken_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_user_snapshots_user_id"), ["user_id"], unique=False)

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("received_at", sa.DateTime(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("webhook_deliveries", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_webhook_deliveries_received_at"), ["received_at"], unique=False
        )
        batch_op.create_index(
            batch_op.f("ix_webhook_deliveries_user_id"), ["user_id"], unique=False
        )

    op.create_table(
        "daily_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("was_due", sa.Boolean(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id", "day"),
    )
    with op.batch_alter_table("daily_records", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_daily_records_day"), ["day"], unique=False)
        batch_op.create_index(batch_op.f("ix_daily_records_user_id"), ["user_id"], unique=False)

    op.create_table(
        "task_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("direction", sa.String(length=4), nullable=True),
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_task_events_at"), ["at"], unique=False)
        batch_op.create_index("ix_task_events_task_id_at", ["task_id", "at"], unique=False)
        batch_op.create_index(batch_op.f("ix_task_events_user_id"), ["user_id"], unique=False)

    op.create_table(
        "task_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("taken_at", sa.DateTime(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=True),
        sa.Column("is_due", sa.Boolean(), nullable=True),
        sa.Column("streak", sa.Integer(), nullable=True),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("counter_up", sa.Integer(), nullable=True),
        sa.Column("counter_down", sa.Integer(), nullable=True),
        sa.Column("checklist_done", sa.Integer(), nullable=False),
        sa.Column("checklist_total", sa.Integer(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("task_snapshots", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_task_snapshots_taken_at"), ["taken_at"], unique=False)
        batch_op.create_index(batch_op.f("ix_task_snapshots_task_id"), ["task_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_task_snapshots_user_id"), ["user_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("task_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_snapshots_user_id"))
        batch_op.drop_index(batch_op.f("ix_task_snapshots_task_id"))
        batch_op.drop_index(batch_op.f("ix_task_snapshots_taken_at"))

    op.drop_table("task_snapshots")
    with op.batch_alter_table("task_events", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_task_events_user_id"))
        batch_op.drop_index("ix_task_events_task_id_at")
        batch_op.drop_index(batch_op.f("ix_task_events_at"))

    op.drop_table("task_events")
    with op.batch_alter_table("daily_records", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_daily_records_user_id"))
        batch_op.drop_index(batch_op.f("ix_daily_records_day"))

    op.drop_table("daily_records")
    with op.batch_alter_table("webhook_deliveries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_webhook_deliveries_user_id"))
        batch_op.drop_index(batch_op.f("ix_webhook_deliveries_received_at"))

    op.drop_table("webhook_deliveries")
    with op.batch_alter_table("user_snapshots", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_user_snapshots_user_id"))
        batch_op.drop_index(batch_op.f("ix_user_snapshots_taken_at"))

    op.drop_table("user_snapshots")
    op.drop_table("user_settings")
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_tasks_user_id"))

    op.drop_table("tasks")
