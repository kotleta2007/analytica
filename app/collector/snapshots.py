"""Pull the account and its tasks, write snapshot rows, diff → task_events.

Every round writes one `user_snapshots` row and one `task_snapshots` row per
task. Task JSON goes through `raw_blobs`, so a task that did not change costs
a pointer, not another copy. Collect everything; decide what to use later.

`source` names who caused the events a round finds: `snapshot_diff` for the
scheduled rounds, `bot` for the round the harness runs right after a tool
changed a task — that round's diff *is* the record of what the bot did, and
the next scheduled round then sees no change and records nothing twice.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.blobs import store_blobs
from app.db.models import TaskEvent, TaskRow, TaskSnapshot, UserSnapshot
from app.habitica.client import HabiticaClient
from app.habitica.types import Task

logger = logging.getLogger(__name__)

SOURCE = "snapshot_diff"


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
        raw=user.raw,
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


@dataclass
class CollectOutcome:
    user: UserSnapshot
    tasks: SnapshotOutcome
    cron_ran: bool


async def collect(
    client: HabiticaClient,
    session: AsyncSession,
    user_id: str,
    now: datetime,
    *,
    source: str = SOURCE,
) -> CollectOutcome:
    """One collection round: user first, then tasks. What the scheduler calls."""
    last_cron_before = await latest_last_cron(session, user_id)
    user_snapshot = await snapshot_user(client, session, user_id, now)
    cron_ran = user_snapshot.raw.get("lastCron") != last_cron_before
    tasks = await snapshot_tasks(
        client, session, user_id, now, reset_by_cron=cron_ran, source=source
    )
    await remember_task_order(session, user_id, user_snapshot.raw.get("tasksOrder", {}))
    return CollectOutcome(user=user_snapshot, tasks=tasks, cron_ran=cron_ran)


ORDER_LISTS = {"habits": "habit", "dailys": "daily", "todos": "todo", "rewards": "reward"}


async def remember_task_order(
    session: AsyncSession, user_id: str, tasks_order: dict[str, list[str]]
) -> None:
    """`tasksOrder` from `/user` → `tasks.position`, one list per type."""
    for list_name in ORDER_LISTS:
        await remember_list_order(session, user_id, tasks_order.get(list_name, []))


async def remember_list_order(session: AsyncSession, user_id: str, ordered_ids: list[str]) -> None:
    """The new order of one type's list, as the app shows it. No snapshot is involved:
    a reorder is not telemetry, it is a fact we keep so the model sees the list as the user does."""
    if not ordered_ids:
        return
    rows = await load_task_rows(session, user_id)
    for index, habitica_id in enumerate(ordered_ids):
        row = rows.get(habitica_id)
        if row is not None:
            row.position = index
    await session.flush()


async def collect_if_stale(
    client: HabiticaClient,
    session: AsyncSession,
    user_id: str,
    now: datetime,
    max_age: timedelta,
) -> UserSnapshot:
    """The latest user snapshot, refreshed by a collection round when older than `max_age`.

    What a chat turn calls before answering: fresh enough to be honest, without
    a round trip to Habitica for every message.
    """
    snapshot = await latest_user_snapshot(session, user_id)
    if snapshot is not None and now - snapshot.taken_at <= max_age:
        return snapshot
    return (await collect(client, session, user_id, now)).user


async def latest_user_snapshot(session: AsyncSession, user_id: str) -> UserSnapshot | None:
    return await session.scalar(
        select(UserSnapshot)
        .where(UserSnapshot.user_id == user_id)
        .order_by(UserSnapshot.id.desc())
        .limit(1)
    )


async def latest_last_cron(session: AsyncSession, user_id: str) -> str | None:
    snapshot = await latest_user_snapshot(session, user_id)
    return None if snapshot is None else snapshot.raw.get("lastCron")


async def snapshot_tasks(
    client: HabiticaClient,
    session: AsyncSession,
    user_id: str,
    now: datetime,
    *,
    reset_by_cron: bool = False,
    source: str = SOURCE,
) -> SnapshotOutcome:
    """Upsert `tasks`, write a `task_snapshots` row per task, diff into `task_events`.

    `reset_by_cron`: cron ran since the previous snapshot, so a daily going from
    completed to not completed is the daily reset, not the user un-ticking it.
    """
    tasks = await client.get_tasks()
    # Completed todos leave /tasks/user; without this list they would look deleted.
    tasks += await client.get_completed_todos()

    rows = await load_task_rows(session, user_id)
    previous = await load_latest_snapshots(session, user_id)
    hashes = await store_blobs(session, [task.raw for task in tasks])

    outcome = SnapshotOutcome(seen=len(tasks), changed=0, events=[])
    seen_ids: set[str] = set()
    for task, raw_hash in zip(tasks, hashes, strict=True):
        if task.id in seen_ids:
            continue
        seen_ids.add(task.id)
        row = rows.get(task.id)
        if row is None:
            row = new_task_row(task, user_id, now)
            session.add(row)
            await session.flush()
            rows[task.id] = row
            outcome.events.append(created_event(row, task, now, source))
        else:
            outcome.events.extend(update_events(row, task, now, source))
            refresh_task_row(row, task, now)

        before = previous.get(row.id)
        if before is not None:
            outcome.events.extend(diff_events(row, before, task, now, reset_by_cron, source))
        session.add(task_snapshot(row, task, user_id, now, raw_hash))
        if before is None or before.raw_hash != raw_hash:
            outcome.changed += 1

    for habitica_id, row in rows.items():
        if habitica_id in seen_ids or row.deleted_at is not None:
            continue
        row.deleted_at = now
        before = previous.get(row.id)
        if row.type == "todo" and before is not None and before.completed:
            continue  # Habitica purges old completed todos; that is not the user deleting one
        outcome.events.append(
            TaskEvent(
                user_id=user_id, task_id=row.id, at=now, kind="deleted", source=source, raw=None
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


def task_snapshot(
    row: TaskRow, task: Task, user_id: str, now: datetime, raw_hash: str
) -> TaskSnapshot:
    """`raw_hash` comes from `store_blobs(session, [task.raw])`."""
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
        raw_hash=raw_hash,
    )


def created_event(row: TaskRow, task: Task, now: datetime, source: str = SOURCE) -> TaskEvent:
    """A task we have never seen. Its own `createdAt` is the honest timestamp."""
    return TaskEvent(
        user_id=row.user_id,
        task_id=row.id,
        at=task.created_at,
        kind="created",
        source=source,
        raw={"first_seen_at": now.isoformat()},
    )


def update_events(row: TaskRow, task: Task, now: datetime, source: str = SOURCE) -> list[TaskEvent]:
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
            user_id=row.user_id, task_id=row.id, at=now, kind="updated", source=source, raw=changes
        )
    ]


def diff_events(
    row: TaskRow,
    before: TaskSnapshot,
    task: Task,
    now: datetime,
    reset_by_cron: bool = False,
    source: str = SOURCE,
) -> list[TaskEvent]:
    """What happened between the previous snapshot and this one."""
    events: list[TaskEvent] = []

    def event(kind: str, **fields: Any) -> TaskEvent:
        return TaskEvent(
            user_id=row.user_id, task_id=row.id, at=now, kind=kind, source=source, **fields
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
