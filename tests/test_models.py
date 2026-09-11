import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db.engine import session_scope
from app.db.models import TaskRow, UserSettings, UserSnapshot

USER = "00000000-0000-4000-8000-000000000001"

EXPECTED_TABLES = {
    "user_snapshots",
    "tasks",
    "task_snapshots",
    "task_events",
    "daily_records",
    "webhook_deliveries",
    "user_settings",
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
