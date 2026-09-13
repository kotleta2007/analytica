"""The user's tasks as our database knows them.

The model never gets a live task list: it sees what the last collection round
wrote to `tasks` (refreshed at the start of a turn when stale, and after every
change the bot makes). Task ids are shown shortened to their first eight
characters — a 36-character UUID per task costs the prompt more than it says —
and resolved back by prefix.
"""

from dataclasses import dataclass

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


async def load_known_tasks(session: AsyncSession, user_id: str) -> list[KnownTask]:
    """Every task Habitica still has, parsed from the JSON of its latest sighting."""
    rows = await session.scalars(
        select(TaskRow)
        .where(TaskRow.user_id == user_id, TaskRow.deleted_at.is_(None))
        .order_by(TaskRow.id)
    )
    return [KnownTask(row=row, task=task_from_json(row.raw)) for row in rows]


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
