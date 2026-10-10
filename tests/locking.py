"""Is the database free for another writer while a turn waits on a model?

SQLite takes one writer at a time. A turn that has written and not committed
holds the lock; a second connection that writes meanwhile waits five seconds
and fails with `database is locked` — what the bot did on 2026-10-09.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import session_scope
from app.db.models import Message

USER_ID = "00000000-0000-4000-8000-000000000001"
AT = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)

Handler = Callable[[httpx.Request], httpx.Response]


async def write_in_turn(session: AsyncSession) -> None:
    """What a turn does first: log the inbound message. From here on it holds the lock."""
    session.add(Message(user_id=USER_ID, at=AT, direction="in", kind="text", text="turn"))
    await session.flush()


def writing_elsewhere_first(
    handler: Handler,
) -> Callable[[httpx.Request], Awaitable[httpx.Response]]:
    """A transport handler that, while the request is "in flight", writes from another session."""

    async def handle(request: httpx.Request) -> httpx.Response:
        async with session_scope() as other:
            other.add(Message(user_id=USER_ID, at=AT, direction="in", kind="text", text="other"))
        return handler(request)

    return handle


async def logged_texts() -> list[str]:
    async with session_scope() as session:
        return list(await session.scalars(select(Message.text).order_by(Message.id)))
