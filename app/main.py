"""The single process: a FastAPI app whose lifespan owns everything else.

The lifespan configures logging, starts the collector schedule when Habitica
credentials are present, and disposes the engine at the end. The Telegram
adapter (P02) hangs off the same hook.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.collector import webhooks
from app.config import get_settings
from app.db.engine import dispose_engine, session_scope
from app.habitica.client import HabiticaClient
from app.jobs.scheduler import CollectorJobs, build_collector

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("analytica starting up")
    collector = start_collector_if_configured()
    if collector is not None:
        await collector.start()
    try:
        yield
    finally:
        if collector is not None:
            await collector.stop()
        await dispose_engine()
        logger.info("analytica shut down")


def start_collector_if_configured() -> CollectorJobs | None:
    settings = get_settings()
    if not (settings.habitica_user_id and settings.habitica_api_token):
        logger.warning("Habitica is not configured; the collector is off")
        return None
    client = HabiticaClient(settings.habitica_user_id, settings.habitica_api_token)
    return build_collector(client, settings.habitica_user_id)


app = FastAPI(title="Habitica Analytica", lifespan=lifespan)
app.include_router(webhooks.router)


async def database_reachable() -> bool:
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check could not reach the database")
        return False
    return True


@app.get("/health")
async def health() -> JSONResponse:
    db = await database_reachable()
    return JSONResponse({"ok": db, "db": db}, status_code=200 if db else 503)
