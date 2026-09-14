"""The user's tasks as our database knows them.

The model never gets a live task list: it sees what the last collection round
wrote to `tasks` (refreshed at the start of a turn when stale, and after every
change the bot makes). Task ids are shown shortened to their first eight
characters — a 36-character UUID per task costs the prompt more than it says —
and resolved back by prefix.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TaskRow
from app.habitica.types import Task, task_from_json

SHORT_ID_LENGTH = 8


class TaskNotFound(LookupError):
    pass


@dataclass(frozen=True)
class KnownTask:
    row: TaskRow
    task: Task

    @property
    def short_id(self) -> str:
        return short_id(self.row.habitica_id)


def short_id(habitica_id: str) -> str:
    return habitica_id[:SHORT_ID_LENGTH]


def due_day(task: Task, tz: ZoneInfo) -> date | None:
    """The calendar day a todo is due, as the user sees it.

    Habitica stores a due date as local midnight in UTC — a todo due 14/09 in
    Zurich is `2026-09-13T22:00:00Z` — so the day must be read in the user's
    timezone, never from the UTC value.
    """
    if task.due_date is None:
        return None
    return task.due_date.astimezone(tz).date()


def due_datetime(day: date, tz: ZoneInfo) -> datetime:
    """The value to send Habitica for a due day: that day's local midnight, in UTC."""
    return datetime(day.year, day.month, day.day, tzinfo=tz).astimezone(UTC)


async def load_known_tasks(session: AsyncSession, user_id: str) -> list[KnownTask]:
    """Every task Habitica still has, in the order the user sees it in the app.

    Habits, then dailies, then todos, each list as arranged (`tasks.position`,
    kept from `tasksOrder` and from the bot's own moves); a task not placed yet
    — created since the last look — goes after the others of its type.
    """
    rows = await session.scalars(
        select(TaskRow)
        .where(TaskRow.user_id == user_id, TaskRow.deleted_at.is_(None))
        .order_by(TaskRow.id)
    )
    known = [KnownTask(row=row, task=task_from_json(row.raw)) for row in rows]
    return sorted(known, key=order_key)


TYPE_RANK = {"habit": 0, "daily": 1, "todo": 2, "reward": 3}


def order_key(known: KnownTask) -> tuple[int, int, int]:
    """Type, then the app's position, then first-seen; unplaced tasks after the placed ones."""
    position = known.row.position
    return (
        TYPE_RANK.get(known.task.type, 9),
        UNPLACED if position is None else position,
        known.row.id,
    )


UNPLACED = 1 << 30


def open_tasks(known: list[KnownTask]) -> list[KnownTask]:
    """What the user can still act on: everything but completed todos and rewards."""
    return [
        k
        for k in known
        if k.task.type != "reward" and not (k.task.type == "todo" and k.task.completed)
    ]


async def resolve_task(session: AsyncSession, user_id: str, reference: str) -> KnownTask:
    """A full or shortened id → the task; raises `TaskNotFound` with a reason."""
    reference = reference.strip().lower()
    if not reference:
        raise TaskNotFound("empty task id")
    matches = [
        k
        for k in await load_known_tasks(session, user_id)
        if k.row.habitica_id.lower().startswith(reference)
    ]
    if not matches:
        raise TaskNotFound(f"no task with id {reference!r}; use list_tasks to see the ids")
    if len(matches) > 1:
        raise TaskNotFound(f"id {reference!r} matches several tasks; pass more characters")
    return matches[0]
