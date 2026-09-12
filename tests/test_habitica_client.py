import json
import os
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.habitica.client import (
    RATE_LIMIT_FALLBACK_SECONDS,
    RETRY_BACKOFF_SECONDS,
    HabiticaClient,
    HabiticaError,
    parse_rate_limit_reset,
)
from tests.habitica_fixtures import load_envelope

NOW = datetime(2026, 9, 11, 14, 56, 0, tzinfo=UTC)
RESET_HEADER = "Fri Sep 11 2026 14:56:29 GMT+0000 (Coordinated Universal Time)"


class Recorder:
    """Scripted responses, plus a log of requests and of every sleep the client asked for."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        return self.responses.pop(0)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def ok(data: object, remaining: int = 29, reset: str = RESET_HEADER) -> httpx.Response:
    return httpx.Response(
        200,
        json={"success": True, "data": data},
        headers={"X-RateLimit-Remaining": str(remaining), "X-RateLimit-Reset": reset},
    )


def make_client(recorder: Recorder, tmp_path: Path) -> HabiticaClient:
    return HabiticaClient(
        "user-1",
        "token-1",
        transport=httpx.MockTransport(recorder.handle),
        sleep=recorder.sleep,
        clock=lambda: NOW,
        content_cache_path=tmp_path / "content.json",
    )


def test_reset_header_is_a_javascript_date() -> None:
    assert parse_rate_limit_reset(RESET_HEADER) == datetime(2026, 9, 11, 14, 56, 29, tzinfo=UTC)
    assert parse_rate_limit_reset("garbage") is None


async def test_required_headers_are_sent(tmp_path: Path) -> None:
    recorder = Recorder([ok(load_envelope("user")["data"])])

    async with make_client(recorder, tmp_path) as client:
        user = await client.get_user()

    request = recorder.requests[0]
    assert request.headers["x-api-user"] == "user-1"
    assert request.headers["x-api-key"] == "token-1"
    assert request.headers["x-client"] == "user-1-HabiticaAnalytica"
    assert str(request.url) == "https://habitica.com/api/v3/user"
    assert user.day_start == 11


async def test_sleeps_until_reset_when_the_limit_is_exhausted(tmp_path: Path) -> None:
    recorder = Recorder([ok([], remaining=0), ok([])])

    async with make_client(recorder, tmp_path) as client:
        await client.get_tasks()
        assert recorder.sleeps == []
        await client.get_tasks()

    assert recorder.sleeps == [29.0]
    assert len(recorder.requests) == 2


async def test_falls_back_to_a_minute_when_the_reset_header_is_unreadable(
    tmp_path: Path,
) -> None:
    recorder = Recorder([ok([], remaining=0, reset="???"), ok([])])

    async with make_client(recorder, tmp_path) as client:
        await client.get_tasks()
        await client.get_tasks()

    assert recorder.sleeps == [RATE_LIMIT_FALLBACK_SECONDS]


async def test_does_not_sleep_while_requests_remain(tmp_path: Path) -> None:
    recorder = Recorder([ok([], remaining=3), ok([], remaining=2), ok([], remaining=1)])

    async with make_client(recorder, tmp_path) as client:
        for _ in range(3):
            await client.get_tasks()

    assert recorder.sleeps == []


async def test_retries_once_on_5xx(tmp_path: Path) -> None:
    recorder = Recorder([httpx.Response(502, text="bad gateway"), ok([])])

    async with make_client(recorder, tmp_path) as client:
        assert await client.get_tasks() == []

    assert len(recorder.requests) == 2
    assert recorder.sleeps == [RETRY_BACKOFF_SECONDS]


async def test_gives_up_after_the_second_5xx(tmp_path: Path) -> None:
    recorder = Recorder([httpx.Response(503), httpx.Response(503)])

    async with make_client(recorder, tmp_path) as client:
        with pytest.raises(HabiticaError) as error:
            await client.get_tasks()

    assert error.value.status == 503
    assert len(recorder.requests) == 2


async def test_does_not_retry_4xx(tmp_path: Path) -> None:
    body = {"success": False, "error": "NotAuthorized", "message": "There is no account..."}
    recorder = Recorder([httpx.Response(401, json=body)])

    async with make_client(recorder, tmp_path) as client:
        with pytest.raises(HabiticaError, match="401 NotAuthorized") as error:
            await client.get_user()

    assert error.value.message == "There is no account..."
    assert len(recorder.requests) == 1
    assert recorder.sleeps == []


async def test_429_sleeps_for_retry_after_then_retries_once(tmp_path: Path) -> None:
    recorder = Recorder([httpx.Response(429, headers={"Retry-After": "7"}), ok([])])

    async with make_client(recorder, tmp_path) as client:
        assert await client.get_tasks() == []

    assert recorder.sleeps == [7.0]
    assert len(recorder.requests) == 2


async def test_score_task_posts_the_direction(tmp_path: Path) -> None:
    data = {"delta": 1.2, "hp": 49, "mp": 240, "exp": 2950, "gp": 4330.2, "lvl": 120}
    recorder = Recorder([ok(data)])

    async with make_client(recorder, tmp_path) as client:
        result = await client.score_task("task-1", "up")
        with pytest.raises(ValueError):
            await client.score_task("task-1", "sideways")

    request = recorder.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/api/v3/tasks/task-1/score/up"
    assert result.delta == 1.2


async def test_create_and_update_send_the_payload(tmp_path: Path) -> None:
    created = {
        "id": "t-new",
        "type": "habit",
        "text": "Workout",
        "createdAt": "2026-09-11T10:00:00.000Z",
        "updatedAt": "2026-09-11T10:00:00.000Z",
        "up": True,
        "down": False,
    }
    recorder = Recorder([ok(created), ok({**created, "text": "Lift"})])

    async with make_client(recorder, tmp_path) as client:
        task = await client.create_task({"type": "habit", "text": "Workout", "down": False})
        updated = await client.update_task("t-new", {"text": "Lift"})

    assert task.id == "t-new"
    assert updated.text == "Lift"
    post, put = recorder.requests
    assert (post.method, post.url.path) == ("POST", "/api/v3/tasks/user")
    assert json.loads(post.content) == {"type": "habit", "text": "Workout", "down": False}
    assert (put.method, put.url.path) == ("PUT", "/api/v3/tasks/t-new")


async def test_completed_todos_use_the_type_query(tmp_path: Path) -> None:
    recorder = Recorder([ok(load_envelope("completed_todos")["data"])])

    async with make_client(recorder, tmp_path) as client:
        todos = await client.get_completed_todos()

    assert recorder.requests[0].url.params["type"] == "completedTodos"
    assert all(todo.completed for todo in todos)


async def test_content_is_cached_on_disk_for_a_week(tmp_path: Path) -> None:
    content = load_envelope("content")["data"]
    recorder = Recorder([ok(content)])
    cache = tmp_path / "content.json"

    async with make_client(recorder, tmp_path) as client:
        first = await client.get_content()
        second = await client.get_content()

    assert first == second == content
    assert len(recorder.requests) == 1
    assert json.loads(cache.read_text()) == content


async def test_stale_content_cache_is_refetched(tmp_path: Path) -> None:
    content = load_envelope("content")["data"]
    recorder = Recorder([ok(content)])
    cache = tmp_path / "content.json"
    cache.write_text(json.dumps({"gear": {"flat": {}}}))
    eight_days_ago = NOW.timestamp() - 8 * 24 * 3600
    os.utime(cache, (eight_days_ago, eight_days_ago))

    async with make_client(recorder, tmp_path) as client:
        fetched = await client.get_content()

    assert fetched == content
    assert len(recorder.requests) == 1


async def test_webhook_registration_payload(tmp_path: Path) -> None:
    hook = {
        "id": "w1",
        "url": "https://bot.example.com/webhooks/habitica/s",
        "type": "taskActivity",
        "enabled": True,
        "options": {"scored": True},
    }
    recorder = Recorder([ok([hook]), ok(hook)])

    async with make_client(recorder, tmp_path) as client:
        listed = await client.list_webhooks()
        created = await client.create_webhook(hook["url"])

    assert [h.id for h in listed] == ["w1"]
    assert created.url == hook["url"]
    payload = json.loads(recorder.requests[1].content)
    assert payload["type"] == "taskActivity"
    assert payload["options"]["scored"] is True
    assert payload["options"]["created"] is True
