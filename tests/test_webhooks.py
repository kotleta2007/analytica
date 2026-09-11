from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.collector.snapshots import snapshot_tasks
from app.config import get_settings
from app.db.engine import session_scope
from app.db.models import TaskEvent, TaskRow, TaskSnapshot, WebhookDelivery
from tests.fakes.habitica import FakeHabiticaClient
from tests.task_factory import make_task, with_fields

USER = "00000000-0000-4000-8000-000000000001"
SECRET = "s3cret"


@pytest.fixture
async def webhook_client(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("HABITICA_USER_ID", USER)
    get_settings.cache_clear()
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    get_settings.cache_clear()


def task_activity(kind: str, task_json: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "type": kind,
        "webhookType": "taskActivity",
        "user": {"_id": USER, "_tmp": {}},
        "task": task_json,
        **extra,
    }


async def stored_events() -> list[tuple[str, str | None, float | None, str]]:
    async with session_scope() as session:
        rows = await session.execute(
            select(TaskEvent, TaskRow.text)
            .join(TaskRow, TaskRow.id == TaskEvent.task_id)
            .where(TaskEvent.source == "webhook")
            .order_by(TaskEvent.id)
        )
        return [(e.kind, e.direction, e.delta, text) for e, text in rows]


async def test_wrong_secret_is_forbidden(webhook_client: httpx.AsyncClient) -> None:
    response = await webhook_client.post("/webhooks/habitica/wrong", json={"type": "scored"})

    assert response.status_code == 403
    async with session_scope() as session:
        assert (await session.scalar(select(WebhookDelivery))) is None


async def test_no_configured_secret_forbids_everything(
    webhook_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    get_settings.cache_clear()

    response = await webhook_client.post("/webhooks/habitica/", json={})
    assert response.status_code in (403, 404)
    response = await webhook_client.post(f"/webhooks/habitica/{SECRET}", json={})
    assert response.status_code == 403


async def test_scored_daily_becomes_a_completed_event(webhook_client: httpx.AsyncClient) -> None:
    floss = make_task("daily", "Floss", completed=True, streak=5, value=3.2)
    payload = task_activity("scored", floss.raw, direction="up", delta=0.87)

    response = await webhook_client.post(f"/webhooks/habitica/{SECRET}", json=payload)

    assert response.status_code == 200
    assert response.json() == {"ok": True, "events": 1}
    assert await stored_events() == [("completed", "up", 0.87, "Floss")]
    async with session_scope() as session:
        delivery = await session.scalar(select(WebhookDelivery))
        row = await session.scalar(select(TaskRow))
        snapshot = await session.scalar(select(TaskSnapshot))
        event = await session.scalar(select(TaskEvent))
    assert delivery is not None and delivery.raw == payload and delivery.type == "scored"
    assert delivery.user_id == USER
    assert row is not None and row.habitica_id == floss.id and row.type == "daily"
    assert snapshot is not None and snapshot.completed is True and snapshot.streak == 5
    assert event is not None and event.raw == {"streak": 5}
    assert event.at.tzinfo is UTC


async def test_scored_habit_keeps_the_direction(webhook_client: httpx.AsyncClient) -> None:
    run = make_task("habit", "Run", counterUp=2, value=1.5)
    for direction in ("up", "down"):
        payload = task_activity("scored", run.raw, direction=direction, delta=0.5)
        await webhook_client.post(f"/webhooks/habitica/{SECRET}", json=payload)

    assert await stored_events() == [("scored", "up", 0.5, "Run"), ("scored", "down", 0.5, "Run")]


async def test_created_updated_deleted_and_checklist(webhook_client: httpx.AsyncClient) -> None:
    essay = make_task(
        "todo",
        "Essay",
        checklist=[{"id": "c1", "text": "outline", "completed": False}],
    )
    url = f"/webhooks/habitica/{SECRET}"
    await webhook_client.post(url, json=task_activity("created", essay.raw))
    renamed = with_fields(essay, text="Essay draft")
    await webhook_client.post(url, json=task_activity("updated", renamed.raw))
    ticked = with_fields(renamed, checklist=[{**renamed.raw["checklist"][0], "completed": True}])
    await webhook_client.post(
        url, json=task_activity("checklistScored", ticked.raw, item=ticked.raw["checklist"][0])
    )
    await webhook_client.post(url, json=task_activity("deleted", ticked.raw))

    events = await stored_events()
    assert [kind for kind, *_ in events] == ["created", "updated", "checklist", "deleted"]
    async with session_scope() as session:
        row = await session.scalar(select(TaskRow))
        created = await session.scalar(select(TaskEvent).where(TaskEvent.kind == "created"))
        checklist = await session.scalar(select(TaskEvent).where(TaskEvent.kind == "checklist"))
    assert row is not None and row.text == "Essay draft" and row.deleted_at is not None
    assert created is not None and created.at == essay.created_at
    assert checklist is not None and checklist.raw["done"] == 1 and checklist.raw["total"] == 1


async def test_the_next_snapshot_does_not_repeat_the_webhook_event(
    webhook_client: httpx.AsyncClient,
) -> None:
    floss = make_task("daily", "Floss", completed=False)
    client = FakeHabiticaClient(tasks=[floss])
    t0 = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
    async with session_scope() as session:
        await snapshot_tasks(client, session, USER, t0)

    done = with_fields(floss, completed=True, streak=1, value=1.0)
    payload = task_activity("scored", done.raw, direction="up", delta=1.0)
    await webhook_client.post(f"/webhooks/habitica/{SECRET}", json=payload)

    client.tasks = [done]
    async with session_scope() as session:
        outcome = await snapshot_tasks(client, session, USER, t0.replace(minute=30))

    assert outcome.events == []
    async with session_scope() as session:
        kinds = list(await session.scalars(select(TaskEvent.kind).order_by(TaskEvent.id)))
    assert kinds == ["created", "completed"]


async def test_other_users_and_other_webhook_types_are_stored_only(
    webhook_client: httpx.AsyncClient,
) -> None:
    url = f"/webhooks/habitica/{SECRET}"
    floss = make_task("daily", "Floss")
    stranger = {**task_activity("scored", floss.raw, direction="up"), "user": {"_id": "someone"}}
    quest = {"webhookType": "questActivity", "type": "questStarted", "user": {"_id": USER}}

    first = await webhook_client.post(url, json=stranger)
    second = await webhook_client.post(url, json=quest)

    assert first.json() == {"ok": True, "events": 0}
    assert second.json() == {"ok": True, "events": 0}
    async with session_scope() as session:
        deliveries = list(
            await session.scalars(select(WebhookDelivery).order_by(WebhookDelivery.id))
        )
        events = list(await session.scalars(select(TaskEvent)))
    assert [d.user_id for d in deliveries] == ["someone", USER]
    assert [d.type for d in deliveries] == ["scored", "questStarted"]
    assert events == []


async def test_a_body_that_is_not_json_is_a_400(webhook_client: httpx.AsyncClient) -> None:
    response = await webhook_client.post(
        f"/webhooks/habitica/{SECRET}", content=b"not json", headers={"content-type": "text/plain"}
    )
    assert response.status_code == 400
