"""Pull the account and its tasks, write snapshot rows, diff → task_events.

Two deliberate economies, both recorded in GAMEPLAN's deviations:

- `user_snapshots.raw` holds the game-state part of `/user` (stats,
  preferences, gear, cron), not the whole 40 KB object with the inbox in it.
- A `task_snapshots` row is written only when the task's JSON changed since
  the previous snapshot. The last row before any instant is still the state
  at that instant, so nothing is lost and the file stays copyable.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TaskEvent, TaskRow, TaskSnapshot, UserSnapshot
from app.habitica.client import HabiticaClient
from app.habitica.types import Task, UserStats

logger = logging.getLogger(__name__)

SOURCE = "snapshot_diff"
USER_RAW_KEYS = ("stats", "preferences", "lastCron", "needsCron", "tasksOrder")


def user_raw_for_snapshot(user: UserStats) -> dict[str, Any]:
    raw = {key: user.raw[key] for key in USER_RAW_KEYS if key in user.raw}
    raw["items"] = {"gear": user.raw.get("items", {}).get("gear", {})}
    return raw


async def snapshot_user(
    client: HabiticaClient, session: AsyncSession, user_id: str, now: datetime
) -> UserSnapshot:
    user = await client.get_user()
    snapshot = UserSnapshot(
        user_id=user_id,
        taken_at=now,
        hp=user.hp,
        mp=user.mp,
        exp=user.exp,
        gp=user.gp,
        lvl=user.lvl,
        class_name=user.class_name,
        to_next_level=user.to_next_level,
        raw=user_raw_for_snapshot(user),
    )
    session.add(snapshot)
    await session.flush()
    logger.info("user snapshot: hp=%s mp=%s gp=%s lvl=%s", user.hp, user.mp, user.gp, user.lvl)
    return snapshot


@dataclass
class SnapshotOutcome:
    seen: int
    changed: int
    events: list[TaskEvent]


async def collect(
    client: HabiticaClient, session: AsyncSession, user_id: str, now: datetime
) -> SnapshotOutcome:
    """One collection round: user first, then tasks. What the scheduler calls."""
    last_cron_before = await latest_last_cron(session, user_id)
    user_snapshot = await snapshot_user(client, session, user_id, now)
    cron_ran = user_snapshot.raw.get("lastCron") != last_cron_before
    return await snapshot_tasks(client, session, user_id, now, reset_by_cron=cron_ran)


async def latest_last_cron(session: AsyncSession, user_id: str) -> str | None:
    snapshot = await session.scalar(
        select(UserSnapshot)
        .where(UserSnapshot.user_id == user_id)
        .order_by(UserSnapshot.id.desc())
        .limit(1)
    )
    return None if snapshot is None else snapshot.raw.get("lastCron")


async def snapshot_tasks(
    client: HabiticaClient,
    session: AsyncSession,
    user_id: str,
    now: datetime,
    *,
    reset_by_cron: bool = False,
) -> SnapshotOutcome:
    """Upsert `tasks`, write `task_snapshots` for what changed, diff into `task_events`.

    `reset_by_cron`: cron ran since the previous snapshot, so a daily going from
    completed to not completed is the daily reset, not the user un-ticking it.
    """
    tasks = await client.get_tasks()
    # Completed todos leave /tasks/user; without this list they would look deleted.
    tasks += await client.get_completed_todos()

    rows = await load_task_rows(session, user_id)
    previous = await load_latest_snapshots(session, user_id)

    outcome = SnapshotOutcome(seen=len(tasks), changed=0, events=[])
    seen_ids: set[str] = set()
    for task in tasks:
        if task.id in seen_ids:
            continue
        seen_ids.add(task.id)
        row = rows.get(task.id)
        if row is None:
            row = new_task_row(task, user_id, now)
            session.add(row)
            await session.flush()
            rows[task.id] = row
            outcome.events.append(created_event(row, task, now))
        else:
            outcome.events.extend(update_events(row, task, now))
            refresh_task_row(row, task, now)

        before = previous.get(row.id)
        if before is not None:
            outcome.events.extend(diff_events(row, before, task, now, reset_by_cron))
        if before is None or before.raw != task.raw:
            session.add(task_snapshot(row, task, user_id, now))
            outcome.changed += 1

    for habitica_id, row in rows.items():
        if habitica_id not in seen_ids and row.deleted_at is None:
            row.deleted_at = now
            outcome.events.append(
                TaskEvent(
                    user_id=user_id, task_id=row.id, at=now, kind="deleted", source=SOURCE, raw=None
                )
            )

    session.add_all(outcome.events)
    await session.flush()
    logger.info(
        "task snapshot: %d tasks, %d changed, %d events",
        outcome.seen,
        outcome.changed,
        len(outcome.events),
    )
    return outcome


async def load_task_rows(session: AsyncSession, user_id: str) -> dict[str, TaskRow]:
    result = await session.scalars(select(TaskRow).where(TaskRow.user_id == user_id))
    return {row.habitica_id: row for row in result}


async def load_latest_snapshots(session: AsyncSession, user_id: str) -> dict[int, TaskSnapshot]:
    latest = (
        select(func.max(TaskSnapshot.id))
        .where(TaskSnapshot.user_id == user_id)
        .group_by(TaskSnapshot.task_id)
    )
    result = await session.scalars(select(TaskSnapshot).where(TaskSnapshot.id.in_(latest)))
    return {snapshot.task_id: snapshot for snapshot in result}


def new_task_row(task: Task, user_id: str, now: datetime) -> TaskRow:
    return TaskRow(
        user_id=user_id,
        habitica_id=task.id,
        type=task.type,
        text=task.text,
        notes=task.notes,
        created_at=task.created_at,
        first_seen_at=now,
        last_seen_at=now,
        raw=task.raw,
    )


def refresh_task_row(row: TaskRow, task: Task, now: datetime) -> None:
    row.type = task.type
    row.text = task.text
    row.notes = task.notes
    row.last_seen_at = now
    row.deleted_at = None
    row.raw = task.raw


def task_snapshot(row: TaskRow, task: Task, user_id: str, now: datetime) -> TaskSnapshot:
    return TaskSnapshot(
        user_id=user_id,
        task_id=row.id,
        taken_at=now,
        completed=task.completed,
        is_due=task.is_due,
        streak=task.streak,
        value=task.value,
        counter_up=task.counter_up,
        counter_down=task.counter_down,
        checklist_done=task.checklist_done,
        checklist_total=len(task.checklist),
        raw=task.raw,
    )


def created_event(row: TaskRow, task: Task, now: datetime) -> TaskEvent:
    """A task we have never seen. Its own `createdAt` is the honest timestamp."""
    return TaskEvent(
        user_id=row.user_id,
        task_id=row.id,
        at=task.created_at,
        kind="created",
        source=SOURCE,
        raw={"first_seen_at": now.isoformat()},
    )


def update_events(row: TaskRow, task: Task, now: datetime) -> list[TaskEvent]:
    """Text or notes changed since we last stored the task."""
    changes: dict[str, Any] = {}
    if row.text != task.text:
        changes["text"] = {"from": row.text, "to": task.text}
    if row.notes != task.notes:
        changes["notes"] = {"from": row.notes, "to": task.notes}
    if not changes:
        return []
    return [
        TaskEvent(
            user_id=row.user_id, task_id=row.id, at=now, kind="updated", source=SOURCE, raw=changes
        )
    ]


def diff_events(
    row: TaskRow, before: TaskSnapshot, task: Task, now: datetime, reset_by_cron: bool = False
) -> list[TaskEvent]:
    """What happened between the previous snapshot and this one."""
    events: list[TaskEvent] = []

    def event(kind: str, **fields: Any) -> TaskEvent:
        return TaskEvent(
            user_id=row.user_id, task_id=row.id, at=now, kind=kind, source=SOURCE, **fields
        )

    if task.type in ("daily", "todo") and before.completed != task.completed:
        kind = "completed" if task.completed else "uncompleted"
        daily_reset = task.type == "daily" and kind == "uncompleted" and reset_by_cron
        if not daily_reset:
            events.append(event(kind, delta=task.value - before.value, raw={"streak": task.streak}))

    if task.type == "habit":
        events.extend(_habit_scored_events(event, before, task))

    if before.checklist_done != task.checklist_done:
        events.append(
            event(
                "checklist",
                raw={
                    "done": task.checklist_done,
                    "total": len(task.checklist),
                    "was": before.checklist_done,
                },
            )
        )

    return events


def _habit_scored_events(event: Any, before: TaskSnapshot, task: Task) -> list[TaskEvent]:
    ups = max((task.counter_up or 0) - (before.counter_up or 0), 0)
    downs = max((task.counter_down or 0) - (before.counter_down or 0), 0)
    value_change = task.value - before.value
    if ups == 0 and downs == 0:
        # Counters reset daily/weekly/monthly; the value still moves with each score.
        if value_change == 0:
            return []
        direction = "up" if value_change > 0 else "down"
        return [event("scored", direction=direction, delta=value_change)]

    per_score = value_change / (ups + downs)
    return [event("scored", direction="up", delta=per_score) for _ in range(ups)] + [
        event("scored", direction="down", delta=per_score) for _ in range(downs)
    ]
