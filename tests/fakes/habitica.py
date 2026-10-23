"""An in-memory stand-in for HabiticaClient with the same method signatures.

Tests set `user` and `tasks` directly and read `calls` to see what the code
under test asked Habitica to do.
"""

import copy
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from app.habitica.types import (
    ScoreResult,
    Task,
    UserStats,
    Webhook,
    task_from_json,
    user_stats_from_json,
)
from tests.habitica_fixtures import load_data


def with_item_ids(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Habitica gives every checklist item an id and a `completed` flag."""
    return [{"id": str(uuid.uuid4()), "completed": False, **item} for item in items]


class FakeHabiticaClient:
    def __init__(
        self,
        user: UserStats | None = None,
        tasks: list[Task] | None = None,
        completed_todos: list[Task] | None = None,
        content: dict[str, Any] | None = None,
    ) -> None:
        self.user = user if user is not None else user_stats_from_json(load_data("user"))
        self.tasks = list(tasks) if tasks is not None else []
        self.completed_todos = list(completed_todos) if completed_todos is not None else []
        self.content = content if content is not None else load_data("content")
        self.webhooks: list[Webhook] = []
        self.calls: list[tuple[str, Any]] = []
        self.now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    # ---- the client's interface ----

    async def get_user(self) -> UserStats:
        self.calls.append(("get_user", None))
        return self.user

    async def get_tasks(self) -> list[Task]:
        self.calls.append(("get_tasks", None))
        return list(self.tasks)

    async def get_completed_todos(self) -> list[Task]:
        self.calls.append(("get_completed_todos", None))
        return list(self.completed_todos)

    async def score_task(self, task_id: str, direction: str) -> ScoreResult:
        self.calls.append(("score_task", (task_id, direction)))
        task = self.find(task_id)
        if task.type in ("daily", "todo"):
            # Re-parsed, so `raw` agrees with the field: the collector stores `raw`.
            self.replace_task(task_from_json({**task.raw, "completed": direction == "up"}))
        return ScoreResult(
            delta=1.0,
            hp=self.user.hp,
            mp=self.user.mp,
            exp=self.user.exp,
            gp=self.user.gp,
            lvl=self.user.lvl,
        )

    async def create_task(self, payload: dict[str, Any]) -> Task:
        self.calls.append(("create_task", payload))
        task = task_from_json(
            {
                "id": str(uuid.uuid4()),
                "createdAt": self.now.isoformat(),
                "updatedAt": self.now.isoformat(),
                "completed": False,
                **payload,
                "checklist": with_item_ids(payload.get("checklist", [])),
            }
        )
        self.tasks.append(task)
        return task

    async def update_task(self, task_id: str, payload: dict[str, Any]) -> Task:
        self.calls.append(("update_task", (task_id, payload)))
        task = self.find(task_id)
        raw = {**copy.deepcopy(task.raw), **payload}
        if "checklist" in payload:
            raw["checklist"] = with_item_ids(payload["checklist"])
        updated = task_from_json(raw)
        self.replace_task(updated)
        return updated

    async def delete_task(self, task_id: str) -> None:
        self.calls.append(("delete_task", task_id))
        self.tasks = [task for task in self.tasks if task.id != task_id]

    async def move_task(self, task_id: str, position: int) -> list[str]:
        self.calls.append(("move_task", (task_id, position)))
        task = self.find(task_id)
        same_type = [t for t in self.tasks if t.type == task.type and t.id != task_id]
        same_type.insert(len(same_type) if position == -1 else position, task)
        others = [t for t in self.tasks if t.type != task.type]
        self.tasks = same_type + others
        return [t.id for t in same_type]

    async def equip(self, key: str) -> None:
        """Puts the item in its slot on the user, as Habitica does (no toggle modelled)."""
        self.calls.append(("equip", key))
        slot = self.content["gear"]["flat"].get(key, {}).get("type")
        if slot is None:
            return
        raw = copy.deepcopy(self.user.raw)
        raw["items"]["gear"]["equipped"][slot] = key
        self.user = user_stats_from_json(raw)

    async def cast_skill(self, key: str, target_id: str | None = None) -> None:
        self.calls.append(("cast_skill", (key, target_id)))

    async def run_cron(self) -> None:
        self.calls.append(("run_cron", None))
        # The one thing cron always does to the user object; tests model damage themselves.
        self.user = user_stats_from_json({**self.user.raw, "needsCron": False})

    async def get_content(self, *, force_refresh: bool = False) -> dict[str, Any]:
        self.calls.append(("get_content", force_refresh))
        return self.content

    async def list_webhooks(self) -> list[Webhook]:
        self.calls.append(("list_webhooks", None))
        return list(self.webhooks)

    async def create_webhook(self, url: str, label: str = "Habitica Analytica") -> Webhook:
        self.calls.append(("create_webhook", url))
        hook = Webhook(id=str(uuid.uuid4()), url=url, type="taskActivity", enabled=True, options={})
        self.webhooks.append(hook)
        return hook

    async def update_webhook(self, webhook_id: str, url: str) -> Webhook:
        self.calls.append(("update_webhook", (webhook_id, url)))
        for index, hook in enumerate(self.webhooks):
            if hook.id == webhook_id:
                updated = replace(hook, url=url, enabled=True)
                self.webhooks[index] = updated
                return updated
        raise KeyError(webhook_id)

    async def aclose(self) -> None:
        pass

    # ---- helpers for tests ----

    def find(self, task_id: str) -> Task:
        for task in self.tasks + self.completed_todos:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def replace_task(self, task: Task) -> None:
        self.tasks = [task if existing.id == task.id else existing for existing in self.tasks]

    def called(self, name: str) -> list[Any]:
        return [args for called, args in self.calls if called == name]
