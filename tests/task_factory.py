"""Build Habitica Task objects by hand, for collector and analytics tests."""

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.habitica.types import Task, task_from_json

CREATED = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def make_task(task_type: str, text: str, task_id: str | None = None, **fields: Any) -> Task:
    """`fields` use Habitica's JSON names (`isDue`, `counterUp`, ...)."""
    data: dict[str, Any] = {
        "id": task_id or f"{task_type}-{text.lower().replace(' ', '-')}",
        "type": task_type,
        "text": text,
        "notes": "",
        "value": 0.0,
        "priority": 1,
        "createdAt": CREATED.isoformat(),
        "updatedAt": CREATED.isoformat(),
        "tags": [],
        "checklist": [],
        "history": [],
    }
    if task_type == "daily":
        data.update(
            completed=False,
            isDue=True,
            streak=0,
            frequency="daily",
            everyX=1,
            repeat=dict.fromkeys(("m", "t", "w", "th", "f", "s", "su"), True),
            startDate=CREATED.isoformat(),
        )
    elif task_type == "habit":
        data.update(up=True, down=False, counterUp=0, counterDown=0, frequency="daily")
    elif task_type == "todo":
        data.update(completed=False)
    data.update(fields)
    return task_from_json(data)


def with_fields(task: Task, **fields: Any) -> Task:
    """A copy with JSON fields changed, re-parsed so `raw` and the attributes agree."""
    return task_from_json({**task.raw, **fields})


__all__ = ["make_task", "with_fields", "replace"]
