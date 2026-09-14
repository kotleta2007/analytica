"""Thin async client for the Habitica v3 REST API.

No business logic lives here. The client sends the required headers, respects
the rate limit, retries once on a 5xx, unwraps the `{"success", "data"}`
envelope and hands the result to the parsers in `types.py`.

Rate limit: 30 requests per minute per user. Every response carries
`X-RateLimit-Remaining` and `X-RateLimit-Reset`; when `remaining` reaches
zero the next call sleeps until the reset time. A 429 (the limit was hit
anyway, e.g. from another client on the same account) sleeps for
`Retry-After` and retries once. Other 4xx responses raise immediately.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.habitica.types import (
    ScoreResult,
    Task,
    UserStats,
    Webhook,
    score_result_from_json,
    task_from_json,
    user_stats_from_json,
    webhook_from_json,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://habitica.com/api/v3"
CONTENT_CACHE_PATH = Path("data/habitica_content.json")
CONTENT_MAX_AGE = timedelta(days=7)
RETRY_BACKOFF_SECONDS = 2.0
# Used when the reset header is missing or unreadable: the window is one minute.
RATE_LIMIT_FALLBACK_SECONDS = 60.0

WEBHOOK_TASK_OPTIONS = {
    "created": True,
    "updated": True,
    "deleted": True,
    "scored": True,
    "checklistScored": True,
}


class HabiticaError(RuntimeError):
    def __init__(self, status: int, error: str, message: str) -> None:
        super().__init__(f"Habitica {status} {error}: {message}")
        self.status = status
        self.error = error
        self.message = message


def parse_rate_limit_reset(value: str) -> datetime | None:
    """The header is a JavaScript Date string: `Fri Sep 11 2026 14:56:29 GMT+0000 (UTC)`."""
    head = value.split(" (", 1)[0]
    try:
        return datetime.strptime(head, "%a %b %d %Y %H:%M:%S GMT%z")
    except ValueError:
        return None


class HabiticaClient:
    def __init__(
        self,
        user_id: str,
        api_token: str,
        *,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        content_cache_path: Path = CONTENT_CACHE_PATH,
    ) -> None:
        self._sleep = sleep
        self._clock = clock
        self._content_cache_path = content_cache_path
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset: datetime | None = None
        self._http = httpx.AsyncClient(
            base_url=base_url,
            transport=transport,
            timeout=httpx.Timeout(30.0),
            headers={
                "x-api-user": user_id,
                "x-api-key": api_token,
                "x-client": f"{user_id}-HabiticaAnalytica",
                "content-type": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "HabiticaClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ---- endpoints ----

    async def get_user(self) -> UserStats:
        return user_stats_from_json(await self._request("GET", "/user"))

    async def get_tasks(self) -> list[Task]:
        return [task_from_json(item) for item in await self._request("GET", "/tasks/user")]

    async def get_completed_todos(self) -> list[Task]:
        data = await self._request("GET", "/tasks/user", params={"type": "completedTodos"})
        return [task_from_json(item) for item in data]

    async def score_task(self, task_id: str, direction: str) -> ScoreResult:
        if direction not in ("up", "down"):
            raise ValueError(f"direction must be 'up' or 'down', not {direction!r}")
        data = await self._request("POST", f"/tasks/{task_id}/score/{direction}")
        return score_result_from_json(data)

    async def create_task(self, payload: dict[str, Any]) -> Task:
        """`payload` is the request body Habitica expects: at least `type` and `text`."""
        return task_from_json(await self._request("POST", "/tasks/user", json=payload))

    async def update_task(self, task_id: str, payload: dict[str, Any]) -> Task:
        return task_from_json(await self._request("PUT", f"/tasks/{task_id}", json=payload))

    async def delete_task(self, task_id: str) -> None:
        await self._request("DELETE", f"/tasks/{task_id}")

    async def move_task(self, task_id: str, position: int) -> list[str]:
        """Move a task within its type's list: 0 is the top, -1 the bottom.

        Returns the new order of ids for that list, as Habitica reports it.
        """
        return list(await self._request("POST", f"/tasks/{task_id}/move/to/{position}"))

    async def equip(self, key: str) -> None:
        await self._request("POST", f"/user/equip/equipped/{key}")

    async def run_cron(self) -> None:
        await self._request("POST", "/cron")

    async def get_content(self, *, force_refresh: bool = False) -> dict[str, Any]:
        """Game content (gear stats among a lot else), cached on disk for a week."""
        cached = None if force_refresh else self._read_content_cache()
        if cached is not None:
            return cached
        content = await self._request("GET", "/content")
        self._write_content_cache(content)
        return content

    async def list_webhooks(self) -> list[Webhook]:
        return [webhook_from_json(item) for item in await self._request("GET", "/user/webhook")]

    async def create_webhook(self, url: str, label: str = "Habitica Analytica") -> Webhook:
        payload = {
            "url": url,
            "label": label,
            "type": "taskActivity",
            "enabled": True,
            "options": WEBHOOK_TASK_OPTIONS,
        }
        return webhook_from_json(await self._request("POST", "/user/webhook", json=payload))

    # ---- plumbing ----

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        await self._wait_for_rate_limit()
        logger.debug("habitica %s %s", method, path)
        response = await self._send(method, path, params, json)

        if response.status_code == 429:
            retry_after = float(response.headers.get("Retry-After", RATE_LIMIT_FALLBACK_SECONDS))
            logger.warning(
                "habitica rate limit hit on %s %s; sleeping %ss", method, path, retry_after
            )
            await self._sleep(retry_after)
            response = await self._send(method, path, params, json)
        elif response.status_code >= 500:
            logger.warning(
                "habitica %s %s returned %s; retrying once", method, path, response.status_code
            )
            await self._sleep(RETRY_BACKOFF_SECONDS)
            response = await self._send(method, path, params, json)

        return self._unwrap(response, method, path)

    async def _send(
        self,
        method: str,
        path: str,
        params: dict[str, str] | None,
        json: dict[str, Any] | None,
    ) -> httpx.Response:
        response = await self._http.request(method, path, params=params, json=json)
        self._remember_rate_limit(response)
        return response

    def _remember_rate_limit(self, response: httpx.Response) -> None:
        remaining = response.headers.get("X-RateLimit-Remaining")
        reset = response.headers.get("X-RateLimit-Reset")
        if remaining is None:
            return
        try:
            self._rate_limit_remaining = int(remaining)
        except ValueError:
            return
        self._rate_limit_reset = parse_rate_limit_reset(reset) if reset else None

    async def _wait_for_rate_limit(self) -> None:
        if self._rate_limit_remaining is None or self._rate_limit_remaining > 0:
            return
        if self._rate_limit_reset is None:
            wait = RATE_LIMIT_FALLBACK_SECONDS
        else:
            wait = (self._rate_limit_reset - self._clock()).total_seconds()
        if wait > 0:
            logger.debug("habitica rate limit exhausted; sleeping %.1fs", wait)
            await self._sleep(wait)
        self._rate_limit_remaining = None

    @staticmethod
    def _unwrap(response: httpx.Response, method: str, path: str) -> Any:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400 or not body.get("success", False):
            error = body.get("error", "HTTPError")
            message = body.get("message", response.text[:200])
            logger.warning(
                "habitica %s %s failed: %s %s", method, path, response.status_code, error
            )
            raise HabiticaError(response.status_code, error, message)
        return body.get("data")

    def _read_content_cache(self) -> dict[str, Any] | None:
        path = self._content_cache_path
        if not path.exists():
            return None
        age = self._clock() - datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if age > CONTENT_MAX_AGE:
            return None
        try:
            return json.loads(path.read_text())
        except ValueError:
            logger.warning("habitica content cache at %s is unreadable; refetching", path)
            return None

    def _write_content_cache(self, content: dict[str, Any]) -> None:
        path = self._content_cache_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content))


def client_from_settings() -> HabiticaClient:
    from app.config import get_settings

    settings = get_settings()
    settings.require_habitica()
    return HabiticaClient(settings.habitica_user_id, settings.habitica_api_token)
