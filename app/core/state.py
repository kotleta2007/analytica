"""Per-user conversation state, in the database rather than in bot memory.

One row per user: what the bot is waiting for (`awaiting_confirmation`,
`awaiting_amendment`, a pending destructive action, ...) and the data it needs
to finish that. No row means nothing is pending.
"""

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationState


async def get_state(session: AsyncSession, user_id: str) -> ConversationState | None:
    return await session.get(ConversationState, user_id)


async def set_state(
    session: AsyncSession, user_id: str, state: str, payload: dict[str, Any], now: datetime
) -> ConversationState:
    row = await session.get(ConversationState, user_id)
    if row is None:
        row = ConversationState(user_id=user_id, state=state, payload=payload, updated_at=now)
        session.add(row)
    else:
        row.state = state
        row.payload = payload
        row.updated_at = now
    await session.flush()
    return row


async def clear_state(session: AsyncSession, user_id: str) -> None:
    row = await session.get(ConversationState, user_id)
    if row is not None:
        await session.delete(row)
        await session.flush()
