from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from app.core.state import clear_state, get_state, set_state
from app.db.engine import session_scope
from app.db.models import Message

USER = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 9, 12, 10, 0, tzinfo=UTC)


async def test_no_state_until_one_is_set(db_path: Path) -> None:
    async with session_scope() as session:
        assert await get_state(session, USER) is None


async def test_set_state_overwrites_the_previous_one(db_path: Path) -> None:
    async with session_scope() as session:
        await set_state(session, USER, "awaiting_confirmation", {"transcript": "hi"}, NOW)
    async with session_scope() as session:
        await set_state(session, USER, "awaiting_amendment", {"transcript": "hi", "n": 2}, NOW)

    async with session_scope() as session:
        state = await get_state(session, USER)

    assert state is not None
    assert state.state == "awaiting_amendment"
    assert state.payload == {"transcript": "hi", "n": 2}
    assert state.updated_at == NOW


async def test_clear_state_removes_the_row_and_tolerates_none(db_path: Path) -> None:
    async with session_scope() as session:
        await set_state(session, USER, "awaiting_confirmation", {}, NOW)
        await clear_state(session, USER)
        await clear_state(session, USER)

    async with session_scope() as session:
        assert await get_state(session, USER) is None


async def test_messages_keep_both_directions_in_order(db_path: Path) -> None:
    async with session_scope() as session:
        session.add(Message(user_id=USER, at=NOW, direction="in", kind="command", text="/stats"))
        session.add(Message(user_id=USER, at=NOW, direction="out", kind="text", text="Lv 120"))

    async with session_scope() as session:
        rows = list(await session.scalars(select(Message).order_by(Message.id)))

    assert [(row.direction, row.kind, row.text) for row in rows] == [
        ("in", "command", "/stats"),
        ("out", "text", "Lv 120"),
    ]
