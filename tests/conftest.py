"""Shared fixtures.

Every test runs against its own temporary SQLite file with the migrations
applied. Nothing here touches the network.
"""

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config

from alembic import command
from app.config import get_settings
from app.db import engine as engine_module

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _reset_caches() -> None:
    get_settings.cache_clear()


async def _upgrade_to_head() -> None:
    """alembic's env.py owns its event loop, so it runs in a thread of its own."""
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    await asyncio.to_thread(command.upgrade, config, "head")


@pytest.fixture
async def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Path]:
    path = tmp_path / "analytica.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    _reset_caches()
    await engine_module.dispose_engine()

    await _upgrade_to_head()
    yield path

    await engine_module.dispose_engine()
    _reset_caches()


@pytest.fixture
async def client(db_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://test") as http_client,
    ):
        yield http_client
