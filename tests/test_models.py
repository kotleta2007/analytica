import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.blobs import content_hash, store_blob, store_blobs
from app.db.engine import session_scope
from app.db.models import RawBlob, TaskRow, UserSettings, UserSnapshot

USER = "00000000-0000-4000-8000-000000000001"

EXPECTED_TABLES = {
    "raw_blobs",
    "user_snapshots",
    "tasks",
    "task_snapshots",
    "task_events",
    "daily_records",
    "webhook_deliveries",
    "user_settings",
    "conversation_state",
    "messages",
    "telegram_buttons",
    "llm_calls",
    "sessions",
}


async def test_migration_creates_every_telemetry_table(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        connection.close()

    assert {name for (name,) in rows} >= EXPECTED_TABLES


async def test_datetimes_round_trip_as_aware_utc(db_path: Path) -> None:
    zurich = timezone(timedelta(hours=2))
    taken_at = datetime(2026, 9, 11, 13, 0, tzinfo=zurich)

    async with session_scope() as session:
        session.add(
            UserSnapshot(
                user_id=USER,
                taken_at=taken_at,
                hp=49,
                mp=242,
                exp=2931,
                gp=4329,
                lvl=120,
                class_name="wizard",
                to_next_level=4940,
                raw={"stats": {}},
            )
        )

    async with session_scope() as session:
        snapshot = await session.scalar(select(UserSnapshot))

    assert snapshot is not None
    assert snapshot.taken_at == datetime(2026, 9, 11, 11, 0, tzinfo=UTC)
    assert snapshot.taken_at.tzinfo is UTC
    assert snapshot.raw == {"stats": {}}


async def test_naive_datetimes_are_rejected(db_path: Path) -> None:
    now = datetime(2026, 9, 11, 11, 0)
    async with session_scope() as session:
        session.add(
            TaskRow(
                user_id=USER,
                habitica_id="t1",
                type="todo",
                text="x",
                created_at=now,
                first_seen_at=now,
                last_seen_at=now,
                raw={},
            )
        )
        with pytest.raises(Exception, match="naive datetime"):
            await session.flush()
        await session.rollback()


async def test_user_settings_defaults(db_path: Path) -> None:
    async with session_scope() as session:
        session.add(UserSettings(user_id=USER))

    async with session_scope() as session:
        settings = await session.get(UserSettings, USER)

    assert settings is not None
    assert (settings.language, settings.model, settings.tts) == ("en", "strong", "off")
    assert settings.reminder_time == "21:00"
    assert settings.gear_int == {}
    assert settings.dismissed_proposals == []


def test_content_hash_ignores_key_order_and_whitespace() -> None:
    assert content_hash({"a": 1, "b": [1, 2]}) == content_hash({"b": [1, 2], "a": 1})
    assert content_hash({"a": 1}) != content_hash({"a": 2})
    assert len(content_hash({})) == 64


async def test_identical_documents_are_stored_once(db_path: Path) -> None:
    doc = {"id": "t1", "text": "Floss"}
    other = {"id": "t2", "text": "Gym"}

    async with session_scope() as session:
        first = await store_blobs(session, [doc, other, dict(doc)])
    async with session_scope() as session:
        again = await store_blob(session, {"text": "Floss", "id": "t1"})
        blobs = list(await session.scalars(select(RawBlob)))

    assert first[0] == first[2] == again
    assert first[1] != again
    assert len(blobs) == 2
    assert {blob.json["id"] for blob in blobs} == {"t1", "t2"}


async def test_store_blobs_with_nothing_to_store(db_path: Path) -> None:
    async with session_scope() as session:
        assert await store_blobs(session, []) == []
