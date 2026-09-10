import sqlite3
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import engine as engine_module
from app.db.engine import session_scope


async def test_migrations_created_the_database(db_path: Path) -> None:
    assert db_path.exists()

    connection = sqlite3.connect(db_path)
    try:
        stamped = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        connection.close()

    assert stamped == [("0001_baseline",)]


async def test_engine_puts_the_database_in_wal_mode(db_path: Path) -> None:
    async with session_scope() as session:
        journal_mode = await session.scalar(text("PRAGMA journal_mode"))

    assert journal_mode == "wal"


async def test_health_reports_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "db": True}


async def test_health_reports_a_broken_database(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.main import app

    # A directory is not a database file: SQLite cannot open it.
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.parent}")
    get_settings.cache_clear()
    await engine_module.dispose_engine()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as broken_client:
        response = await broken_client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"ok": False, "db": False}
