"""Habitica's taskActivity webhooks: register ours, receive them.

    POST /webhooks/habitica/{secret}

The secret in the path is the only authentication Habitica offers. Every
delivery is stored whole in `webhook_deliveries`; the ones we understand
become `task_events` with `source=webhook`.

A payload carries the full task, so it is also written as a `task_snapshots`
row: the next scheduled snapshot then sees no change and does not record
the same completion a second time.
"""

import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.snapshots import (
    load_task_rows,
    new_task_row,
    refresh_task_row,
    task_snapshot,
)
from app.config import get_settings
from app.db.blobs import store_blob
from app.db.engine import session_scope
from app.db.models import TaskEvent, TaskRow, WebhookDelivery
from app.habitica.client import HabiticaClient
from app.habitica.types import Task, task_from_json

logger = logging.getLogger(__name__)

router = APIRouter()

SOURCE = "webhook"
# The path every Analytica receiver has, whatever base URL and secret were in
# front of it when the webhook was last registered.
RECEIVER_PATH = "/webhooks/habitica/"


def receiver_url(public_base_url: str, secret: str) -> str:
    return f"{public_base_url.rstrip('/')}{RECEIVER_PATH}{secret}"


async def ensure_webhook(client: HabiticaClient, url: str) -> str:
    """Make sure Habitica posts task activity to `url`; say what was done.

    Ours is any registered webhook aimed at an Analytica receiver. The exact
    URL, enabled: nothing to do. The exact URL but switched off (Habitica does
    that after repeated failures), or an old base URL or secret: updated in
    place. None at all: created. Returns one line for the log.
    """
    ours = [hook for hook in await client.list_webhooks() if RECEIVER_PATH in hook.url]
    for hook in ours:
        if hook.url == url and hook.enabled:
            return f"Habitica webhook {hook.id} is registered"
        if hook.url == url:
            await client.update_webhook(hook.id, url)
            return f"Habitica webhook {hook.id} was disabled; enabled it again"
    if ours:
        stale = ours[0]
        await client.update_webhook(stale.id, url)
        return f"Habitica webhook {stale.id} moved from {stale.url} to {url}"
    hook = await client.create_webhook(url)
    return f"Habitica webhook {hook.id} created for {url}"


@router.post("/webhooks/habitica/{secret}")
async def receive_habitica_webhook(secret: str, request: Request) -> JSONResponse:
    settings = get_settings()
    if not settings.webhook_secret or secret != settings.webhook_secret:
        logger.warning("habitica webhook with a wrong secret from %s", request.client)
        raise HTTPException(status_code=403, detail="wrong secret")

    try:
        payload = await request.json()
    except ValueError as error:
        raise HTTPException(status_code=400, detail="body is not JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="body is not a JSON object")

    now = datetime.now(UTC)
    async with session_scope() as session:
        events = await store_delivery(session, payload, settings.habitica_user_id, now)
    return JSONResponse({"ok": True, "events": len(events)})


async def store_delivery(
    session: AsyncSession, payload: dict[str, Any], expected_user_id: str, now: datetime
) -> list[TaskEvent]:
    """Store the raw delivery, then whatever events it implies."""
    user_id = str(payload.get("user", {}).get("_id") or "")
    delivery_type = str(payload.get("type") or payload.get("webhookType") or "unknown")
    session.add(
        WebhookDelivery(
            user_id=user_id,
            received_at=now,
            type=delivery_type,
            raw_hash=await store_blob(session, payload),
        )
    )
    if user_id != expected_user_id:
        logger.warning("habitica webhook for another user (%s); stored, not processed", user_id)
        return []
    if payload.get("webhookType") != "taskActivity":
        logger.info("habitica webhook of type %s stored, not processed", delivery_type)
        return []

    events = await task_activity_events(session, payload, user_id, now)
    session.add_all(events)
    await session.flush()
    logger.info("habitica webhook %s: %d events", delivery_type, len(events))
    return events


async def task_activity_events(
    session: AsyncSession, payload: dict[str, Any], user_id: str, now: datetime
) -> list[TaskEvent]:
    task_json = payload.get("task")
    if not isinstance(task_json, dict):
        logger.warning("taskActivity webhook without a task; nothing to record")
        return []
    task = task_from_json(task_json)
    kind = payload.get("type")

    rows = await load_task_rows(session, user_id)
    row = rows.get(task.id)
    if row is None:
        row = new_task_row(task, user_id, now)
        session.add(row)
        await session.flush()
    else:
        refresh_task_row(row, task, now)

    if kind == "deleted":
        row.deleted_at = now
        return [event(row, now, "deleted")]

    session.add(task_snapshot(row, task, user_id, now, await store_blob(session, task.raw)))
    if kind == "scored":
        return [scored_event(row, task, payload, now)]
    if kind == "created":
        return [event(row, task.created_at, "created", raw={"received_at": now.isoformat()})]
    if kind == "updated":
        return [event(row, now, "updated")]
    if kind == "checklistScored":
        item = payload.get("item", {})
        return [
            event(
                row,
                now,
                "checklist",
                raw={"done": task.checklist_done, "total": len(task.checklist), "item": item},
            )
        ]
    logger.warning("taskActivity webhook of unknown type %r", kind)
    return []


def scored_event(row: TaskRow, task: Task, payload: dict[str, Any], now: datetime) -> TaskEvent:
    direction = payload.get("direction")
    delta = payload.get("delta")
    if task.type == "habit":
        return event(row, now, "scored", direction=direction, delta=delta)
    kind = "completed" if direction == "up" else "uncompleted"
    return event(row, now, kind, direction=direction, delta=delta, raw={"streak": task.streak})


def event(row: TaskRow, at: datetime, kind: str, **fields: Any) -> TaskEvent:
    return TaskEvent(user_id=row.user_id, task_id=row.id, at=at, kind=kind, source=SOURCE, **fields)
